from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import time

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
START = "2010-01-01"
END_EXCLUSIVE = "2020-01-01"
CANONICAL_VERSION = "PEA_OHLCV_CANONICAL_2010_2019_V1"
OUT = ROOT / "outputs" / "canonical_ohlcv_2010_2019"


def _read_master(path: Path) -> pd.DataFrame:
    for sep in (";", ",", "\t"):
        try:
            frame = pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
            if len(frame.columns) > 1:
                return frame
        except Exception:
            continue
    raise RuntimeError(f"MASTER_READ_FAILED:{path}")


def _master(asset: str) -> pd.DataFrame:
    candidates = (
        [ROOT / "inputs/V18.2_PEA_ACTIONS_MASTER.csv", ROOT / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"]
        if asset == "ACTION"
        else [ROOT / "inputs/V18.2_PEA_ETF_MASTER.csv", ROOT / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv"]
    )
    for path in candidates:
        if path.exists():
            frame = _read_master(path)
            if "isin" in frame.columns:
                return frame
    raise RuntimeError(f"{asset}_MASTER_MISSING")


def _ticker_column(frame: pd.DataFrame) -> str:
    for col in ("yahoo_ticker", "ticker_yahoo_final", "ticker_yahoo"):
        if col in frame.columns:
            return col
    raise RuntimeError("YAHOO_TICKER_COLUMN_MISSING")


def _clean_mapping(frame: pd.DataFrame) -> pd.DataFrame:
    ticker_col = _ticker_column(frame)
    cols = ["isin", ticker_col]
    for optional in ("name", "nom", "primary_mic", "trading_currency"):
        if optional in frame.columns:
            cols.append(optional)
    out = frame[cols].copy()
    out = out.rename(columns={ticker_col: "ticker"})
    out["isin"] = out["isin"].astype(str).str.strip()
    out["ticker"] = out["ticker"].astype(str).str.strip()
    out = out[~out["ticker"].str.lower().isin({"", "nan", "none", "<na>"})]
    return out.drop_duplicates("ticker", keep="first")


def _extract_one(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    if isinstance(frame.columns, pd.MultiIndex):
        if ticker in frame.columns.get_level_values(0):
            sub = frame[ticker].copy()
        elif ticker in frame.columns.get_level_values(-1):
            sub = frame.xs(ticker, axis=1, level=-1, drop_level=True).copy()
        else:
            return pd.DataFrame()
    else:
        sub = frame.copy()
    sub.index = pd.to_datetime(sub.index, errors="coerce")
    sub = sub[~sub.index.isna()].sort_index()
    if sub.empty or "Close" not in sub.columns:
        return pd.DataFrame()
    close = pd.to_numeric(sub["Close"], errors="coerce")
    if not close.notna().any():
        return pd.DataFrame()
    return sub


def _download_asset(asset: str, mapping: pd.DataFrame, batch_size: int = 40) -> tuple[pd.DataFrame, list[dict]]:
    tickers = mapping["ticker"].tolist()
    meta = mapping.set_index("ticker").to_dict("index")
    rows: list[pd.DataFrame] = []
    failures: list[dict] = []
    for start in range(0, len(tickers), batch_size):
        batch = tickers[start : start + batch_size]
        try:
            frame = yf.download(
                tickers=batch,
                start=START,
                end=END_EXCLUSIVE,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=True,
                threads=True,
                progress=False,
                timeout=30,
            )
        except Exception as exc:
            frame = pd.DataFrame()
            failures.extend({"asset_class": asset, "ticker": ticker, "reason": type(exc).__name__, "detail": str(exc)[:160]} for ticker in batch)
        for ticker in batch:
            sub = _extract_one(frame, ticker)
            if sub.empty:
                try:
                    retry = yf.download(
                        tickers=[ticker], start=START, end=END_EXCLUSIVE, interval="1d",
                        group_by="ticker", auto_adjust=False, actions=True, threads=False,
                        progress=False, timeout=30,
                    )
                    sub = _extract_one(retry, ticker)
                except Exception as exc:
                    failures.append({"asset_class": asset, "ticker": ticker, "reason": type(exc).__name__, "detail": str(exc)[:160]})
            if sub.empty:
                failures.append({"asset_class": asset, "ticker": ticker, "reason": "NO_PRICE_DATA_2010_2019"})
                continue
            m = meta[ticker]
            out = pd.DataFrame(index=sub.index)
            out["date"] = sub.index
            out["asset_class"] = asset
            out["isin"] = m["isin"]
            out["ticker"] = ticker
            for source, target in (
                ("Open", "open_raw"), ("High", "high_raw"), ("Low", "low_raw"),
                ("Close", "close_raw"), ("Adj Close", "adj_close"), ("Volume", "volume"),
                ("Dividends", "dividends"), ("Stock Splits", "stock_splits"),
            ):
                out[target] = pd.to_numeric(sub[source], errors="coerce").to_numpy() if source in sub.columns else np.nan
            out["source"] = "Yahoo Finance via yfinance"
            out["source_contract"] = "HISTORICAL_DAILY_OHLCV_CURRENT_UNIVERSE_RECONSTRUCTION"
            rows.append(out.reset_index(drop=True))
        time.sleep(0.25)
    return (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()), failures


def _coverage(data: pd.DataFrame, mapping: pd.DataFrame, asset: str) -> pd.DataFrame:
    requested = int(mapping["ticker"].nunique())
    records = []
    for year in range(2010, 2020):
        subset = data[pd.to_datetime(data["date"]).dt.year == year] if not data.empty else pd.DataFrame()
        covered = int(subset["ticker"].nunique()) if not subset.empty else 0
        records.append({
            "asset_class": asset,
            "year": year,
            "requested_tickers_current_universe": requested,
            "covered_tickers": covered,
            "coverage_pct": round(100.0 * covered / requested, 4) if requested else 0.0,
            "rows": int(len(subset)),
        })
    return pd.DataFrame(records)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_data = []
    all_coverage = []
    all_failures: list[dict] = []
    identity_snapshots = []

    for asset in ("ACTION", "ETF"):
        mapping = _clean_mapping(_master(asset))
        mapping["asset_class"] = asset
        identity_snapshots.append(mapping)
        data, failures = _download_asset(asset, mapping)
        all_failures.extend(failures)
        if not data.empty:
            data = data[(pd.to_datetime(data["date"]) >= pd.Timestamp(START)) & (pd.to_datetime(data["date"]) < pd.Timestamp(END_EXCLUSIVE))]
            data = data.sort_values(["isin", "date"]).drop_duplicates(["isin", "date"], keep="last")
            all_data.append(data)
        all_coverage.append(_coverage(data, mapping, asset))

    canonical = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
    coverage = pd.concat(all_coverage, ignore_index=True)
    identities = pd.concat(identity_snapshots, ignore_index=True)
    failures = pd.DataFrame(all_failures).drop_duplicates() if all_failures else pd.DataFrame(columns=["asset_class", "ticker", "reason"])

    data_path = OUT / "PEA_OHLCV_2010_2019_CANONICAL.parquet"
    identity_path = OUT / "PEA_IDENTITY_SNAPSHOT_CURRENT_UNIVERSE.csv"
    coverage_path = OUT / "PEA_OHLCV_2010_2019_COVERAGE.csv"
    failures_path = OUT / "PEA_OHLCV_2010_2019_FAILURES.csv"
    canonical.to_parquet(data_path, index=False, compression="zstd")
    identities.to_csv(identity_path, sep=";", index=False, encoding="utf-8-sig")
    coverage.to_csv(coverage_path, sep=";", index=False, encoding="utf-8-sig")
    failures.to_csv(failures_path, sep=";", index=False, encoding="utf-8-sig")

    manifest = {
        "canonical_version": CANONICAL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "period": {"start_inclusive": START, "end_exclusive": END_EXCLUSIVE, "years": list(range(2010, 2020))},
        "asset_classes": ["ACTION", "ETF"],
        "rows": int(len(canonical)),
        "unique_isin": int(canonical["isin"].nunique()) if not canonical.empty else 0,
        "unique_tickers": int(canonical["ticker"].nunique()) if not canonical.empty else 0,
        "price_fields": ["open_raw", "high_raw", "low_raw", "close_raw", "adj_close", "volume", "dividends", "stock_splits"],
        "source": "Yahoo Finance via yfinance",
        "pit_classification": "PRICE_ONLY_HISTORICAL_RECONSTRUCTION",
        "decision_influence": 0.0,
        "anti_lookahead": {
            "current_fundamentals_used_as_history": False,
            "current_consensus_used_as_history": False,
            "current_sector_scores_used_as_history": False,
            "future_returns_embedded": False,
        },
        "methodological_limits": [
            "Universe membership is reconstructed from the current canonical PEA masters, not a complete historical 2010 constituent ledger.",
            "Delisted, merged or formerly PEA-eligible instruments absent from the current masters may be missing; survivorship bias must be acknowledged in universe-level backtests.",
            "This dataset certifies historical market-price observations only. Fundamental, consensus, news and sector PIT evidence require separate dated sources.",
            "Adjusted Close is retained separately from raw OHLC to avoid silently mixing corporate-action conventions.",
        ],
        "files": {},
    }
    for path in (data_path, identity_path, coverage_path, failures_path):
        manifest["files"][path.name] = {"size_bytes": path.stat().st_size, "sha256": _sha256(path)}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "SUCCESS", **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
