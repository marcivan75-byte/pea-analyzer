from __future__ import annotations

"""Governed price-history loader for HEBDO AT META.

2010-2022 is the development segment. Yahoo raw bars are transformed to a
corporate-action-consistent price basis using the contemporaneous row ratio
Adj Close / Close, applied uniformly to OHLC. Raw OHLC are retained for audit.
2023+ is the frozen HOLDOUT segment and is never accepted by fit inputs.

The historical identity bootstrap is still not survivorship-safe and historical
PEA eligibility is not certified. Those limitations are metadata limitations,
not reasons to mix development and holdout or to fabricate prices.
"""

from pathlib import Path
import json
import numpy as np
import pandas as pd

DEVELOPMENT_END = pd.Timestamp("2022-12-31", tz="UTC")
HOLDOUT_START = pd.Timestamp("2023-01-01", tz="UTC")


def _utc_dates(values) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", utc=True)


def load_pre2023_development(corpus_path: str | Path, manifest_path: str | Path) -> pd.DataFrame:
    corpus_path, manifest_path = Path(corpus_path), Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {
        "purpose": "DEVELOPMENT_PRICE_CORPUS_BOOTSTRAP",
        "source": "YAHOO_YFINANCE_RAW",
        "repair": False,
        "holdout_accessed_for_prices": False,
    }
    for k, v in required.items():
        if manifest.get(k) != v:
            raise ValueError(f"BLOCK_META_PRE2023_CONTRACT:{k}={manifest.get(k)!r}")

    df = pd.read_parquet(corpus_path).copy()
    need = {"date", "ticker", "open", "high", "low", "close", "adj_close", "volume"}
    if not need.issubset(df.columns):
        raise ValueError(f"BLOCK_META_PRE2023_COLUMNS:{sorted(need-set(df.columns))}")
    df["date"] = _utc_dates(df["date"])
    if df["date"].isna().any() or df["date"].max() > DEVELOPMENT_END:
        raise ValueError("BLOCK_META_PRE2023_DATE_RANGE")
    if df.duplicated(["ticker", "date"]).any():
        raise ValueError("BLOCK_META_PRE2023_DUPLICATES")

    raw_close = pd.to_numeric(df["close"], errors="coerce")
    adj_close = pd.to_numeric(df["adj_close"], errors="coerce")
    factor = adj_close / raw_close
    if (~np.isfinite(factor)).any() or (factor <= 0).any():
        raise ValueError("BLOCK_META_INVALID_ADJUSTMENT_FACTOR")

    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out[f"raw_{col}"] = pd.to_numeric(out[col], errors="coerce")
        out[col] = out[f"raw_{col}"] * factor
    out["adjustment_factor"] = factor
    out["price_basis"] = "YAHOO_ADJ_CLOSE_RATIO_OHLC"
    out["segment"] = "DEVELOPMENT"
    out["fit_allowed"] = True

    o, h, l, c = (out[x] for x in ("open", "high", "low", "close"))
    if ((h < l) | (h < o) | (h < c) | (l > o) | (l > c)).any():
        raise ValueError("BLOCK_META_ADJUSTED_OHLC_GEOMETRY")
    return out


def load_holdout(ohlc_path: str | Path) -> pd.DataFrame:
    from v182.hebdo.tabport_historical import _read_ohlc_source

    df, _ = _read_ohlc_source(Path(ohlc_path))
    if df.empty:
        raise ValueError("BLOCK_META_EMPTY_HOLDOUT")
    df = df.copy()
    df["date"] = _utc_dates(df["date"])
    df = df.loc[df["date"] >= HOLDOUT_START].copy()
    if df.empty:
        raise ValueError("BLOCK_META_NO_2023_PLUS_HOLDOUT")
    if df.duplicated(["ticker", "date"]).any():
        raise ValueError("BLOCK_META_HOLDOUT_DUPLICATES")
    df["segment"] = "HOLDOUT"
    df["fit_allowed"] = False
    df["price_basis"] = "GOVERNED_HOLDOUT_CACHE"
    return df


def assert_fit_window(frame: pd.DataFrame) -> None:
    if "date" not in frame.columns:
        raise ValueError("BLOCK_META_FIT_MISSING_DATE")
    dates = _utc_dates(frame["date"])
    if dates.isna().any() or (dates >= HOLDOUT_START).any():
        raise ValueError("BLOCK_META_FIT_HOLDOUT_ACCESS")
    if "fit_allowed" in frame.columns and (~frame["fit_allowed"].astype(bool)).any():
        raise ValueError("BLOCK_META_FIT_FLAG")


def load_2010_2026(pre2023_corpus: str | Path, pre2023_manifest: str | Path, holdout_path: str | Path) -> pd.DataFrame:
    dev = load_pre2023_development(pre2023_corpus, pre2023_manifest)
    hold = load_holdout(holdout_path)
    common = ["date", "ticker", "open", "high", "low", "close", "segment", "fit_allowed", "price_basis"]
    combined = pd.concat([dev[common], hold[common]], ignore_index=True)
    combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)
    if combined["date"].min().year != 2010 or combined["date"].max().year < 2026:
        raise ValueError(f"BLOCK_META_2010_2026_COVERAGE:{combined['date'].min()}..{combined['date'].max()}")
    return combined
