from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.decision.tct_timing_exact_v24_1_7 import _extract_histories
from v182.reporting.tct_daily_trader_shadow_run_v24_3_1 import _completed_daily_history


ROOT = Path(__file__).resolve().parents[3]
CONFIG_V2431 = "TCT_V24_3_1_DAILY_TRADER_SHADOW.json"
CONFIG_V244 = "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json"
VERSION = "TCT_V24.4_PIT_DAILY_CLOSE_LEDGER_V1"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _candidate_mapping(seed: pd.DataFrame, catalyst_ledger: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for frame in (seed, catalyst_ledger):
        if frame.empty or "isin" not in frame.columns or "yahoo_ticker" not in frame.columns:
            continue
        part = frame[["isin", "yahoo_ticker"]].copy()
        part["isin"] = part["isin"].astype(str).str.strip().str.upper()
        part["yahoo_ticker"] = part["yahoo_ticker"].astype(str).str.strip()
        part = part[
            part["isin"].ne("")
            & ~part["isin"].isin({"NAN", "NONE"})
            & part["yahoo_ticker"].ne("")
            & ~part["yahoo_ticker"].str.lower().isin({"nan", "none"})
        ]
        frames.append(part)
    if not frames:
        return pd.DataFrame(columns=["isin", "yahoo_ticker"])
    return pd.concat(frames, ignore_index=True).drop_duplicates(["isin", "yahoo_ticker"])


def _close_column(history: pd.DataFrame) -> str | None:
    for column in history.columns:
        if str(column).strip().lower() == "close":
            return str(column)
    return None


def build_close_observations(
    mapping: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    cfg_v2431: dict,
    *,
    observed_at_utc: str,
    recent_bars: int = 10,
) -> pd.DataFrame:
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
        close_col = _close_column(completed)
        if close_col is None:
            continue
        tail = completed.tail(max(1, int(recent_bars))).copy()
        dates = pd.to_datetime(tail.index, errors="coerce")
        closes = pd.to_numeric(tail[close_col], errors="coerce")
        for date_value, close_value in zip(dates, closes):
            if pd.isna(date_value) or pd.isna(close_value) or float(close_value) <= 0:
                continue
            rows.append(
                {
                    "version": VERSION,
                    "as_of_date": pd.Timestamp(date_value).date().isoformat(),
                    "isin": isin,
                    "yahoo_ticker": ticker,
                    "reference_close": round(float(close_value), 8),
                    "observed_at_utc": observed_at_utc,
                    "source": "LOCAL_DAILY_OHLCV_CACHE",
                    "network_download_required": False,
                }
            )
    return pd.DataFrame(rows)


def run(root: Path = ROOT) -> dict:
    cfg31 = json.loads((root / "config" / CONFIG_V2431).read_text(encoding="utf-8"))
    cfg44 = json.loads((root / "config" / CONFIG_V244).read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).isoformat()

    seed_path = root / cfg44["state"]["context_seed_path"]
    catalyst_path = root / cfg44["state"]["catalyst_ledger_path"]
    close_path = root / cfg44["state"].get("daily_close_ledger_path", "state/tct_context/TCT_DAILY_CLOSE_LEDGER.csv")
    seed = _read_csv(seed_path)
    catalyst = _read_csv(catalyst_path)
    mapping = _candidate_mapping(seed, catalyst)

    tickers = set(mapping.get("yahoo_ticker", pd.Series(dtype=str)).astype(str))
    histories = _extract_histories(root / cfg31["data_policy"]["source_cache"], tickers) if tickers else {}
    recent_bars = int(cfg44.get("pit_lineage", {}).get("close_ledger_recent_bars", 10))
    new_rows = build_close_observations(
        mapping,
        histories,
        cfg31,
        observed_at_utc=generated_at,
        recent_bars=recent_bars,
    )

    existing = _read_csv(close_path)
    before = len(existing)
    combined = pd.concat([existing, new_rows], ignore_index=True, sort=False) if not existing.empty else new_rows.copy()
    if not combined.empty:
        combined["as_of_date"] = combined["as_of_date"].astype(str)
        combined["isin"] = combined["isin"].astype(str).str.upper()
        combined = combined.drop_duplicates(["as_of_date", "isin"], keep="first")
        combined = combined.sort_values(["as_of_date", "isin"])
    _write_csv(combined, close_path)

    payload = {
        "status": "SUCCESS_PIT_CLOSE_LEDGER",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "candidate_mappings": int(len(mapping)),
        "histories_found": int(len(histories)),
        "new_observations_seen": int(len(new_rows)),
        "new_unique_close_rows": int(max(0, len(combined) - before)),
        "ledger_rows": int(len(combined)),
        "recent_bars_examined_per_ticker": recent_bars,
        "local_daily_cache_only": True,
        "network_download_required": False,
        "production_influence": 0.0,
        "output": str(close_path.relative_to(root)),
    }
    audit = root / "outputs" / "audit" / "TCT_V24_4_0_PIT_CLOSE_LEDGER_AUDIT.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
