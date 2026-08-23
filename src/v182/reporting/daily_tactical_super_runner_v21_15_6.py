from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.decision import tct_timing_exact_v24_1_7 as exact_timing
from v182.reporting import daily_tactical_super_runner_v21_15_5 as base


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_TACTICAL_DAG_V21_15_6"


def _build_core_full_decision_shape(root: Path) -> dict:
    """Keep full-universe TCT decision rows while computing exact T1/T2 only on Daily Top-N."""
    core = _ORIGINAL_DAILY_CORE(root)
    baseline_path = root / "outputs" / "daily_tct_ct" / "TCT_BASELINE_V24_1_8.csv"
    baseline = pd.read_csv(baseline_path, sep=";", encoding="utf-8-sig", low_memory=False)
    decisions = core["decisions"].copy()
    existing_tct = set(
        decisions.loc[
            decisions["asset_class"].astype(str).str.upper().eq("ACTION")
            & decisions["horizon"].astype(str).str.upper().eq("TCT"),
            "isin",
        ].astype(str).str.upper()
    )

    placeholders = []
    for _, row in baseline.iterrows():
        isin = str(row.get("isin") or "").upper()
        if not isin or isin in existing_tct:
            continue
        placeholder = exact_timing._snapshot_row(
            row,
            "SKIPPED_DAILY_OUTSIDE_TOPN",
            "NO_T1_T2",
            None,
            0.0,
            {},
            "EXACT_T1_T2_DEFERRED_TO_WEEKLY_OUTSIDE_DAILY_TOPN",
            False,
        )
        placeholder["generated_at_utc"] = core["generated_at"]
        placeholder["live_orders_enabled"] = False
        placeholder["daily_tactical_scope"] = True
        placeholders.append(placeholder)

    if placeholders:
        decisions = pd.concat([decisions, pd.DataFrame(placeholders)], ignore_index=True, sort=False)
    base.base._assert_unique(decisions, "DAILY_CORE_FULL_SHAPE")
    base.base._write(decisions, root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_DECISIONS.csv")
    core["decisions"] = decisions
    core["source_input"] = base.base.attach_master_identity(decisions, core["actions"], core["etfs"])

    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": VERSION,
        "full_tct_decision_rows": int(
            (
                decisions["asset_class"].astype(str).str.upper().eq("ACTION")
                & decisions["horizon"].astype(str).str.upper().eq("TCT")
            ).sum()
        ),
        "exact_tct_engine_rows": int(len(existing_tct)),
        "outside_topn_placeholder_rows": int(len(placeholders)),
        "placeholder_decision": "NO_T1_T2",
        "placeholder_has_score_influence": False,
        "weekly_exact_full_universe_preserved": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }
    (auditdir / "DAILY_TCT_DECISION_SHAPE_V21_15_6.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return core


def _patch_audit_version(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    enriched = dict(payload or {})
    enriched["version"] = VERSION
    enriched["action_ct_daily_latest_isolated"] = True
    enriched["weekly_action_ct_latest_preserved"] = True
    enriched["full_tct_decision_shape_preserved"] = True
    enriched["exact_tct_compute_scope"] = "CURRENT_BASELINE_TOP_N_ONLY"
    text = json.dumps(enriched, ensure_ascii=False, indent=2, default=str)
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_6.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_5.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_4.json").write_text(text, encoding="utf-8")


_ORIGINAL_DAILY_CORE = base._build_core_daily


def run(root: Path = ROOT) -> dict:
    """Final tactical Daily: bounded engines, full decision shape, isolated Daily shadow state."""
    bundle = base.base.tactical.action_ct_bundle
    v220 = bundle.v220
    v221 = bundle.v221
    original_v220_latest = v220.LATEST
    original_v221_latest = v221.LATEST
    original_version = base.VERSION
    original_core = base._build_core_daily

    v220.LATEST = v220.STATE_DIR / "ACTION_CT_V22_0_0_DAILY_LATEST.csv"
    v221.LATEST = v221.STATE_DIR / "ACTION_CT_V22_1_0_DAILY_LATEST.csv"
    base.VERSION = VERSION
    base._build_core_daily = _build_core_full_decision_shape
    try:
        payload = base.run(root=root)
    finally:
        v220.LATEST = original_v220_latest
        v221.LATEST = original_v221_latest
        base.VERSION = original_version
        base._build_core_daily = original_core

    payload = dict(payload or {})
    payload["version"] = VERSION
    payload["action_ct_daily_latest_isolated"] = True
    payload["weekly_action_ct_latest_preserved"] = True
    payload["full_tct_decision_shape_preserved"] = True
    payload["decision_logic_changed"] = False
    payload["criteria_changed"] = False
    payload["weights_changed"] = False
    payload["thresholds_changed"] = False
    _patch_audit_version(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
