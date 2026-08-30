from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

MIN_ADV_20_EUR = 800_000.0


class LiquidityPITBlocked(RuntimeError):
    pass


def _read(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise LiquidityPITBlocked(f"BLOCK_DATA_LIQUIDITY: missing input {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise LiquidityPITBlocked(f"BLOCK_DATA_LIQUIDITY: unsupported format {path.suffix}")


def compute_daily_liquidity(ohlcv: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "date", "close", "volume"}
    missing = required.difference(ohlcv.columns)
    if missing:
        raise LiquidityPITBlocked(f"BLOCK_DATA_LIQUIDITY: OHLCV columns missing {sorted(missing)}")

    frame = ohlcv.loc[:, ["ticker", "date", "close", "volume"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "date"]).sort_values(["ticker", "date"])

    # Strict PIT convention: today's liquidity feature uses only the 20 completed
    # sessions preceding the signal session. The current-session close is used only
    # to express the already-known historical average volume in EUR.
    frame["volume_avg20"] = (
        frame.groupby("ticker", sort=False)["volume"]
        .transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    )
    frame["adv_20_eur"] = frame["volume_avg20"] * frame["close"]
    return frame[["ticker", "date", "volume_avg20", "adv_20_eur"]]


def enrich_liquidity_pit(
    pit: pd.DataFrame,
    ohlcv: pd.DataFrame,
    *,
    min_adv_eur: float = MIN_ADV_20_EUR,
) -> pd.DataFrame:
    required = {"ticker", "market_data_date"}
    missing = required.difference(pit.columns)
    if missing:
        raise LiquidityPITBlocked(f"BLOCK_DATA_LIQUIDITY: PIT columns missing {sorted(missing)}")
    if not np.isfinite(min_adv_eur) or min_adv_eur <= 0:
        raise ValueError("min_adv_eur must be positive")

    base = pit.copy()
    base["market_data_date"] = pd.to_datetime(base["market_data_date"], errors="coerce")
    if base["market_data_date"].isna().any():
        raise LiquidityPITBlocked("BLOCK_DATA_LIQUIDITY: invalid market_data_date")

    daily = compute_daily_liquidity(ohlcv).rename(columns={"date": "market_data_date"})
    enriched = base.drop(columns=[c for c in ("volume_avg20", "adv_20_eur", "liquidity_status") if c in base.columns]).merge(
        daily,
        on=["ticker", "market_data_date"],
        how="left",
        validate="many_to_one",
    )

    adv = pd.to_numeric(enriched["adv_20_eur"], errors="coerce")
    status = np.where(
        adv.isna() | ~np.isfinite(adv),
        "BLOCK_DATA_LIQUIDITY",
        np.where(adv < float(min_adv_eur), "BLOCK_ILLIQUID", "ELIGIBLE"),
    )
    enriched["liquidity_status"] = status
    return enriched


def liquidity_report(frame: pd.DataFrame, *, min_adv_eur: float = MIN_ADV_20_EUR) -> dict[str, object]:
    status = frame["liquidity_status"].astype(str)
    valid = status.ne("BLOCK_DATA_LIQUIDITY")
    years = pd.to_datetime(frame.get("signal_date", frame["market_data_date"]), errors="coerce").dt.year
    by_year: dict[str, dict[str, float | int]] = {}
    for year in sorted(y for y in years.dropna().unique()):
        mask = years.eq(year)
        total = int(mask.sum())
        eligible = int((mask & status.eq("ELIGIBLE")).sum())
        known = int((mask & valid).sum())
        by_year[str(int(year))] = {
            "rows": total,
            "known_liquidity_rows": known,
            "eligible_rows": eligible,
            "known_coverage": known / total if total else 0.0,
            "eligible_share": eligible / total if total else 0.0,
        }
    return {
        "min_adv_20_eur": float(min_adv_eur),
        "rows": int(len(frame)),
        "known_liquidity_rows": int(valid.sum()),
        "known_liquidity_coverage": float(valid.mean()) if len(frame) else 0.0,
        "eligible_rows": int(status.eq("ELIGIBLE").sum()),
        "blocked_illiquid_rows": int(status.eq("BLOCK_ILLIQUID").sum()),
        "blocked_data_rows": int(status.eq("BLOCK_DATA_LIQUIDITY").sum()),
        "by_year": by_year,
        "governance": {
            "volume_window": "20 completed sessions before market_data_date",
            "adv_formula": "volume_avg20 * signal-session close",
            "fail_closed_on_missing": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pit", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--min-adv-eur", type=float, default=MIN_ADV_20_EUR)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--liquid-output", type=Path, default=None)
    args = parser.parse_args()

    try:
        pit = _read(args.pit)
        ohlcv = _read(args.ohlcv)
        enriched = enrich_liquidity_pit(pit, ohlcv, min_adv_eur=args.min_adv_eur)
    except LiquidityPITBlocked as exc:
        raise SystemExit(str(exc)) from exc

    if args.pit.suffix.lower() != ".parquet":
        raise SystemExit("BLOCK_DATA_LIQUIDITY: canonical PIT must be parquet")
    enriched.to_parquet(args.pit, index=False)

    liquid_path = args.liquid_output or args.pit.with_name(args.pit.stem + "_LIQUID.parquet")
    liquid = enriched[enriched["liquidity_status"].eq("ELIGIBLE")].copy()
    liquid.to_parquet(liquid_path, index=False)

    report = liquidity_report(enriched, min_adv_eur=args.min_adv_eur)
    report_path = args.report or args.pit.with_name("V22_1_LIQUIDITY_PIT_REPORT.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["known_liquidity_coverage"] >= 0.90 else 2


if __name__ == "__main__":
    raise SystemExit(main())
