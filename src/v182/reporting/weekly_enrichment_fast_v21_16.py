from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v182.features import action_decision_enhancements as action_enhancements
from v182.features import sector_rotation as sector_rotation
from v182.reporting import run as legacy
from v182.reporting import waves

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_ENRICHMENT_FAST_OUTPUTS_V21_16_2"


def _restamp_internal_observations(observations: list[dict]) -> list[dict]:
    """Stamp precomputed local observations at their historical application point.

    WAVE10/WAVE11 historically construct their local observations after WAVE09.
    The optimized collector computes them while WAVE09 waits on FRED/GDELT, but
    applies them in the original order. Re-stamping here preserves the effective
    collection/as-of semantics rather than exposing the earlier speculative CPU
    start time in provenance.
    """
    if not observations:
        return observations
    stamp = datetime.now(timezone.utc).isoformat()
    day = stamp[:10]
    for row in observations:
        row["collected_at"] = stamp
        row["as_of"] = day
    return observations


def run() -> dict:
    """Run the complete weekly collector with lean serialization and safe I/O overlap.

    Network collection, universes, observations, quality gates and enriched CSV
    masters are unchanged. Redundant XLSX serialization is removed. In addition,
    WAVE10 sector rotation and WAVE11 Action enhancement CPU work are precomputed
    from the exact post-WAVE08 snapshot while WAVE09 waits on independent FRED/
    GDELT network I/O. Their observations are still applied in historical order
    WAVE09 -> WAVE10 -> WAVE11, with application-time provenance timestamps.
    """
    original_audit = legacy._audit
    original_exports = legacy._export_excel_reports
    original_wave9 = waves.wave9_topdown
    original_rotation = sector_rotation.build_rotation_observations
    original_enhancements = action_enhancements.build_action_enhancement_observations
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="weekly-local-under-topdown")
    pending: dict[str, Future] = {}
    overlap_started = {"rotation": False, "action_enhancements": False}
    overlap_reused = {"rotation": False, "action_enhancements": False}

    def lean_audit(
        actions: pd.DataFrame,
        etfs: pd.DataFrame,
        wave_id: str,
        *,
        failures: list[dict] | None = None,
        source_context: str = "",
    ) -> None:
        legacy.write_collection_audit(
            actions,
            etfs,
            wave_id,
            legacy.DATA_AUDIT,
            failures=failures,
            source_context=source_context,
            write_excel=wave_id == "WAVE_99_FINAL",
        )

    def skip_unused_raw_excel(
        actions: pd.DataFrame,
        etfs: pd.DataFrame,
        before: dict,
        after: dict,
        quality_checks: list[dict],
        run_profile: str,
    ) -> bool:
        del actions, etfs, before, after, quality_checks, run_profile
        return False

    def overlapped_wave9(
        actions_df: pd.DataFrame,
        etf_df: pd.DataFrame,
        cfg: dict,
        fred_api_key: str | None,
    ):
        # WAVE08 has completed at this point. WAVE10 consumes only OHLCV/sector
        # fields and WAVE11 only Morningstar/target/dividend/consensus fields;
        # neither consumes any WAVE09 output. Immutable copies therefore retain
        # the exact relevant input snapshot while local CPU work hides under I/O.
        pending["rotation"] = pool.submit(original_rotation, actions_df.copy())
        pending["action_enhancements"] = pool.submit(original_enhancements, actions_df.copy())
        overlap_started["rotation"] = True
        overlap_started["action_enhancements"] = True
        return original_wave9(actions_df, etf_df, cfg, fred_api_key)

    def reused_rotation(actions_df: pd.DataFrame, *, cfg: dict | None = None):
        future = pending.pop("rotation", None)
        if future is None:
            return original_rotation(actions_df, cfg=cfg)
        observations, sectors, diagnostic = future.result()
        overlap_reused["rotation"] = True
        return _restamp_internal_observations(observations), sectors, diagnostic

    def reused_enhancements(actions_df: pd.DataFrame):
        future = pending.pop("action_enhancements", None)
        if future is None:
            return original_enhancements(actions_df)
        observations = future.result()
        overlap_reused["action_enhancements"] = True
        return _restamp_internal_observations(observations)

    legacy._audit = lean_audit
    legacy._export_excel_reports = skip_unused_raw_excel
    waves.wave9_topdown = overlapped_wave9
    sector_rotation.build_rotation_observations = reused_rotation
    action_enhancements.build_action_enhancement_observations = reused_enhancements
    try:
        payload = legacy.run()
    finally:
        legacy._audit = original_audit
        legacy._export_excel_reports = original_exports
        waves.wave9_topdown = original_wave9
        sector_rotation.build_rotation_observations = original_rotation
        action_enhancements.build_action_enhancement_observations = original_enhancements
        pool.shutdown(wait=True, cancel_futures=False)

    payload["weekly_output_optimization"] = {
        "version": VERSION,
        "network_collection_changed": False,
        "provider_start_policy_changed": False,
        "universe_changed": False,
        "observations_changed": False,
        "quality_gates_changed": False,
        "enriched_csv_masters_changed": False,
        "final_collection_audit_xlsx_retained": True,
        "intermediate_collection_audit_xlsx_skipped": True,
        "intermediate_audit_csv_json_retained": True,
        "unused_raw_master_xlsx_skipped": [
            "outputs/V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
            "outputs/V18.2_PEA_ETF_ACTUALISE.xlsx",
            "outputs/V18.2_RUN_REPORT.xlsx",
        ],
        "committee_ci_outputs_affected": False,
        "topdown_overlap": {
            "network_stage": "WAVE_09_TOPDOWN_FRED_GDELT",
            "local_precomputed_stages": ["WAVE_10_SECTOR_ROTATION", "WAVE_11_ACTION_DECISION_FACTORS"],
            "rotation_started": overlap_started["rotation"],
            "rotation_reused_at_original_stage": overlap_reused["rotation"],
            "action_enhancements_started": overlap_started["action_enhancements"],
            "action_enhancements_reused_at_original_stage": overlap_reused["action_enhancements"],
            "application_order_changed": False,
            "relevant_input_fields_changed": False,
            "provenance_restamped_at_original_application_stage": True,
        },
    }
    return payload


if __name__ == "__main__":
    import json

    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
