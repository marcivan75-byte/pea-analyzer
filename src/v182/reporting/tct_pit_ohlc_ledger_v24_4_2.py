from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.decision.tct_timing_exact_v24_1_7 import _extract_histories
from v182.reporting.tct_daily_trader_shadow_run_v24_3_1 import _completed_daily_history
from v182.reporting.tct_pit_close_ledger import _candidate_mapping, _read_csv, _write_csv


ROOT = Path(__file__).resolve().parents[3]
CONFIG_V2431 = "TCT_V24_3_1_DAILY_TRADER_SHADOW.json"
CONFIG_V2442 = "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json"
VERSION = "TCT_V24.4.2_PIT_DAILY_OHLC_LEDGER_V2"


def _column(history: pd.DataFrame, target: str) -> str | None:
    for column in history.columns:
        if str(column).strip().lower() == target.lower():
            return str(column)
    return None


def _finite_float(value: object) -> float | None:
    """Normalize a pandas scalar without leaking NaN into the PIT ledger."""
    if value is None or pd.isna(value):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def build_ohlc_observations(mapping: pd.DataFrame, histories: dict[str, pd.DataFrame], cfg_v2431: dict, *, observed_at_utc: str, recent_bars: int = 10) -> pd.DataFrame:
    rows: list[dict] = []
    for _, item in mapping.iterrows():
        ticker = str(item.get("yahoo_ticker") or "").strip()
        isin = str(item.get("isin") or "").strip().upper()
        history = histories.get(ticker)
        if history is None or history.empty:
            continue
        completed, _ = _completed_daily_history(history, cfg_v2431)
        if completed is None or completed.empty:
            continue
        columns = {name: _column(completed, name) for name in ("open", "high", "low", "close")}
        if columns["close"] is None:
            continue
        tail = completed.tail(max(1, int(recent_bars))).copy()
        for date_value, row in tail.iterrows():
            date = pd.to_datetime(date_value, errors="coerce")
            if pd.isna(date):
                continue
            values = {}
            for name, column in columns.items():
                values[name] = None if column is None else pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
            numeric_values = {name: _finite_float(value) for name, value in values.items()}
            close = numeric_values["close"]
            if close is None or close <= 0:
                continue
            rows.append(
                {
                    "version": VERSION,
                    "as_of_date": pd.Timestamp(date).date().isoformat(),
                    "isin": isin,
                    "yahoo_ticker": ticker,
                    "session_open": None if numeric_values["open"] is None else round(numeric_values["open"], 8),
                    "session_high": None if numeric_values["high"] is None else round(numeric_values["high"], 8),
                    "session_low": None if numeric_values["low"] is None else round(numeric_values["low"], 8),
                    "session_close": round(close, 8),
                    "reference_close": round(close, 8),
                    "observed_at_utc": observed_at_utc,
                    "source": "LOCAL_DAILY_OHLCV_CACHE",
                    "network_download_required": False,
                }
            )
    return pd.DataFrame(rows)


def run(root: Path = ROOT) -> dict:
    cfg31 = json.loads((root / "config" / CONFIG_V2431).read_text(encoding="utf-8"))
    cfg42 = json.loads((root / "config" / CONFIG_V2442).read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).isoformat()
    seed = _read_csv(root / cfg42["state"]["context_seed_path"])
    catalyst = _read_csv(root / cfg42["state"]["catalyst_ledger_path"])
    mapping = _candidate_mapping(seed, catalyst)
    tickers = set(mapping.get("yahoo_ticker", pd.Series(dtype=str)).astype(str))
    histories = _extract_histories(root / cfg31["data_policy"]["source_cache"], tickers) if tickers else {}
    recent_bars = int(cfg42.get("pit_lineage", {}).get("ohlc_ledger_recent_bars", 10))
    new_rows = build_ohlc_observations(mapping, histories, cfg31, observed_at_utc=generated_at, recent_bars=recent_bars)
    ledger_path = root / cfg42["state"]["daily_close_ledger_path"]
    existing = _read_csv(ledger_path)
    before = len(existing)
    combined = pd.concat([existing, new_rows], ignore_index=True, sort=False) if not existing.empty else new_rows.copy()
    if not combined.empty:
        combined["as_of_date"] = combined["as_of_date"].astype(str)
        combined["isin"] = combined["isin"].astype(str).str.upper()
        combined = combined.drop_duplicates(["as_of_date", "isin"], keep="first").sort_values(["as_of_date", "isin"])
    _write_csv(combined, ledger_path)
    payload = {
        "status": "SUCCESS_PIT_OHLC_LEDGER",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "candidate_mappings": int(len(mapping)),
        "histories_found": int(len(histories)),
        "new_observations_seen": int(len(new_rows)),
        "new_unique_ohlc_rows": int(max(0, len(combined) - before)),
        "ledger_rows": int(len(combined)),
        "recent_bars_examined_per_ticker": recent_bars,
        "fields": ["open", "high", "low", "close"],
        "local_daily_cache_only": True,
        "network_download_required": False,
        "production_influence": 0.0,
        "output": str(ledger_path.relative_to(root)),
    }
    audit = root / "outputs" / "audit" / "TCT_V24_4_2_PIT_OHLC_LEDGER_AUDIT.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
