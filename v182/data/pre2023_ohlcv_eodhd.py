"""Build a governed PRE-2023 OHLCV development corpus from EODHD.

This module is deliberately isolated from the 2023-2026 holdout cache. It never
writes to data/cache and refuses any requested/end date on or after 2023-01-01.
No synthetic fill/interpolation is allowed. Missing/invalid symbols fail closed.

Input CSV columns:
  ticker,eodhd_symbol
Example:
  AI.PA,AI.PA

Environment:
  EODHD_API_TOKEN   required; never persist the token in outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

HOLDOUT_START = pd.Timestamp("2023-01-01", tz="UTC")
DEFAULT_START = "2010-01-01"
DEFAULT_END = "2022-12-31"
REQUIRED = ["date", "open", "high", "low", "close", "volume"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_date(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _validate_window(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    a, b = _utc_date(start), _utc_date(end)
    if b < a:
        raise ValueError("BLOCK_PRE2023_WINDOW: end before start")
    if b >= HOLDOUT_START or a >= HOLDOUT_START:
        raise ValueError("BLOCK_PRE2023_HOLDOUT_LEAK: requested window touches 2023-2026 holdout")
    return a, b


def _load_symbols(path: Path) -> pd.DataFrame:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"BLOCK_PRE2023_SYMBOLS: missing/empty {path}")
    df = pd.read_csv(path, dtype=str)
    need = {"ticker", "eodhd_symbol"}
    if not need.issubset(df.columns):
        raise ValueError(f"BLOCK_PRE2023_SYMBOLS: required columns={sorted(need)}")
    df = df[["ticker", "eodhd_symbol"]].fillna("").apply(lambda s: s.str.strip())
    if (df == "").any().any() or df["ticker"].duplicated().any() or df["eodhd_symbol"].duplicated().any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS: blanks or duplicate ticker/source symbols")
    return df


def _fetch_symbol(symbol: str, token: str, start: str, end: str, timeout: int = 45) -> pd.DataFrame:
    query = urlencode({"api_token": token, "from": start, "to": end, "period": "d", "fmt": "json", "order": "a"})
    url = f"https://eodhd.com/api/eod/{symbol}?{query}"
    req = Request(url, headers={"User-Agent": "pea-analyzer-pre2023/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"BLOCK_PRE2023_SOURCE: no rows for {symbol}")
    df = pd.DataFrame(payload)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"BLOCK_PRE2023_SCHEMA: {symbol} missing {missing}")
    return df


def _validate_bars(df: pd.DataFrame, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    out = df[REQUIRED + (["adjusted_close"] if "adjusted_close" in df.columns else [])].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce", utc=True)
    numeric_cols = ["open", "high", "low", "close", "volume"] + (["adjusted_close"] if "adjusted_close" in out.columns else [])
    for c in numeric_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if out["date"].isna().any() or out[numeric_cols].isna().any().any():
        raise ValueError(f"BLOCK_PRE2023_QUALITY: invalid dates/numerics for {ticker}")
    if out["date"].duplicated().any() or not out["date"].is_monotonic_increasing:
        raise ValueError(f"BLOCK_PRE2023_QUALITY: duplicate/non-monotonic dates for {ticker}")
    if (out[["open", "high", "low", "close"]] <= 0).any().any() or (out["volume"] < 0).any():
        raise ValueError(f"BLOCK_PRE2023_QUALITY: non-positive OHLC or negative volume for {ticker}")
    if "adjusted_close" in out.columns and (out["adjusted_close"] <= 0).any():
        raise ValueError(f"BLOCK_PRE2023_QUALITY: non-positive adjusted_close for {ticker}")
    bad_geometry = (out["high"] < out[["open", "close", "low"]].max(axis=1)) | (out["low"] > out[["open", "close", "high"]].min(axis=1))
    if bad_geometry.any():
        raise ValueError(f"BLOCK_PRE2023_QUALITY: impossible OHLC geometry for {ticker}")
    if out["date"].max() >= HOLDOUT_START:
        raise ValueError(f"BLOCK_PRE2023_HOLDOUT_LEAK: returned data reaches holdout for {ticker}")
    out = out[(out["date"] >= start) & (out["date"] <= end)].copy()
    if out.empty:
        raise ValueError(f"BLOCK_PRE2023_COVERAGE: empty retained window for {ticker}")
    out.insert(1, "ticker", ticker.upper())
    return out


def build(symbols_file: str | Path, start: str, end: str, output_dir: str | Path, sleep_s: float = 0.05) -> dict:
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token:
        raise ValueError("BLOCK_PRE2023_SECRET: EODHD_API_TOKEN missing")
    start_ts, end_ts = _validate_window(start, end)
    symbols = _load_symbols(Path(symbols_file))
    root = Path(output_dir)
    if "data/cache" in str(root).replace("\\", "/"):
        raise ValueError("BLOCK_PRE2023_ISOLATION: output may not be inside holdout cache")
    bars_dir = root / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)

    inventory = []
    for row in symbols.itertuples(index=False):
        raw = _fetch_symbol(row.eodhd_symbol, token, start_ts.date().isoformat(), end_ts.date().isoformat())
        bars = _validate_bars(raw, row.ticker, start_ts, end_ts)
        target = bars_dir / f"{row.ticker.replace('/', '_')}.parquet"
        bars.to_parquet(target, index=False)
        inventory.append({
            "ticker": row.ticker,
            "source_symbol": row.eodhd_symbol,
            "rows": int(len(bars)),
            "min_date": str(bars["date"].min()),
            "max_date": str(bars["date"].max()),
            "sha256": _sha256(target),
            "file": str(target),
        })
        time.sleep(max(0.0, sleep_s))

    manifest = {
        "status": "OK",
        "dataset_role": "DEVELOPMENT_PRE2023_ONLY",
        "provider": "EODHD",
        "endpoint": "EOD historical daily",
        "requested_window": {"start": str(start_ts), "end": str(end_ts)},
        "holdout_start": str(HOLDOUT_START),
        "holdout_rows_allowed": 0,
        "synthetic_fill": False,
        "interpolation": False,
        "source_token_persisted": False,
        "symbols_file": str(symbols_file),
        "symbols_file_sha256": _sha256(Path(symbols_file)),
        "instrument_count": int(len(inventory)),
        "inventory": inventory,
    }
    (root / "MANIFEST_PRE2023_OHLCV.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pd.DataFrame(inventory).to_csv(root / "INVENTORY_PRE2023_OHLCV.csv", index=False)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", required=True)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--output-dir", default="data/dev_pre2023/eodhd")
    p.add_argument("--sleep-s", type=float, default=0.05)
    args = p.parse_args()
    try:
        result = build(args.symbols, args.start, args.end, args.output_dir, args.sleep_s)
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        raise SystemExit(2)
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
