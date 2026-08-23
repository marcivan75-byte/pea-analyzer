from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting import run as legacy

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_ENRICHMENT_FAST_OUTPUTS_V21_16_1"


def run() -> dict:
    """Run the complete weekly collector with lean, lossless audit serialization.

    Network collection, universes, observations, quality gates and enriched CSV
    masters are unchanged. Only redundant heavy XLSX serialization is removed:
    intermediate per-wave audit workbooks and three raw workbooks that are not
    consumed downstream nor uploaded by the weekly workflow. The final collection
    audit XLSX remains published.
    """
    original_audit = legacy._audit
    original_exports = legacy._export_excel_reports

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

    legacy._audit = lean_audit
    legacy._export_excel_reports = skip_unused_raw_excel
    try:
        payload = legacy.run()
    finally:
        legacy._audit = original_audit
        legacy._export_excel_reports = original_exports

    payload["weekly_output_optimization"] = {
        "version": VERSION,
        "network_collection_changed": False,
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
    }
    return payload


if __name__ == "__main__":
    import json

    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
