from __future__ import annotations

"""Build the isolated 2010-2022 Yahoo development-price corpus.

This builder intentionally bootstraps identities from the governed current action
cache.  It therefore does NOT certify a survivorship-safe historical universe or
historical PEA eligibility.  Prices are downloaded raw (auto_adjust=False,
repair=False). Invalid rows are excluded fail-closed; they are never repaired or
numerically tolerated.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

START = "2010-01-01"
END_EXCLUSIVE = "2023-01-01"
MAX_ALLOWED_DATE = pd.Timestamp("2022-12-31")
REQUIRED_PRICE_COLUMNS = ("open", "high", "low", "close", "adj close", "volume")


def _load_current_cache_tickers(cache_root: Path) -> list[str]:
    from v182.hebdo.tabport_historical import _read_ohlc_source

    frame, _ = _read_ohlc_source(cache_root)
    if frame.empty or "ticker" not in frame.columns:
        raise SystemExit("BLOCK_PRE2023_CORPUS_UNIVERSE: governed action cache has no ticker identities")
    tickers = sorted({str(x).strip() for x in frame["ticker"].dropna() if str(x).strip()})
    if not tickers:
        raise SystemExit("BLOCK_PRE2023_CORPUS_UNIVERSE: no ticker identities")
    return tickers


def _extract_ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        for level in range(raw.columns.nlevels):
            values = {str(v) for v in raw.columns.get_level_values(level)}
            if ticker in values:
                try:
                    return raw.xs(ticker, axis=1, level=level, drop_level=True).copy().dropna(how="all")
                except Exception:
                    return pd.DataFrame()
        return pd.DataFrame()
    return raw.copy().dropna(how="all")


def _normalise_and_filter(frame: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame(), {"raw_rows": 0, "kept_rows": 0, "excluded_rows": 0}

    work = frame.copy()
    idx = pd.to_datetime(work.index, errors="coerce")
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        idx = idx.tz_localize(None)
    work.index = idx
    work.index.name = "date"
    work = work.sort_index()

    lower = {str(c).strip().lower(): c for c in work.columns}
    missing = [c for c in REQUIRED_PRICE_COLUMNS if c not in lower]
    if missing:
        raise ValueError(f"MISSING_REQUIRED_COLUMNS:{ticker}:{','.join(missing)}")

    out = pd.DataFrame(index=work.index)
    out["open"] = pd.to_numeric(work[lower["open"]], errors="coerce")
    out["high"] = pd.to_numeric(work[lower["high"]], errors="coerce")
    out["low"] = pd.to_numeric(work[lower["low"]], errors="coerce")
    out["close"] = pd.to_numeric(work[lower["close"]], errors="coerce")
    out["adj_close"] = pd.to_numeric(work[lower["adj close"]], errors="coerce")
    out["volume"] = pd.to_numeric(work[lower["volume"]], errors="coerce")
    out.insert(0, "ticker", ticker)

    reasons = pd.Series("", index=out.index, dtype="object")

    def add_reason(mask: pd.Series | np.ndarray, label: str) -> None:
        nonlocal reasons
        mask = pd.Series(mask, index=out.index).fillna(False).astype(bool)
        reasons.loc[mask] = reasons.loc[mask].where(reasons.loc[mask].eq(""), reasons.loc[mask] + ";") + label

    add_reason(out.index.isna(), "INVALID_DATE")
    add_reason((out.index < pd.Timestamp(START)) | (out.index > MAX_ALLOWED_DATE), "OUT_OF_RANGE")
    duplicated = pd.Series(out.index.duplicated(keep=False), index=out.index)
    add_reason(duplicated, "DUPLICATE_DATE")

    price_cols = ["open", "high", "low", "close", "adj_close"]
    finite_prices = np.isfinite(out[price_cols]).all(axis=1)
    add_reason(~finite_prices, "MISSING_OR_NONFINITE_PRICE")
    add_reason(~np.isfinite(out["volume"]), "MISSING_OR_NONFINITE_VOLUME")
    add_reason((out[price_cols] <= 0).any(axis=1), "NONPOSITIVE_PRICE")
    add_reason(out["volume"] < 0, "NEGATIVE_VOLUME")

    geometry = (
        (out["high"] < out["low"])
        | (out["high"] < out["open"])
        | (out["high"] < out["close"])
        | (out["low"] > out["open"])
        | (out["low"] > out["close"])
    )
    add_reason(geometry, "INVALID_OHLC_GEOMETRY")

    bad = reasons.ne("")
    excluded = out.loc[bad].copy()
    if len(excluded):
        excluded["reason"] = reasons.loc[bad].values
        excluded = excluded.reset_index()
    kept = out.loc[~bad].copy().reset_index()

    if len(kept):
        if pd.to_datetime(kept["date"]).max() > MAX_ALLOWED_DATE:
            raise SystemExit(f"BLOCK_PRE2023_HOLDOUT_LEAK:{ticker}")
        if kept.duplicated(["ticker", "date"]).any():
            raise SystemExit(f"BLOCK_PRE2023_DUPLICATE_AFTER_FILTER:{ticker}")

    stats = {
        "raw_rows": int(len(out)),
        "kept_rows": int(len(kept)),
        "excluded_rows": int(len(excluded)),
        "invalid_geometry_rows": int(geometry.fillna(False).sum()),
        "min_date": str(pd.to_datetime(kept["date"]).min().date()) if len(kept) else None,
        "max_date": str(pd.to_datetime(kept["date"]).max().date()) if len(kept) else None,
    }
    return kept, excluded, stats


def run(cache_root: Path, output_dir: Path, batch_size: int, pause_seconds: float) -> dict:
    import yfinance as yf

    tickers = _load_current_cache_tickers(cache_root)
    bars: list[pd.DataFrame] = []
    exclusions: list[pd.DataFrame] = []
    inventory: list[dict] = []

    for start in range(0, len(tickers), int(batch_size)):
        batch = tickers[start : start + int(batch_size)]
        print(f"YAHOO_BATCH start={start} count={len(batch)} total={len(tickers)}", flush=True)
        try:
            raw = yf.download(
                tickers=batch,
                start=START,
                end=END_EXCLUSIVE,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                repair=False,
                actions=False,
                threads=True,
                progress=False,
                timeout=30,
            )
        except Exception as exc:
            for ticker in batch:
                inventory.append({"ticker": ticker, "status": "REQUEST_ERROR", "detail": f"{type(exc).__name__}:{str(exc)[:160]}"})
            time.sleep(max(0.0, pause_seconds))
            continue

        for ticker in batch:
            frame = _extract_ticker_frame(raw, ticker)
            if frame.empty:
                inventory.append({"ticker": ticker, "status": "NO_HISTORY", "raw_rows": 0, "kept_rows": 0, "excluded_rows": 0})
                continue
            try:
                kept, rejected, stats = _normalise_and_filter(frame, ticker)
            except Exception as exc:
                inventory.append({"ticker": ticker, "status": "QUALITY_ERROR", "detail": f"{type(exc).__name__}:{str(exc)[:160]}"})
                continue
            if len(kept):
                bars.append(kept)
            if len(rejected):
                exclusions.append(rejected)
            inventory.append({"ticker": ticker, "status": "OK" if len(kept) else "ALL_ROWS_EXCLUDED", **stats})
        time.sleep(max(0.0, pause_seconds))

    output_dir.mkdir(parents=True, exist_ok=True)
    corpus = pd.concat(bars, ignore_index=True) if bars else pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"])
    rejected = pd.concat(exclusions, ignore_index=True) if exclusions else pd.DataFrame(columns=["date", "ticker", "reason"])
    inv = pd.DataFrame(inventory).sort_values("ticker").reset_index(drop=True)

    if len(corpus):
        corpus["date"] = pd.to_datetime(corpus["date"], errors="raise")
        corpus = corpus.sort_values(["ticker", "date"]).reset_index(drop=True)
        if corpus["date"].max() > MAX_ALLOWED_DATE:
            raise SystemExit("BLOCK_PRE2023_HOLDOUT_LEAK")
        if corpus.duplicated(["ticker", "date"]).any():
            raise SystemExit("BLOCK_PRE2023_DUPLICATE_CORPUS")
        o, h, l, c = (corpus[x] for x in ("open", "high", "low", "close"))
        if ((h < l) | (h < o) | (h < c) | (l > o) | (l > c)).any():
            raise SystemExit("BLOCK_PRE2023_INVALID_OHLC_AFTER_FILTER")
        if not np.isfinite(corpus[["open", "high", "low", "close", "adj_close", "volume"]]).all().all():
            raise SystemExit("BLOCK_PRE2023_NONFINITE_AFTER_FILTER")
        if (corpus[["open", "high", "low", "close", "adj_close"]] <= 0).any().any() or (corpus["volume"] < 0).any():
            raise SystemExit("BLOCK_PRE2023_INVALID_VALUE_AFTER_FILTER")

    corpus.to_parquet(output_dir / "PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet", index=False)
    rejected.to_csv(output_dir / "PRE2023_YAHOO_EXCLUSIONS.csv", index=False)
    inv.to_csv(output_dir / "PRE2023_YAHOO_INVENTORY.csv", index=False)

    year_coverage = pd.DataFrame(columns=["ticker", "year", "rows"])
    if len(corpus):
        yc = corpus.assign(year=corpus["date"].dt.year).groupby(["ticker", "year"], as_index=False).size()
        year_coverage = yc.rename(columns={"size": "rows"})
    year_coverage.to_csv(output_dir / "PRE2023_YAHOO_YEAR_COVERAGE.csv", index=False)

    ok = inv[inv["status"].eq("OK")] if len(inv) else inv
    total_raw = int(pd.to_numeric(inv.get("raw_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total_kept = int(len(corpus))
    total_excluded = int(len(rejected))
    summary = {
        "purpose": "DEVELOPMENT_PRICE_CORPUS_BOOTSTRAP",
        "source": "YAHOO_YFINANCE_RAW",
        "auto_adjust": False,
        "repair": False,
        "exclusion_policy": "FAIL_CLOSED_ROW_EXCLUSION_NO_TOLERANCE_NO_SYNTHETIC_REPAIR",
        "historical_universe_certified": False,
        "survivorship_safe": False,
        "historical_pea_eligibility_certified": False,
        "repair_promoted": False,
        "retuning": False,
        "holdout_accessed_for_prices": False,
        "identity_bootstrap": "CURRENT_GOVERNED_ACTION_CACHE_TICKERS_ONLY",
        "requested_start": START,
        "requested_end_exclusive": END_EXCLUSIVE,
        "available_identity_tickers": int(len(tickers)),
        "ok_tickers": int(len(ok)),
        "no_history_tickers": int((inv["status"] == "NO_HISTORY").sum()) if len(inv) else 0,
        "request_error_tickers": int((inv["status"] == "REQUEST_ERROR").sum()) if len(inv) else 0,
        "quality_error_tickers": int((inv["status"] == "QUALITY_ERROR").sum()) if len(inv) else 0,
        "all_rows_excluded_tickers": int((inv["status"] == "ALL_ROWS_EXCLUDED").sum()) if len(inv) else 0,
        "coverage_pct": round(100.0 * len(ok) / len(tickers), 4) if tickers else 0.0,
        "raw_rows": total_raw,
        "kept_rows": total_kept,
        "excluded_rows": total_excluded,
        "excluded_pct_of_raw": round(100.0 * total_excluded / total_raw, 6) if total_raw else 0.0,
        "min_kept_date": str(corpus["date"].min().date()) if len(corpus) else None,
        "max_kept_date": str(corpus["date"].max().date()) if len(corpus) else None,
        "duplicate_ticker_dates_after_filter": int(corpus.duplicated(["ticker", "date"]).sum()) if len(corpus) else 0,
        "invalid_ohlc_after_filter": 0,
    }
    (output_dir / "PRE2023_YAHOO_CORPUS_MANIFEST.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)

    if not len(corpus):
        raise SystemExit("BLOCK_PRE2023_EMPTY_CORPUS")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", default="data/cache/actions")
    parser.add_argument("--output-dir", default="outputs/pre2023_yahoo_corpus")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--pause-seconds", type=float, default=0.25)
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise SystemExit("INVALID_PRE2023_BATCH_SIZE")
    run(Path(args.cache_root), Path(args.output_dir), args.batch_size, args.pause_seconds)


if __name__ == "__main__":
    main()
