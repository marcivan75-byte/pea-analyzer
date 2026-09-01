from __future__ import annotations

# Isolated source-feasibility probe; intentionally never used for model tuning.
import argparse
import json
import time
from pathlib import Path

import pandas as pd

START = "2010-01-01"
END_EXCLUSIVE = "2023-01-01"
MAX_ALLOWED_DATE = pd.Timestamp("2022-12-31")


def _load_current_cache_tickers(cache_root: Path) -> list[str]:
    # Identity bootstrap only. These 2023+ cache tickers are NOT a historical
    # survivorship-safe universe and MUST NOT be used to tune/select a model.
    from v182.hebdo.tabport_historical import _read_ohlc_source

    frame, _ = _read_ohlc_source(cache_root)
    if frame.empty or "ticker" not in frame.columns:
        raise SystemExit("BLOCK_YAHOO_PROBE_UNIVERSE: governed cache has no ticker identities")
    return sorted({str(x).strip() for x in frame["ticker"].dropna() if str(x).strip()})


def _extract_ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        for level in range(raw.columns.nlevels):
            values = {str(v) for v in raw.columns.get_level_values(level)}
            if ticker in values:
                try:
                    out = raw.xs(ticker, axis=1, level=level, drop_level=True).copy()
                except Exception:
                    return pd.DataFrame()
                return out.dropna(how="all")
        return pd.DataFrame()
    return raw.copy().dropna(how="all")


def _bar_quality(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "rows": 0,
            "min_date": None,
            "max_date": None,
            "duplicate_dates": 0,
            "invalid_ohlc": 0,
            "negative_volume": 0,
            "has_adj_close": False,
        }
    out = frame.copy()
    idx = pd.to_datetime(out.index, errors="coerce")
    valid = ~idx.isna()
    out = out.loc[valid].copy()
    idx = pd.DatetimeIndex(idx[valid])
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx
    out = out.sort_index()
    if len(out) and out.index.max() > MAX_ALLOWED_DATE:
        raise SystemExit(f"BLOCK_YAHOO_HOLDOUT_LEAK: max_date={out.index.max()}")

    lower = {str(c).strip().lower(): c for c in out.columns}
    invalid_ohlc = 0
    if all(k in lower for k in ("open", "high", "low", "close")):
        o = pd.to_numeric(out[lower["open"]], errors="coerce")
        h = pd.to_numeric(out[lower["high"]], errors="coerce")
        l = pd.to_numeric(out[lower["low"]], errors="coerce")
        c = pd.to_numeric(out[lower["close"]], errors="coerce")
        observed = o.notna() & h.notna() & l.notna() & c.notna()
        invalid = observed & ((h < l) | (h < o) | (h < c) | (l > o) | (l > c) | (o <= 0) | (h <= 0) | (l <= 0) | (c <= 0))
        invalid_ohlc = int(invalid.sum())

    negative_volume = 0
    if "volume" in lower:
        v = pd.to_numeric(out[lower["volume"]], errors="coerce")
        negative_volume = int((v < 0).sum())

    return {
        "rows": int(len(out)),
        "min_date": str(out.index.min().date()) if len(out) else None,
        "max_date": str(out.index.max().date()) if len(out) else None,
        "duplicate_dates": int(out.index.duplicated().sum()),
        "invalid_ohlc": invalid_ohlc,
        "negative_volume": negative_volume,
        "has_adj_close": "adj close" in lower,
    }


def run(cache_root: Path, output_dir: Path, sample_size: int, batch_size: int, pause_seconds: float) -> dict:
    import yfinance as yf

    tickers = _load_current_cache_tickers(cache_root)
    if not tickers:
        raise SystemExit("BLOCK_YAHOO_PROBE_UNIVERSE: no tickers")
    sample = tickers[: min(int(sample_size), len(tickers))]
    rows: list[dict] = []

    for start in range(0, len(sample), int(batch_size)):
        batch = sample[start : start + int(batch_size)]
        try:
            raw = yf.download(
                tickers=batch,
                start=START,
                end=END_EXCLUSIVE,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=True,
                threads=True,
                progress=False,
                timeout=20,
            )
        except Exception as exc:
            for ticker in batch:
                rows.append({"ticker": ticker, "status": "REQUEST_ERROR", "detail": f"{type(exc).__name__}:{str(exc)[:120]}"})
            time.sleep(max(0.0, pause_seconds))
            continue

        for ticker in batch:
            frame = _extract_ticker_frame(raw, ticker)
            quality = _bar_quality(frame)
            status = "OK" if quality["rows"] > 0 else "NO_HISTORY"
            rows.append({"ticker": ticker, "status": status, **quality})
        time.sleep(max(0.0, pause_seconds))

    inventory = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory.to_csv(output_dir / "PRE2023_YAHOO_PROBE_INVENTORY.csv", index=False)

    ok = inventory[inventory["status"].eq("OK")].copy() if not inventory.empty else pd.DataFrame()
    summary = {
        "purpose": "SOURCE_FEASIBILITY_ONLY",
        "historical_universe_certified": False,
        "survivorship_safe": False,
        "retuning": False,
        "holdout_accessed_for_prices": False,
        "identity_bootstrap": "CURRENT_GOVERNED_CACHE_TICKERS_ONLY",
        "requested_start": START,
        "requested_end_exclusive": END_EXCLUSIVE,
        "available_current_cache_tickers": len(tickers),
        "sample_tickers": len(sample),
        "ok_tickers": int(len(ok)),
        "no_history_tickers": int((inventory["status"] == "NO_HISTORY").sum()) if not inventory.empty else 0,
        "request_error_tickers": int((inventory["status"] == "REQUEST_ERROR").sum()) if not inventory.empty else 0,
        "coverage_pct": round(100.0 * len(ok) / len(sample), 2) if sample else 0.0,
        "min_observed_date": min((x for x in ok.get("min_date", pd.Series(dtype=str)).dropna().astype(str)), default=None),
        "max_observed_date": max((x for x in ok.get("max_date", pd.Series(dtype=str)).dropna().astype(str)), default=None),
        "duplicate_dates": int(pd.to_numeric(ok.get("duplicate_dates", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "invalid_ohlc": int(pd.to_numeric(ok.get("invalid_ohlc", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "negative_volume": int(pd.to_numeric(ok.get("negative_volume", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "adj_close_tickers": int(ok.get("has_adj_close", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if len(ok) else 0,
    }
    (output_dir / "PRE2023_YAHOO_PROBE_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if summary["max_observed_date"] and pd.Timestamp(summary["max_observed_date"]) > MAX_ALLOWED_DATE:
        raise SystemExit("BLOCK_YAHOO_HOLDOUT_LEAK")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", default="data/cache/actions")
    parser.add_argument("--output-dir", default="outputs/pre2023_yahoo_probe")
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--pause-seconds", type=float, default=0.5)
    args = parser.parse_args()
    if args.sample_size <= 0 or args.batch_size <= 0:
        raise SystemExit("INVALID_YAHOO_PROBE_SIZE")
    run(Path(args.cache_root), Path(args.output_dir), args.sample_size, args.batch_size, args.pause_seconds)


if __name__ == "__main__":
    main()
