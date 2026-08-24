from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
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


def _attach_context_to_precomputed_governance(
    core_decisions: pd.DataFrame,
    enriched: pd.DataFrame,
    governed_core: pd.DataFrame,
) -> pd.DataFrame:
    """Reattach source-only columns after governance computed from authoritative core.

    Selected-source enrichment is contractually forbidden from mutating row keys,
    score or decision. V21.8 governance reads only authoritative model/timing/risk
    fields already present in the core. Running it on the core therefore produces
    identical governance columns; source-only context is then joined back 1:1.
    """
    base.base._assert_non_authoritative_enrichment(core_decisions, enriched)
    keys = list(base.base.KEYS)
    source_only = [column for column in enriched.columns if column not in core_decisions.columns]
    governance_only = [column for column in governed_core.columns if column not in core_decisions.columns]
    if source_only:
        context = enriched[keys + source_only].copy()
        result = governed_core.merge(context, on=keys, how="left", validate="one_to_one")
    else:
        result = governed_core.copy()
    expected_columns = list(enriched.columns) + [column for column in governance_only if column not in enriched.columns]
    result = result[expected_columns]
    base.base._assert_non_authoritative_enrichment(enriched, result)
    return result


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
    original_govern = base.base._govern

    governance_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-governance")
    governance_state: dict = {
        "future": None,
        "core_decisions": None,
        "compute_seconds": 0.0,
        "fallback_serial": False,
    }

    def governance_job(decisions: pd.DataFrame, target_root: Path):
        started = perf_counter()
        try:
            return original_govern(decisions, target_root)
        finally:
            governance_state["compute_seconds"] = round(perf_counter() - started, 6)

    def build_core_and_start_governance(target_root: Path) -> dict:
        core = _build_core_full_decision_shape(target_root)
        authoritative = core["decisions"].copy()
        governance_state["core_decisions"] = authoritative
        governance_state["future"] = governance_pool.submit(governance_job, authoritative.copy(), target_root)
        return core

    def govern_from_precomputed(enriched: pd.DataFrame, target_root: Path):
        future = governance_state.get("future")
        authoritative = governance_state.get("core_decisions")
        if future is None or authoritative is None:
            governance_state["fallback_serial"] = True
            return original_govern(enriched, target_root)
        governed_core, state_rows = future.result()
        governed = _attach_context_to_precomputed_governance(authoritative, enriched, governed_core)
        return governed, state_rows

    v220.LATEST = v220.STATE_DIR / "ACTION_CT_V22_0_0_DAILY_LATEST.csv"
    v221.LATEST = v221.STATE_DIR / "ACTION_CT_V22_1_0_DAILY_LATEST.csv"
    base.VERSION = VERSION
    base._build_core_daily = build_core_and_start_governance
    base.base._govern = govern_from_precomputed
    try:
        payload = base.run(root=root)
    finally:
        governance_pool.shutdown(wait=True, cancel_futures=False)
        v220.LATEST = original_v220_latest
        v221.LATEST = original_v221_latest
        base.VERSION = original_version
        base._build_core_daily = original_core
        base.base._govern = original_govern

    payload = dict(payload or {})
    payload["version"] = VERSION
    payload["action_ct_daily_latest_isolated"] = True
    payload["weekly_action_ct_latest_preserved"] = True
    payload["full_tct_decision_shape_preserved"] = True
    payload["governance_overlapped_with_selected_source"] = not bool(governance_state["fallback_serial"])
    payload["governance_compute_seconds"] = float(governance_state["compute_seconds"])
    payload["governance_serial_fallback"] = bool(governance_state["fallback_serial"])
    payload["governance_source_context_dependency"] = False
    payload["decision_logic_changed"] = False
    payload["criteria_changed"] = False
    payload["weights_changed"] = False
    payload["thresholds_changed"] = False
    _patch_audit_version(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
