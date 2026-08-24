from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import traceback

import pandas as pd

from v182.decision.committee_master import decisions_from_scores, load_registry, tct_adapter
from v182.decision.tct_baseline_v24_1_8 import build_tct_baseline, NORMALIZATION_POLICY
from v182.decision.tct_timing_exact_v24_1_7 import build_exact_timing_snapshot
from v182.decision.tct_v24_1_7 import load_tct_config
from v182.reporting import daily_tct_ct_runner as daily
from v182.reporting import tactical_shadow_bundle_run as tactical
from v182.reporting import tct_postmarket_bundle_run as postmarket
from v182.reporting.selected_source_enrichment import attach_master_identity, enrich_selected_rows
from v182.risk.entry_exit_governance_v21_8 import (
    STATE_RELATIVE_PATH,
    _attach_temporal_state,
    _load_temporal_state,
    _load_temporal_state_observed_at,
    _persist_temporal_state,
    apply_governance,
)


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_TACTICAL_DAG_V21_15_4"
KEYS = ["asset_class", "horizon", "isin"]


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"DAILY_TACTICAL_DAG_INPUT_MISSING:{path}")
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _safe_call(name: str, runner) -> tuple[dict, dict | None, float]:
    started = perf_counter()
    try:
        return runner(), None, perf_counter() - started
    except Exception as exc:
        return {}, {
            "step": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=5),
        }, perf_counter() - started


def _assert_unique(frame: pd.DataFrame, label: str) -> None:
    if not set(KEYS).issubset(frame.columns):
        raise RuntimeError(f"{label}_KEYS_MISSING")
    keys = frame[KEYS].astype(str)
    if keys.duplicated().any():
        raise RuntimeError(f"{label}_DUPLICATE_KEYS")


def _assert_non_authoritative_enrichment(core: pd.DataFrame, enriched: pd.DataFrame) -> None:
    """Selected-source context may add columns but may never alter model authority."""
    _assert_unique(core, "DAILY_CORE")
    _assert_unique(enriched, "DAILY_ENRICHED")
    if len(core) != len(enriched):
        raise RuntimeError("SELECTED_SOURCE_ROW_COUNT_MUTATION_FORBIDDEN")

    left = core[KEYS + [column for column in ("score", "decision") if column in core.columns]].copy()
    right = enriched[KEYS + [column for column in ("score", "decision") if column in enriched.columns]].copy()
    merged = left.merge(right, on=KEYS, how="outer", suffixes=("_core", "_enriched"), indicator=True)
    if not merged["_merge"].eq("both").all():
        raise RuntimeError("SELECTED_SOURCE_KEY_MUTATION_FORBIDDEN")
    for field in ("score", "decision"):
        lcol = f"{field}_core"
        rcol = f"{field}_enriched"
        if lcol not in merged.columns or rcol not in merged.columns:
            continue
        if field == "score":
            lhs = pd.to_numeric(merged[lcol], errors="coerce")
            rhs = pd.to_numeric(merged[rcol], errors="coerce")
            equal = lhs.eq(rhs) | (lhs.isna() & rhs.isna())
        else:
            lhs = merged[lcol].astype("string").fillna("")
            rhs = merged[rcol].astype("string").fillna("")
            equal = lhs.eq(rhs)
        if not bool(equal.all()):
            raise RuntimeError(f"SELECTED_SOURCE_{field.upper()}_MUTATION_FORBIDDEN")


