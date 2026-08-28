"""Publication Daily O/R SHADOW — fail-closed, influence 0.

Réutilise le ranking HEBDO challenger si un LATEST Daily est présent.
Ne recalcule aucun poids de sélection. No-op propre sinon.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CHALLENGER = Path("outputs/committee_master/OBJECTIVES_RISK_CHALLENGER_V2.csv")
DAILY_CANDIDATES = (
    Path("outputs/action_ct/ACTION_CT_V22_1_0_DAILY_LATEST.csv"),
    Path("outputs/committee_master/ACTION_CT_V22_1_0_DAILY_LATEST.csv"),
    Path("outputs/action_ct/ACTION_CT_V22_0_0_DAILY_LATEST.csv"),
    Path("outputs/tct/TCT_DAILY_TRADER_LATEST.csv"),
)
AUDIT = Path("outputs/audit/OR_RANKING_DAILY_SHADOW_V1.json")
MIN_HISTORY_SESSIONS = 250


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _first_existing(root: Path) -> Path | None:
    for relative in DAILY_CANDIDATES:
        path = root / relative
        if path.exists() and path.stat().st_size:
            return path
    return None


def run(root: Path = ROOT) -> dict:
    generated = datetime.now(timezone.utc).isoformat()
    date = datetime.now(timezone.utc).date().isoformat()
    out = root / f"outputs/committee_master/OR_RANKING_DAILY_SHADOW_{date}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    (root / AUDIT).parent.mkdir(parents=True, exist_ok=True)

    daily_path = _first_existing(root)
    challenger = _read(root / CHALLENGER)
    if daily_path is None or challenger.empty:
        payload = {
            "status": "SKIPPED_NO_DAILY_INPUT",
            "generated_at_utc": generated,
            "shadow_only": True,
            "real_orders_enabled": False,
            "score_influence": 0.0,
            "rows": 0,
            "daily_input": None if daily_path is None else str(daily_path.relative_to(root)),
            "criteria_changed": False,
            "weights_changed": False,
        }
        (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    daily = _read(daily_path)
    if "isin" not in daily or "isin" not in challenger:
        payload = {
            "status": "SKIPPED_MISSING_ISIN",
            "generated_at_utc": generated,
            "shadow_only": True,
            "real_orders_enabled": False,
            "score_influence": 0.0,
            "rows": 0,
        }
        (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    keep_daily = [col for col in ("isin", "history_sessions", "staleness_days", "coverage_pct") if col in daily]
    merged = challenger.merge(daily[keep_daily].drop_duplicates("isin"), on="isin", how="inner")
    if "history_sessions" in merged:
        history = pd.to_numeric(merged["history_sessions"], errors="coerce")
        merged = merged[history.isna() | history.ge(MIN_HISTORY_SESSIONS)].copy()

    preferred = [
        "name", "isin", "asset_class", "horizon",
        "OR_SELECTION_SCORE_0_100", "OR_RR_SCORE_0_100", "OR_RELIABILITY_0_100",
        "OR_RISK_VERDICT", "OR_RISK_SOFT_MULT", "OR_COMPOSITE_SHADOW",
        "OR_HEBDO_LABEL", "OR_ENTRY_ACTION_SHADOW", "OR_HEBDO_GATE_REASON",
        "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "SIM_RELIABILITY",
        "CI_CONFIDENCE_SCORE_0_100", "OR_AS_OF_CLOSE", "OR_PROVENANCE_QUALITY",
        "OR_DATA_CONTRACT_STATUS",
    ]
    columns = [col for col in preferred if col in merged]
    if "OR_COMPOSITE_SHADOW" in merged:
        merged = merged.sort_values("OR_COMPOSITE_SHADOW", ascending=False, na_position="last")
    merged[columns].to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS",
        "generated_at_utc": generated,
        "shadow_only": True,
        "real_orders_enabled": False,
        "score_influence": 0.0,
        "rows": int(len(merged)),
        "daily_input": str(daily_path.relative_to(root)),
        "output": str(out.relative_to(root)),
        "minimum_history_sessions": MIN_HISTORY_SESSIONS,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
