from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

OHLCV_FIELDS = {"open", "high", "low", "close", "volume"}


def _ticker_level(frame: pd.DataFrame) -> int | None:
    if not isinstance(frame.columns, pd.MultiIndex):
        return None
    for level in range(frame.columns.nlevels):
        values = {str(v).strip().lower() for v in frame.columns.get_level_values(level)}
        field_hits = len(values.intersection(OHLCV_FIELDS))
        if field_hits >= 3:
            continue
        other_levels = [i for i in range(frame.columns.nlevels) if i != level]
        for other in other_levels:
            other_values = {str(v).strip().lower() for v in frame.columns.get_level_values(other)}
            if len(other_values.intersection(OHLCV_FIELDS)) >= 3:
                return level
    return 0


def _field_level(frame: pd.DataFrame, ticker_level: int) -> int:
    for level in range(frame.columns.nlevels):
        if level == ticker_level:
            continue
        values = {str(v).strip().lower() for v in frame.columns.get_level_values(level)}
        if len(values.intersection(OHLCV_FIELDS)) >= 3:
            return level
    raise ValueError("OHLCV_FIELD_LEVEL_NOT_FOUND")


def cache_frame_to_long(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "instrument_id", "open", "high", "low", "close", "volume"])
    if not isinstance(frame.columns, pd.MultiIndex):
        raise ValueError("CACHE_MULTIINDEX_REQUIRED")
    ticker_level = _ticker_level(frame)
    if ticker_level is None:
        raise ValueError("CACHE_TICKER_LEVEL_NOT_FOUND")
    field_level = _field_level(frame, ticker_level)
    tickers = sorted({str(v) for v in frame.columns.get_level_values(ticker_level)})
    pieces: list[pd.DataFrame] = []
    for ticker in tickers:
        try:
            sub = frame.xs(ticker, axis=1, level=ticker_level, drop_level=True)
        except (KeyError, ValueError):
            continue
        if isinstance(sub.columns, pd.MultiIndex):
            while isinstance(sub.columns, pd.MultiIndex) and sub.columns.nlevels > 1:
                # Keep the level carrying OHLCV names and drop singleton metadata levels.
                matched = None
                for level in range(sub.columns.nlevels):
                    vals = {str(v).strip().lower() for v in sub.columns.get_level_values(level)}
                    if len(vals.intersection(OHLCV_FIELDS)) >= 3:
                        matched = level
                        break
                if matched is None:
                    break
                if matched != 0:
                    sub.columns = sub.columns.reorder_levels([matched] + [i for i in range(sub.columns.nlevels) if i != matched])
                sub.columns = sub.columns.get_level_values(0)
        rename = {c: str(c).strip().lower() for c in sub.columns}
        sub = sub.rename(columns=rename)
        available = [c for c in ("open", "high", "low", "close", "volume") if c in sub.columns]
        if "close" not in available or len(set(available).intersection({"open", "high", "low"})) < 3:
            continue
        out = sub[available].copy()
        out["date"] = pd.to_datetime(out.index, errors="coerce")
        out["instrument_id"] = ticker
        out = out.reset_index(drop=True)
        for c in ("open", "high", "low", "close", "volume"):
            if c not in out.columns:
                out[c] = pd.NA
            out[c] = pd.to_numeric(out[c], errors="coerce")
        out = out.dropna(subset=["date", "open", "high", "low", "close"])
        pieces.append(out[["date", "instrument_id", "open", "high", "low", "close", "volume"]])
    if not pieces:
        return pd.DataFrame(columns=["date", "instrument_id", "open", "high", "low", "close", "volume"])
    combined = pd.concat(pieces, ignore_index=True)
    return combined.drop_duplicates(["instrument_id", "date"], keep="last").sort_values(["instrument_id", "date"]).reset_index(drop=True)


def load_governed_action_cache(cache_dir: str | Path) -> pd.DataFrame:
    root = Path(cache_dir)
    paths = sorted(root.glob("history_*.parquet"))
    if not paths:
        raise FileNotFoundError(f"NO_HISTORY_PARQUET:{root}")
    pieces: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_parquet(path)
        long = cache_frame_to_long(frame)
        if not long.empty:
            long["cache_source_file"] = path.name
            pieces.append(long)
    if not pieces:
        raise ValueError("NO_VALID_OHLCV_ROWS_IN_CACHE")
    out = pd.concat(pieces, ignore_index=True)
    return out.drop_duplicates(["instrument_id", "date"], keep="last").sort_values(["instrument_id", "date"]).reset_index(drop=True)
