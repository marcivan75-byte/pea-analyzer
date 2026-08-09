from __future__ import annotations

from pathlib import Path
from typing import Iterable
import re

import numpy as np
import pandas as pd

from .config import DATE_CANDIDATES, ID_CANDIDATES, PRICE_CANDIDATES


def first_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def normalise_id(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().replace({"": np.nan, "NAN": np.nan, "NONE": np.nan})


def coerce_score(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    finite = s.dropna()
    if len(finite) and finite.quantile(0.99) <= 3.2 and finite.min() >= -0.1:
        s = s / 3.0 * 100.0
    return s.clip(0.0, 100.0)


def infer_snapshot_date(path: Path) -> pd.Timestamp | None:
    tokens = [path.name, *[p.name for p in path.parents][:3]]
    for token in tokens:
        m = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", token)
        if m:
            try:
                return pd.Timestamp(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
            except ValueError:
                pass
    return None


def load_snapshot_files(input_path: str | Path) -> pd.DataFrame:
    """Load only dated point-in-time CSV/Parquet snapshots with identifier and price."""
    root = Path(input_path)
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".parquet", ".pq"}
    )
    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            df = (pd.read_csv(path, sep=None, engine="python", dtype=object, encoding="utf-8-sig")
                  if path.suffix.lower() == ".csv" else pd.read_parquet(path))
        except Exception:
            continue
        id_col = first_column(df, ID_CANDIDATES)
        price_col = first_column(df, PRICE_CANDIDATES)
        if id_col is None or price_col is None:
            continue
        date_col = first_column(df, DATE_CANDIDATES)
        if date_col is None:
            inferred = infer_snapshot_date(path)
            if inferred is None:
                continue
            df = df.copy()
            df["snapshot_date"] = inferred
            date_col = "snapshot_date"
        keep = df.copy()
        keep["__instrument_id"] = normalise_id(keep[id_col])
        keep["__snapshot_date"] = pd.to_datetime(keep[date_col], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
        keep["__price"] = pd.to_numeric(keep[price_col], errors="coerce")
        keep["__source_file"] = str(path)
        keep = keep.dropna(subset=["__instrument_id", "__snapshot_date", "__price"])
        keep = keep[keep["__price"] > 0]
        if not keep.empty:
            frames.append(keep)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    out = out.sort_values(["__snapshot_date", "__instrument_id", "__source_file"])
    return out.drop_duplicates(["__snapshot_date", "__instrument_id"], keep="last").reset_index(drop=True)


def attach_forward_returns(df: pd.DataFrame, horizon_days: int, tolerance_days: int) -> pd.DataFrame:
    """Derive forward returns only from later archived snapshots of the same instrument, preserving input row order."""
    if df.empty:
        return df.copy()
    work = df.copy().reset_index(drop=True)
    work["__row_order"] = np.arange(len(work), dtype=int)
    out_parts: list[pd.DataFrame] = []
    lower = max(1, horizon_days - tolerance_days)
    upper = horizon_days + tolerance_days
    for _, g in work.groupby("__instrument_id", sort=False):
        g = g.sort_values("__snapshot_date").copy()
        dates = g["__snapshot_date"].to_numpy(dtype="datetime64[D]")
        prices = g["__price"].to_numpy(dtype=float)
        fwd = np.full(len(g), np.nan)
        realized_days = np.full(len(g), np.nan)
        for i in range(len(g)):
            delta = (dates - dates[i]).astype("timedelta64[D]").astype(int)
            candidates = np.where((delta >= lower) & (delta <= upper))[0]
            candidates = candidates[candidates > i]
            if len(candidates) == 0:
                continue
            j = candidates[np.argmin(np.abs(delta[candidates] - horizon_days))]
            fwd[i] = prices[j] / prices[i] - 1.0
            realized_days[i] = delta[j]
        g["__forward_return"] = fwd
        g["__realized_horizon_days"] = realized_days
        out_parts.append(g)
    out = pd.concat(out_parts, ignore_index=True, sort=False)
    return out.sort_values("__row_order").drop(columns="__row_order").reset_index(drop=True)
