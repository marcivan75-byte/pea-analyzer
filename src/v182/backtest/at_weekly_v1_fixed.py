"""AT WEEKLY V1 corrected reader for the consolidated OHLCV cache.

Research only. Strategy thresholds are intentionally unchanged.
The optimization here concerns cache parsing, diagnostics and runtime only.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import time

import numpy as np
import pandas as pd

from .at_weekly_v1 import (
    MIN_WEEKLY_BARS,
    _backtest_one,
    _markdown,
    _to_weekly,
    _window_metrics,
)

ROOT = Path(__file__).resolve().parents[3]
CACHE_DIRS = {
    "ACTION": Path("data/cache/actions"),
    "ETF": Path("data/cache/etf"),
}
OUT_JSON = Path("outputs/backtest/AT_WEEKLY_V1_SUMMARY.json")
OUT_TRADES = Path("outputs/backtest/AT_WEEKLY_V1_TRADES.csv")
OUT_MD = Path("outputs/backtest/AT_WEEKLY_V1_SUMMARY.md")

PRICE_FIELDS = {"open", "high", "low", "close", "volume", "adj_close"}
REQUIRED = {"open", "high", "low", "close"}


def _norm_field(value) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("adj_close", "adj_close")


def _normalize_index(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[out.index.notna()]
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    out = _normalize_index(frame)
    out.columns = [_norm_field(c) for c in out.columns]
    # A few cache generations may contain a duplicate field. Keep the last copy.
    out = out.loc[:, ~pd.Index(out.columns).duplicated(keep="last")]
    if not REQUIRED <= set(out.columns):
        return pd.DataFrame()
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in out.columns]
    out = out[keep].copy()
    for col in keep:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    # OHLC all need to exist on the session used by the strategy.
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out


def _multiindex_orientation(columns: pd.MultiIndex) -> tuple[int, int] | None:
    """Return (instrument_level, field_level) without assuming yfinance orientation."""
    scores = []
    for level in range(columns.nlevels):
        values = {_norm_field(v) for v in columns.get_level_values(level)}
        scores.append(len(values & PRICE_FIELDS))
    field_level = int(np.argmax(scores))
    if scores[field_level] < 4:
        return None
    candidates = [i for i in range(columns.nlevels) if i != field_level]
    if not candidates:
        return None
    # Prefer the non-field level with most unique values: normally the ticker.
    instrument_level = max(candidates, key=lambda i: columns.get_level_values(i).nunique())
    return instrument_level, field_level


def _iter_consolidated(path: Path):
    """Yield (symbol, daily_ohlcv) from one cache file, loading it only once."""
    try:
        if path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        elif path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, header=[0, 1], index_col=0, parse_dates=True)
        else:
            return
    except Exception as exc:
        yield None, None, f"READ_ERROR:{type(exc).__name__}"
        return

    if frame.empty:
        yield None, None, "EMPTY_FILE"
        return

    if isinstance(frame.columns, pd.MultiIndex):
        orientation = _multiindex_orientation(frame.columns)
        if orientation is None:
            yield None, None, "UNRECOGNIZED_MULTIINDEX"
            return
        instrument_level, field_level = orientation
        instruments = pd.Index(frame.columns.get_level_values(instrument_level)).dropna().unique()
        for instrument in instruments:
            try:
                mask = frame.columns.get_level_values(instrument_level) == instrument
                sub = frame.loc[:, mask].copy()
                sub.columns = [_norm_field(c[field_level]) for c in sub.columns]
                history = _normalize_history(sub)
                yield str(instrument), history, None if not history.empty else "MISSING_OHLC"
            except Exception as exc:
                yield str(instrument), pd.DataFrame(), f"INSTRUMENT_ERROR:{type(exc).__name__}"
        return

    # Defensive compatibility with a future long-format cache.
    flat = frame.copy()
    flat.columns = [_norm_field(c) for c in flat.columns]
    instrument_col = next((c for c in ("ticker", "symbol", "isin", "instrument") if c in flat.columns), None)
    date_col = next((c for c in ("date", "datetime", "timestamp") if c in flat.columns), None)
    if instrument_col and date_col and REQUIRED <= set(flat.columns):
        flat[date_col] = pd.to_datetime(flat[date_col], errors="coerce")
        for instrument, sub in flat.groupby(instrument_col, sort=False):
            history = sub.set_index(date_col)
            history = _normalize_history(history)
            yield str(instrument), history, None if not history.empty else "MISSING_OHLC"
        return

    # Last compatibility case: one instrument per file.
    if REQUIRED <= set(flat.columns):
        if not isinstance(flat.index, pd.DatetimeIndex) and date_col:
            flat = flat.set_index(date_col)
        history = _normalize_history(flat)
        yield path.stem, history, None if not history.empty else "MISSING_OHLC"
        return

    yield None, None, "UNRECOGNIZED_FLAT_SCHEMA"


def _cache_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    # Parquet is the governed cache format. CSV remains a compatibility fallback.
    return sorted(list(folder.rglob("*.parquet")) + list(folder.rglob("*.csv")))


def _empty_trade_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "asset_class", "isin", "entry_date", "exit_date", "return_pct",
        "holding_weeks", "exit_reasons",
    ])


def run(root: Path = ROOT) -> dict:
    started = time.perf_counter()
    all_trades: list[dict] = []
    open_positions: list[dict] = []
    filter_totals = Counter()
    failures = Counter()
    data_first: list[pd.Timestamp] = []
    data_last: list[pd.Timestamp] = []
    scopes: dict[str, dict] = {}
    seen_global: set[tuple[str, str]] = set()

    for asset, relative in CACHE_DIRS.items():
        files = _cache_files(root / relative)
        valid = 0
        raw_instruments = 0
        short_history = 0
        duplicate_symbols = 0
        scope_trades: list[dict] = []
        scope_open: list[dict] = []

        for path in files:
            file_yielded = False
            for symbol, history, error in _iter_consolidated(path):
                if symbol is None:
                    failures[f"{asset}_{error or 'UNKNOWN_FILE_ERROR'}"] += 1
                    continue
                file_yielded = True
                raw_instruments += 1
                key = (asset, symbol)
                if key in seen_global:
                    duplicate_symbols += 1
                    failures[f"{asset}_DUPLICATE_SYMBOL"] += 1
                    continue
                seen_global.add(key)
                if error or history is None or history.empty:
                    failures[f"{asset}_{error or 'INVALID_OHLCV'}"] += 1
                    continue
                weekly = _to_weekly(history)
                if len(weekly) < MIN_WEEKLY_BARS:
                    short_history += 1
                    failures[f"{asset}_SHORT_HISTORY"] += 1
                    continue
                valid += 1
                data_first.append(pd.Timestamp(weekly.index.min()))
                data_last.append(pd.Timestamp(weekly.index.max()))
                trades, diagnostics, open_position = _backtest_one(asset, symbol, weekly)
                scope_trades.extend(trades)
                filter_totals.update(diagnostics)
                if open_position:
                    scope_open.append(open_position)
            if not file_yielded:
                failures[f"{asset}_FILE_NO_INSTRUMENTS"] += 1

        all_trades.extend(scope_trades)
        open_positions.extend(scope_open)
        scopes[asset] = {
            "cache_files": len(files),
            "raw_instruments": raw_instruments,
            "valid_instruments": valid,
            "short_history_instruments": short_history,
            "duplicate_symbols": duplicate_symbols,
            "completed_trades": len(scope_trades),
            "open_positions": len(scope_open),
        }

    trades_df = pd.DataFrame(all_trades)
    if trades_df.empty:
        trades_df = _empty_trade_frame()
    else:
        trades_df = trades_df.sort_values(["entry_date", "asset_class", "isin"]).reset_index(drop=True)

    if data_last:
        end_date = max(data_last)
    else:
        end_date = pd.Timestamp.now(tz="UTC").tz_localize(None)

    for asset in ("ACTION", "ETF"):
        subset = trades_df[trades_df["asset_class"].eq(asset)]
        scopes[asset]["metrics"] = _window_metrics(subset, end_date)

    scopes["TOTAL"] = {
        "raw_instruments": scopes["ACTION"]["raw_instruments"] + scopes["ETF"]["raw_instruments"],
        "valid_instruments": scopes["ACTION"]["valid_instruments"] + scopes["ETF"]["valid_instruments"],
        "completed_trades": int(len(trades_df)),
        "open_positions": len(open_positions),
        "metrics": _window_metrics(trades_df, end_date),
    }

    exit_counts = Counter()
    for text in trades_df.get("exit_reasons", pd.Series(dtype=str)).fillna(""):
        for reason in str(text).split("|"):
            if reason:
                exit_counts[reason] += 1

    runtime = round(time.perf_counter() - started, 3)
    payload = {
        "status": "SUCCESS" if scopes["TOTAL"]["valid_instruments"] > 0 else "NO_USABLE_CACHE",
        "version": "AT_WEEKLY_V1_1_READER_FIXED_2026_08_29",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": runtime,
        "research_only": True,
        "promotion_eligible": False,
        "decision_influence": 0.0,
        "real_orders_enabled": False,
        "strategy_parameters_changed": False,
        "reader": "CONSOLIDATED_MULTIINDEX_BLOCK_READER_V1",
        "rules": {
            "timeframe": "weekly_W-FRI_completed_bars",
            "entry_all": [
                "RSI14_LT_60", "STOCH_14_3_3_K_CROSS_UP_D",
                "CLOSE_GT_SMA20", "CLOSE_GT_SMA50", "CLOSE_GT_PSAR_0_02_0_20",
            ],
            "exit_any": [
                "RSI14_GT_75", "STOCH_K_GT_75", "CLOSE_LT_SMA20",
                "CLOSE_LT_SMA50", "CLOSE_LT_PSAR",
            ],
            "execution": "NEXT_WEEK_OPEN",
            "fees_slippage": "NOT_APPLIED",
        },
        "data_window": {
            "first_completed_week": min(data_first).date().isoformat() if data_first else None,
            "last_completed_week": max(data_last).date().isoformat() if data_last else None,
        },
        "scopes": scopes,
        "entry_filter_diagnostics": dict(filter_totals),
        "exit_trigger_counts": dict(exit_counts),
        "failures": dict(failures),
        "limitations": [
            "CURRENT_CACHE_UNIVERSE_NOT_POINT_IN_TIME_MEMBERSHIP",
            "SURVIVORSHIP_BIAS_POSSIBLE",
            "NO_FEES_OR_SLIPPAGE",
            "PRE_OOS_DIAGNOSTIC_ONLY",
        ],
    }

    out_json = root / OUT_JSON
    out_csv = root / OUT_TRADES
    out_md = root / OUT_MD
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    trades_df.to_csv(out_csv, sep=";", index=False, encoding="utf-8-sig")
    out_md.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return payload


if __name__ == "__main__":
    run()