def _build_core(root: Path) -> dict:
    """Build the exact historical TCT/CT model decisions before source context."""
    outputs = root / "outputs"
    outdir = outputs / "daily_tct_ct"
    outdir.mkdir(parents=True, exist_ok=True)

    actions = _read(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etfs = _read(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_ref = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    etf_ref = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    tct_cfg = load_tct_config(root / "config" / "TCT_V24_1_7_SHADOW.json")
    v21_cfg = json.loads((root / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))

    parts: list[pd.DataFrame] = [
        decisions_from_scores(actions, action_ref, "ACTION", ["CT"]),
        decisions_from_scores(etfs, etf_ref, "ETF", ["CT"]),
    ]
    actions_with_tct, baseline = build_tct_baseline(actions, tct_cfg)
    _write(actions_with_tct, outdir / "TCT_BASELINE_V24_1_8.csv")
    tct_state_path = root / str(tct_cfg.get("state", {}).get("path", "state/TCT_V24_1_7_T1_STATE.json"))
    tct_shadow, exact = build_exact_timing_snapshot(
        actions_with_tct,
        root / "data" / "cache" / "actions",
        tct_state_path,
        tct_cfg,
    )
    _write(tct_shadow, outdir / "TCT_SHADOW_V24_1_7.csv")
    parts.append(tct_adapter(tct_shadow))

    decisions = pd.concat([part for part in parts if part is not None and not part.empty], ignore_index=True, sort=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    decisions["generated_at_utc"] = generated_at
    decisions["live_orders_enabled"] = False
    decisions["daily_tactical_scope"] = True
    _assert_unique(decisions, "DAILY_CORE")

    # This core file intentionally exists before source enrichment: Action CT and
    # TCT only consume model score/decision/preselection fields. The enriched
    # version replaces it after those readers have completed.
    _write(decisions, outdir / "DAILY_TCT_CT_DECISIONS.csv")
    source_input = attach_master_identity(decisions, actions, etfs)
    return {
        "actions": actions,
        "etfs": etfs,
        "decisions": decisions,
        "source_input": source_input,
        "generated_at": generated_at,
        "baseline": baseline,
        "exact": exact,
        "v21_cfg": v21_cfg,
    }


def _govern(enriched: pd.DataFrame, root: Path) -> tuple[pd.DataFrame, int]:
    state_path = root / STATE_RELATIVE_PATH
    previous = _load_temporal_state(state_path)
    previous_observed_at = _load_temporal_state_observed_at(state_path)
    with_state = _attach_temporal_state(enriched, previous, previous_observed_at)
    governed = apply_governance(with_state, json.loads((root / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8")))
    return governed, _persist_temporal_state(governed, state_path)


def _daily_payload(core: dict, governed: pd.DataFrame, state_rows: int, source_context: dict) -> dict:
    baseline = core["baseline"]
    exact = core["exact"]
    return {
        "status": "SUCCESS",
        "version": daily.VERSION,
        "runtime_orchestration_version": VERSION,
        "generated_at_utc": core["generated_at"],
        "scope": ["ACTION_TCT", "ACTION_CT", "ETF_CT"],
        "rows": int(len(governed)),
        "rows_by_asset_horizon": governed.groupby(["asset_class", "horizon"], dropna=False).size().reset_index(name="count").to_dict("records"),
        "selected_source_context": source_context,
        "tct_baseline": {
            "universe_rows": baseline.universe_rows,
            "ranked_rows": baseline.ranked_rows,
            "top20_rows": baseline.top20_rows,
            "normalization_policy": NORMALIZATION_POLICY,
        },
        "tct_exact": {
            "histories_found": exact.histories_found,
            "histories_usable": exact.histories_usable,
            "t1_detected_raw": exact.t1_detected_raw,
            "t2_confirmed": exact.t2_confirmed,
        },
        "entry_exit_v21_8": {
            "position_states": governed["v21_8_position_state"].value_counts(dropna=False).to_dict(),
            "entry_states": governed["v21_8_entry_state"].value_counts(dropna=False).to_dict(),
            "temporal_state_rows": int(state_rows),
            "same_day_rerun_can_confirm_exit": False,
        },
        "weights_unchanged": True,
        "selection_thresholds_unchanged": True,
        "holdout_opened": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "fixed_take_profit_enabled": False,
        "legacy_fixed_stop_enabled": False,
        "real_orders_enabled": False,
        "heavy_modules_executed": [],
        "outputs": {
            "decisions": "outputs/daily_tct_ct/DAILY_TCT_CT_DECISIONS.csv",
            "entry_exit": "outputs/daily_tct_ct/DAILY_TCT_CT_V21_8.csv",
            "android": "outputs/mobile/ANDROID_DAILY_TCT_CT.md",
            "source_context": "outputs/source_context/DAILY_TCT_CT_SOURCE_OBSERVATIONS.csv",
        },
    }


def run(root: Path = ROOT) -> dict:
    """Execute the daily tactical path as a dependency DAG, not a serial list."""
    started = perf_counter()
    core_started = perf_counter()
    core = _build_core(root)
    core_seconds = perf_counter() - core_started
    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    auditdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)

    postmarket_future: Future | None = None
    postmarket_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-postmarket")

    def release_postmarket(_tct_payload: dict, _tct_error: dict | None) -> None:
        nonlocal postmarket_future
        if postmarket_future is None:
            postmarket_future = postmarket_executor.submit(
                _safe_call,
                "POSTMARKET_V24.4.2",
                lambda: postmarket.run(root=root),
            )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="daily-dag") as pool:
        selected_future = pool.submit(
            _safe_call,
            "SELECTED_SOURCE_ENRICHMENT",
            lambda: enrich_selected_rows(core["source_input"], root, profile="DAILY_TCT_CT"),
        )
        tactical_future = pool.submit(
            _safe_call,
            "TACTICAL_SHADOW_DAG",
            lambda: tactical.run(root=root, tct_complete_callback=release_postmarket),
        )

        selected_result, selected_error, selected_seconds = selected_future.result()
        if selected_error is not None:
            # This step was blocking in the historical daily runner. Keep it
            # blocking for temporal-state authority even though SHADOW work may
            # already have progressed in parallel.
            tactical_result, tactical_error, tactical_seconds = tactical_future.result()
            if postmarket_future is not None:
                postmarket_future.result()
            postmarket_executor.shutdown(wait=True, cancel_futures=False)
            raise RuntimeError(
                "DAILY_SELECTED_SOURCE_ENRICHMENT_FAILED:"
                f"{selected_error['type']}:{selected_error['message']}"
            )

        enriched, source_context = selected_result
        _assert_non_authoritative_enrichment(core["decisions"], enriched)

        governance_started = perf_counter()
        governed, state_rows = _govern(enriched, root)
        governance_seconds = perf_counter() - governance_started

        tactical_result, tactical_error, tactical_seconds = tactical_future.result()

    if postmarket_future is None:
        # Defensive fallback: callback should run after every attempted TCT branch.
        postmarket_future = postmarket_executor.submit(
            _safe_call,
            "POSTMARKET_V24.4.2",
            lambda: postmarket.run(root=root),
        )
    postmarket_result, postmarket_error, postmarket_seconds = postmarket_future.result()
    postmarket_executor.shutdown(wait=True, cancel_futures=False)

    # TCT has now completed its read of the core decisions, so replacing the file
    # with the fully enriched form cannot race its preselection reader.
    _write(enriched, outdir / "DAILY_TCT_CT_DECISIONS.csv")
    _write(governed, outdir / "DAILY_TCT_CT_V21_8.csv")
    android_path = mobile / "ANDROID_DAILY_TCT_CT.md"
    android_path.write_text(daily._android_summary(governed, core["generated_at"]), encoding="utf-8")

    daily_payload = _daily_payload(core, governed, state_rows, source_context)
    (auditdir / "DAILY_TCT_CT_AUDIT.json").write_text(
        json.dumps(daily_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    nonblocking_errors = [error for error in (tactical_error, postmarket_error) if error is not None]
    payload = {
        "status": "SUCCESS_DAILY_TACTICAL_DAG" if not nonblocking_errors else "SUCCESS_DAILY_TACTICAL_DAG_WITH_SHADOW_ERRORS",
        "version": VERSION,
        "daily_core_version": daily.VERSION,
        "generated_at_utc": core["generated_at"],
        "selected_source_overlapped_with_tactical_models": True,
        "postmarket_released_on_tct_completion": True,
        "postmarket_waits_for_action_ct": False,
        "selected_source_decision_influence": False,
        "selected_source_score_influence": 0.0,
        "governance_runs_after_selected_source_success": True,
        "final_decision_file_written_after_tct_reader_completed": True,
        "weights_changed": False,
        "thresholds_changed": False,
        "criteria_changed": False,
        "decision_logic_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
        "timings_seconds": {
            "core": round(float(core_seconds), 6),
            "selected_source": round(float(selected_seconds), 6),
            "governance": round(float(governance_seconds), 6),
            "tactical_bundle": round(float(tactical_seconds), 6),
            "postmarket": round(float(postmarket_seconds), 6),
            "total": round(float(perf_counter() - started), 6),
        },
        "critical_path_policy": "CORE_THEN_MAX(SELECTED_SOURCE_PLUS_GOVERNANCE,TACTICAL_WITH_TCT_RELEASED_POSTMARKET)",
        "tactical": {
            "status": tactical_result.get("status"),
            "version": tactical_result.get("version"),
        },
        "postmarket": {
            "status": postmarket_result.get("status"),
            "version": postmarket_result.get("version"),
        },
        "nonblocking_errors": nonblocking_errors,
    }
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_4.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
