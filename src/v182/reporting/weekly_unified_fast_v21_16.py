from __future__ import annotations

from pathlib import Path
import json

from v182.reporting import unified_runner
from v182.reporting import weekly_enrichment_fast_v21_16

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_FAST_V21_16_1"


def run(root: Path = ROOT) -> dict:
    """Use the optimized full collector only for the weekly unified pipeline."""
    original = unified_runner.enrichment_run
    unified_runner.enrichment_run = weekly_enrichment_fast_v21_16
    try:
        payload = unified_runner.run(root)
    finally:
        unified_runner.enrichment_run = original
    payload.setdefault("runtime", {})["weekly_enrichment_output_profile"] = VERSION
    payload["runtime"]["unused_raw_collection_xlsx_skipped"] = True
    payload["runtime"]["final_collection_audit_xlsx_retained"] = True
    return payload


def main() -> None:
    payload = run(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if payload.get("status") == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
