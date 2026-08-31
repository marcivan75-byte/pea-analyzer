from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import time

import numpy as np
import pandas as pd
import yfinance as yf

from v182.audit.canonical_universe import IDENTITY_ONLY_STATUS, filter_actions
from v182.io.frames import load_master
from v182.mapping.action_isin_resolver import apply_identity_overlay
from v182.mapping.identity_overlay_store import materialize_identity_overlay

ROOT = Path(__file__).resolve().parents[1]
START = "2010-01-01"
END_EXCLUSIVE = "2020-01-01"
CANONICAL_VERSION = "PEA_OHLCV_CANONICAL_2010_2019_V1"
OUT = ROOT / "outputs" / "canonical_ohlcv_2010_2019"
EXPECTED_REQUESTED = {"ACTION": 1790, "ETF": 102}


def _governed_master(asset: str) -> pd.DataFrame:
    if asset == "ACTION":
        legacy = load_master(ROOT / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
        canonical = filter_actions(
            legacy, ROOT / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts"
        ).included
        overlay_path = materialize_identity_overlay(ROOT)
        governed = canonical if overlay_path is None else apply_identity_overlay(canonical, overlay_path)[0]
        if "canonical_seed_status" in governed.columns:
            governed = governed[
                ~governed["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS)
            ].copy()
        return governed.reset_index(drop=True)
    if asset == "ETF":
        return load_master(ROOT / "inputs" / "V18.2_PEA_ETF_MASTER.csv").reset_index(drop=True)
    raise RuntimeError(f"UNSUPPORTED_ASSET:{asset}")


def _ticker_column(frame: pd.DataFrame) -> str:
    for col in ("yahoo_ticker", "ticker_yahoo_final", "ticker_yahoo"):
        if col in frame.columns:
            return col
    raise RuntimeError("YAHOO_TICKER_COLUMN_MISSING")


def _clean_mapping(frame: pd.DataFrame, asset: str) -> pd.DataFrame:
    ticker_col = _ticker_column(frame)
    cols = ["isin", ticker_col]
    for optional in ("name", "nom", "primary_mic", "trading_currency"):
        if optional in frame.columns:
            cols.append(optional)
    out = frame[cols].copy().rename(columns={ticker_col: "ticker"})
    out["isin"] = out["isin"].astype(str).str.strip()
    out["ticker"] = out["ticker"].astype(str).str.strip()
    out = out[
        ~out["ticker"].str.lower().isin({"", "nan", "none", "<na>"})
        & ~out["isin"].str.lower().isin({"", "nan", "none", "<na>"})
    ].copy()
    if out["isin"].duplicated().any():
        raise RuntimeError(f"{asset}_DUPLICATE_ISIN_MAPPING:{int(out['isin'].duplicated(keep=False).sum())}")
    conflicts = out[out["ticker"].duplicated(keep=False)].sort_values("ticker")
    if not conflicts.empty:
        sample = conflicts[["isin", "ticker"]].head(20).to_dict("records")
        raise RuntimeError(
            f"{asset}_AMBIGUOUS_TICKER_MAPPING:{conflicts['ticker'].nunique()}:{sample}"
        )
    expected = EXPECTED_REQUESTED[asset]
    if len(out) != expected:
        raise RuntimeError(f"{asset}_GOVERNED_REQUESTED_COUNT:{len(out)}:expected={expected}")
    return out.reset_index(drop=True)


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
    sub = sub.loc[close.notna()].copy()
    return sub


def _download_asset(
    asset: str, mapping: pd.DataFrame, batch_size: int = 40
) -> tuple[pd.DataFrame, list[dict]]:
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
            failures.extend(
                {
                    "asset_class": asset,
                    "ticker": ticker,
                    "isin": meta[ticker]["isin"],
                    "reason": type(exc).__name__,
                    "detail": str(exc)[:160],
                }
                for ticker in batch
            )
        for ticker in batch:
            sub = _extract_one(frame, ticker)
            if sub.empty:
                try:
                    retry = yf.download(
                        tickers=[ticker],
                        start=START,
                        end=END_EXCLUSIVE,
                        interval="1d",
                        group_by="ticker",
                        auto_adjust=False,
                        actions=True,
                        threads=False,
                        progress=False,
                        timeout=30,
                    )
                    sub = _extract_one(retry, ticker)
                except Exception as exc:
                    failures.append(
                        {
                            "asset_class": asset,
                            "ticker": ticker,
                            "isin": meta[ticker]["isin"],
                            "reason": type(exc).__name__,
                            "detail": str(exc)[:160],
                        }
                    )
            if sub.empty:
                failures.append(
                    {
                        "asset_class": asset,
                        "ticker": ticker,
                        "isin": meta[ticker]["isin"],
                        "reason": "NO_PRICE_DATA_2010_2019",
                    }
                )
                continue
            m = meta[ticker]
            out = pd.DataFrame(index=sub.index)
            out["date"] = sub.index
            out["asset_class"] = asset
            out["isin"] = m["isin"]
            out["ticker"] = ticker
            for source, target in (
                ("Open", "open_raw"),
                ("High", "high_raw"),
                ("Low", "low_raw"),
                ("Close", "close_raw"),
                ("Adj Close", "adj_close"),
                ("Volume", "volume"),
                ("Dividends", "dividends"),
                ("Stock Splits", "stock_splits"),
            ):
                out[target] = (
                    pd.to_numeric(sub[source], errors="coerce").to_numpy()
                    if source in sub.columns
                    else np.nan
                )
            out["source"] = "Yahoo Finance via yfinance"
            out["source_contract"] = (
                "HISTORICAL_DAILY_OHLCV_CURRENT_UNIVERSE_RECONSTRUCTION"
            )
            rows.append(out.reset_index(drop=True))
        time.sleep(0.25)
    return (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()), failures


def _coverage(data: pd.DataFrame, mapping: pd.DataFrame, asset: str) -> pd.DataFrame:
    requested = int(mapping["ticker"].nunique())
    records = []
    for year in range(2010, 2020):
        subset = (
            data[pd.to_datetime(data["date"]).dt.year == year]
            if not data.empty
            else pd.DataFrame()
        )
        covered = int(subset["ticker"].nunique()) if not subset.empty else 0
        records.append(
            {
                "asset_class": asset,
                "year": year,
                "requested_tickers_governed_current_universe": requested,
                "covered_tickers": covered,
                "coverage_pct": round(100.0 * covered / requested, 4) if requested else 0.0,
                "rows": int(len(subset)),
            }
        )
    return pd.DataFrame(records)


def _ticker_coverage(data: pd.DataFrame, mapping: pd.DataFrame, asset: str) -> pd.DataFrame:
    observed = (
        data.groupby(["isin", "ticker"])["date"].agg(["min", "max", "count"]).reset_index()
        if not data.empty
        else pd.DataFrame(columns=["isin", "ticker", "min", "max", "count"])
    )
    observed = observed.rename(
        columns={"min": "first_observed_date", "max": "last_observed_date", "count": "rows"}
    )
    result = mapping[["isin", "ticker"]].merge(observed, on=["isin", "ticker"], how="left")
    result.insert(0, "asset_class", asset)
    result["observed_years"] = result.apply(
        lambda row: (
            pd.Timestamp(row["last_observed_date"]).year
            - pd.Timestamp(row["first_observed_date"]).year
            + 1
        )
        if pd.notna(row["first_observed_date"]) and pd.notna(row["last_observed_date"])
        else 0,
        axis=1,
    )
    return result


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
    all_ticker_coverage = []
    all_failures: list[dict] = []
    identity_snapshots = []
    requested_counts: dict[str, int] = {}

    for asset in ("ACTION", "ETF"):
        mapping = _clean_mapping(_governed_master(asset), asset)
        requested_counts[asset] = int(len(mapping))
        mapping["asset_class"] = asset
        identity_snapshots.append(mapping)
        data, failures = _download_asset(asset, mapping)
        all_failures.extend(failures)
        if not data.empty:
            dates = pd.to_datetime(data["date"])
            data = data[
                (dates >= pd.Timestamp(START)) & (dates < pd.Timestamp(END_EXCLUSIVE))
            ].copy()
            data = data[data["close_raw"].notna()].copy()
            data = data.sort_values(["isin", "date"]).drop_duplicates(
                ["isin", "date"], keep="last"
            )
            all_data.append(data)
        all_coverage.append(_coverage(data, mapping, asset))
        all_ticker_coverage.append(_ticker_coverage(data, mapping, asset))

    canonical = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
    coverage = pd.concat(all_coverage, ignore_index=True)
    ticker_coverage = pd.concat(all_ticker_coverage, ignore_index=True)
    identities = pd.concat(identity_snapshots, ignore_index=True)
    failures = (
        pd.DataFrame(all_failures).drop_duplicates()
        if all_failures
        else pd.DataFrame(columns=["asset_class", "ticker", "isin", "reason"])
    )

    if canonical.empty:
        raise RuntimeError("CANONICAL_OHLCV_EMPTY")
    if canonical.duplicated(["isin", "date"]).any():
        raise RuntimeError("CANONICAL_DUPLICATE_ISIN_DATE")
    if canonical[["isin", "ticker", "date", "close_raw"]].isna().any().any():
        raise RuntimeError("CANONICAL_CRITICAL_NULL")

    data_path = OUT / "PEA_OHLCV_2010_2019_CANONICAL.parquet"
    identity_path = OUT / "PEA_IDENTITY_SNAPSHOT_CURRENT_UNIVERSE.csv"
    coverage_path = OUT / "PEA_OHLCV_2010_2019_COVERAGE.csv"
    ticker_coverage_path = OUT / "PEA_OHLCV_2010_2019_TICKER_COVERAGE.csv"
    failures_path = OUT / "PEA_OHLCV_2010_2019_FAILURES.csv"
    canonical.to_parquet(data_path, index=False, compression="zstd")
    identities.to_csv(identity_path, sep=";", index=False, encoding="utf-8-sig")
    coverage.to_csv(coverage_path, sep=";", index=False, encoding="utf-8-sig")
    ticker_coverage.to_csv(ticker_coverage_path, sep=";", index=False, encoding="utf-8-sig")
    failures.to_csv(failures_path, sep=";", index=False, encoding="utf-8-sig")

    collected_counts = {
        asset: int(canonical.loc[canonical["asset_class"].eq(asset), "isin"].nunique())
        for asset in ("ACTION", "ETF")
    }
    manifest = {
        "canonical_version": CANONICAL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "period": {
            "start_inclusive": START,
            "end_exclusive": END_EXCLUSIVE,
            "years": list(range(2010, 2020)),
        },
        "asset_classes": ["ACTION", "ETF"],
        "requested_governed_current_universe": requested_counts,
        "collected_any_observation": collected_counts,
        "rows": int(len(canonical)),
        "unique_isin": int(canonical["isin"].nunique()),
        "unique_tickers": int(canonical["ticker"].nunique()),
        "duplicate_isin_date_rows": 0,
        "critical_null_rows": 0,
        "price_fields": [
            "open_raw",
            "high_raw",
            "low_raw",
            "close_raw",
            "adj_close",
            "volume",
            "dividends",
            "stock_splits",
        ],
        "source": "Yahoo Finance via yfinance",
        "pit_classification": "PRICE_ONLY_HISTORICAL_RECONSTRUCTION",
        "decision_influence": 0.0,
        "coverage_contract": "OBSERVED_CLOSE_ONLY; union-calendar all-NaN rows are excluded",
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
            "Per-year coverage counts only actual non-null Close observations; instruments not yet listed are not backfilled into earlier years.",
        ],
        "files": {},
    }
    for path in (
        data_path,
        identity_path,
        coverage_path,
        ticker_coverage_path,
        failures_path,
    ):
        manifest["files"][path.name] = {
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    (OUT / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": "SUCCESS", **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
