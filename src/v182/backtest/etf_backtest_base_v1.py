from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from v182.io.frames import load_master
from v182.reporting.waves import resolve_etf_tickers

ROOT = Path(__file__).resolve().parents[3]
REQUIRED_PRICE_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]


@dataclass
class InstrumentQuality:
    isin: str
    ticker: str
    rows: int
    first_date: str | None
    last_date: str | None
    close_coverage_pct: float
    volume_coverage_pct: float
    zero_volume_pct: float
    duplicate_dates: int
    nonpositive_close_rows: int
    negative_volume_rows: int
    ohlc_invariant_violations: int
    max_business_day_gap: int
    mt_756_sessions_available: bool
    quality_pass: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalise_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date"] + REQUIRED_PRICE_COLUMNS + ["dividends", "stock_splits"])
    out = frame.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index()
    rename = {}
    for c in out.columns:
        key = str(c).strip().lower().replace(" ", "_")
        if key in {"date", "datetime"}:
            rename[c] = "date"
        elif key == "adj_close":
            rename[c] = "adj_close"
        elif key in {"open", "high", "low", "close", "volume", "dividends", "stock_splits"}:
            rename[c] = key
    out = out.rename(columns=rename)
    if "date" not in out.columns:
        raise ValueError("ETF_BACKTEST_HISTORY_DATE_MISSING")
    out["date"] = pd.to_datetime(out["date"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
    out = out.dropna(subset=["date"]).sort_values("date")
    out = out.drop_duplicates("date", keep="last")
    for col in REQUIRED_PRICE_COLUMNS + ["dividends", "stock_splits"]:
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out[["date"] + REQUIRED_PRICE_COLUMNS + ["dividends", "stock_splits"]]


def audit_history(isin: str, ticker: str, frame: pd.DataFrame, cfg: dict) -> InstrumentQuality:
    out = _normalise_history(frame)
    rows = len(out)
    dupes = int(out["date"].duplicated().sum())
    close = pd.to_numeric(out["close"], errors="coerce")
    volume = pd.to_numeric(out["volume"], errors="coerce")
    valid_close = close.notna()
    valid_volume = volume.notna()
    close_cov = 100.0 * float(valid_close.mean()) if rows else 0.0
    volume_cov = 100.0 * float(valid_volume.mean()) if rows else 0.0
    zero_volume = 100.0 * float((volume.fillna(-1) == 0).mean()) if rows else 0.0
    nonpositive = int(((close <= 0) & valid_close).sum())
    negative_volume = int(((volume < 0) & valid_volume).sum())

    o = pd.to_numeric(out["open"], errors="coerce")
    h = pd.to_numeric(out["high"], errors="coerce")
    l = pd.to_numeric(out["low"], errors="coerce")
    c = close
    comparable = o.notna() & h.notna() & l.notna() & c.notna()
    invariant_bad = comparable & ((h < l) | (h < o) | (h < c) | (l > o) | (l > c))
    ohlc_bad = int(invariant_bad.sum())

    max_gap = 0
    dates = pd.DatetimeIndex(out["date"].dropna().unique()).sort_values()
    if len(dates) > 1:
        for prev, cur in zip(dates[:-1], dates[1:]):
            gap = max(0, len(pd.bdate_range(prev, cur)) - 2)
            max_gap = max(max_gap, gap)

    gates = cfg["quality_gates"]
    passed = (
        close_cov >= float(gates["minimum_price_coverage_pct"])
        and volume_cov >= float(gates["minimum_volume_coverage_pct"])
        and dupes <= int(gates["maximum_duplicate_dates_per_instrument"])
        and nonpositive <= int(gates["maximum_nonpositive_close_rows"])
        and negative_volume <= int(gates["maximum_negative_volume_rows"])
        and ohlc_bad <= int(gates["maximum_ohlc_invariant_violations"])
        and max_gap <= int(gates["maximum_internal_gap_business_days"])
    )
    return InstrumentQuality(
        isin=str(isin), ticker=str(ticker), rows=rows,
        first_date=None if not rows else str(out["date"].iloc[0].date()),
        last_date=None if not rows else str(out["date"].iloc[-1].date()),
        close_coverage_pct=round(close_cov, 4), volume_coverage_pct=round(volume_cov, 4),
        zero_volume_pct=round(zero_volume, 4), duplicate_dates=dupes,
        nonpositive_close_rows=nonpositive, negative_volume_rows=negative_volume,
        ohlc_invariant_violations=ohlc_bad, max_business_day_gap=max_gap,
        mt_756_sessions_available=rows >= int(gates["minimum_history_sessions_for_mt"]),
        quality_pass=bool(passed),
    )


def _download_one(ticker: str, start: str) -> pd.DataFrame:
    import yfinance as yf

    frame = yf.Ticker(ticker).history(
        start=start,
        interval="1d",
        auto_adjust=False,
        actions=True,
        repair=True,
        raise_errors=False,
    )
    return _normalise_history(frame)


def _membership_template(valid: pd.DataFrame, output_path: Path) -> pd.DataFrame:
    cols = [c for c in ["isin", "name", "yahoo_ticker"] if c in valid.columns]
    membership = valid[cols].copy()
    membership["membership_start"] = ""
    membership["membership_end"] = ""
    membership["pea_eligibility_start"] = ""
    membership["pea_eligibility_end"] = ""
    membership["membership_source"] = "CURRENT_MASTER_ONLY"
    membership["pit_status"] = "UNKNOWN_RESEARCH_ONLY"
    membership["promotion_eligible"] = False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    membership.to_csv(output_path, sep=";", index=False, encoding="utf-8-sig")
    return membership


def _read_existing_prices(prices_dir: Path, isin: str) -> pd.DataFrame:
    path = prices_dir / f"{isin}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return _normalise_history(pd.read_parquet(path))


def _merge_append_only(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        return _normalise_history(fresh)
    if fresh is None or fresh.empty:
        return _normalise_history(existing)
    a = _normalise_history(existing).set_index("date")
    b = _normalise_history(fresh).set_index("date")
    common = a.index.intersection(b.index)
    if len(common):
        # Preserve the first stored raw observation. Revisions are auditable instead of silently rewriting history.
        b = b.loc[~b.index.isin(common)]
    return pd.concat([a, b]).sort_index().reset_index()


def build(root: Path = ROOT, *, refresh: bool = True, limit: int | None = None) -> dict:
    cfg_path = root / "config" / "ETF_BACKTEST_BASE_V1.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    out_root = root / cfg["outputs"]["root"]
    prices_dir = out_root / cfg["outputs"]["prices"]
    actions_dir = out_root / cfg["outputs"]["corporate_actions"]
    prices_dir.mkdir(parents=True, exist_ok=True)
    actions_dir.mkdir(parents=True, exist_ok=True)

    master = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    mapped, gaps = resolve_etf_tickers(master, root / "config" / "V18.2_ETF_TICKER_MAP.csv")
    valid = mapped.dropna(subset=["isin", "yahoo_ticker"]).copy()
    valid = valid[valid["yahoo_ticker"].astype(str).str.strip().ne("")].drop_duplicates("isin")
    if limit:
        valid = valid.head(int(limit))

    membership_path = out_root / cfg["outputs"]["pit_membership_template"]
    membership = _membership_template(valid, membership_path)

    qualities: list[InstrumentQuality] = []
    failures: list[dict] = []
    for row in valid.itertuples(index=False):
        isin = str(row.isin)
        ticker = str(row.yahoo_ticker)
        existing = _read_existing_prices(prices_dir, isin)
        fresh = pd.DataFrame()
        if refresh:
            try:
                fresh = _download_one(ticker, cfg["history"]["start"])
            except Exception as exc:
                failures.append({"isin": isin, "ticker": ticker, "error": f"{type(exc).__name__}:{exc}"})
        merged = _merge_append_only(existing, fresh)
        if merged.empty:
            qualities.append(audit_history(isin, ticker, merged, cfg))
            continue
        prices = merged[["date"] + REQUIRED_PRICE_COLUMNS].copy()
        corporate = merged[["date", "dividends", "stock_splits"]].copy()
        prices.to_parquet(prices_dir / f"{isin}.parquet", index=False)
        corporate.to_parquet(actions_dir / f"{isin}.parquet", index=False)
        qualities.append(audit_history(isin, ticker, merged, cfg))

    quality_df = pd.DataFrame([asdict(x) for x in qualities])
    quality_path = out_root / cfg["outputs"]["instrument_quality"]
    quality_df.to_csv(quality_path, sep=";", index=False, encoding="utf-8-sig")

    promotion_ready_membership = bool(
        len(membership)
        and membership["membership_start"].astype(str).str.strip().ne("").all()
        and membership["pea_eligibility_start"].astype(str).str.strip().ne("").all()
    )
    promotion_eligible = bool(
        promotion_ready_membership
        and len(quality_df)
        and quality_df["quality_pass"].fillna(False).all()
        and not failures
    )
    manifest = {
        "version": cfg["version"], "generated_at_utc": _utc_now(),
        "source": cfg["source"], "history_policy": cfg["history"],
        "requested_instruments": int(len(valid)), "ticker_mapping_gaps": int(len(gaps)),
        "built_instruments": int((quality_df["rows"] > 0).sum()) if len(quality_df) else 0,
        "quality_pass_instruments": int(quality_df["quality_pass"].sum()) if len(quality_df) else 0,
        "mt_756_sessions_instruments": int(quality_df["mt_756_sessions_available"].sum()) if len(quality_df) else 0,
        "download_failures": failures,
        "pit_membership_complete": promotion_ready_membership,
        "promotion_eligible": promotion_eligible,
        "promotion_block_reason": None if promotion_eligible else "PIT_MEMBERSHIP_OR_DATA_QUALITY_INCOMPLETE",
        "current_universe_reconstruction_promotion_eligible": False,
        "files": {
            "quality": {"path": str(quality_path.relative_to(root)), "sha256": _sha256(quality_path)},
            "membership": {"path": str(membership_path.relative_to(root)), "sha256": _sha256(membership_path)},
        },
        "governance": cfg["governance"],
    }
    manifest_path = out_root / cfg["outputs"]["manifest"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/audit immutable ETF backtest data foundation")
    parser.add_argument("--no-refresh", action="store_true", help="Audit/rebuild outputs from local stored history only")
    parser.add_argument("--limit", type=int, default=None, help="Limit instruments for smoke tests")
    args = parser.parse_args(list(argv) if argv is not None else None)
    manifest = build(refresh=not args.no_refresh, limit=args.limit)
    print(json.dumps({k: manifest[k] for k in ["requested_instruments", "built_instruments", "quality_pass_instruments", "mt_756_sessions_instruments", "pit_membership_complete", "promotion_eligible"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
