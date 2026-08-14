from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import pandas as pd

from v182.io.frames import apply_observations, load_master, save_master
from v182.sources.amf_short_positions import AMF_CURRENT_RESOURCE_URL, fetch_amf_short_positions
from v182.sources.finnhub_cdc import fetch_cdc_observations

ROOT = Path(__file__).resolve().parents[3]


def _write_failures(path: Path, failures: list[dict]) -> None:
    if failures:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(failures).to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def run(root: Path = ROOT) -> dict:
    """Enrich the current Action master with CDC sources before Committee scoring."""
    source = root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    if not source.exists():
        raise FileNotFoundError("CURRENT_ACTION_MASTER_NOT_FOUND_AFTER_REFRESH")
    actions = load_master(source)
    failures: list[dict] = []
    quarantine: list[dict] = []
    counts: dict[str, int] = {}

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if finnhub_key:
        observations, failed = fetch_cdc_observations(
            actions,
            finnhub_key,
            root / "state" / "finnhub" / "EPS_ESTIMATE_HISTORY.csv",
            horizon_days=90,
        )
        actions, rejected = apply_observations(actions, observations)
        counts["finnhub_observations"] = len(observations)
        failures.extend(failed)
        quarantine.extend(rejected)
    else:
        counts["finnhub_observations"] = 0
        failures.append({"source": "Finnhub", "reason": "FINNHUB_API_KEY_MISSING", "scope": "EARNINGS_CALENDAR_EPS_REVISION"})

    amf_observations, amf_failed = fetch_amf_short_positions(actions, url=AMF_CURRENT_RESOURCE_URL)
    actions, amf_rejected = apply_observations(actions, amf_observations)
    counts["amf_observations"] = len(amf_observations)
    failures.extend(amf_failed)
    quarantine.extend(amf_rejected)

    save_master(actions, source)
    gaps = root / "outputs" / "gaps"
    _write_failures(gaps / "V21_6_3_CDC_SOURCE_FAILURES.csv", failures)
    _write_failures(gaps / "V21_6_3_CDC_MERGE_QUARANTINE.csv", quarantine)

    payload = {
        "status": "SUCCESS",
        "version": "V21.6.3_CDC",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **counts,
        "source_failures": len(failures),
        "merge_quarantined": len(quarantine),
        "outputs": {
            "action_master": "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",
            "eps_history": "state/finnhub/EPS_ESTIMATE_HISTORY.csv",
            "failures": "outputs/gaps/V21_6_3_CDC_SOURCE_FAILURES.csv",
        },
        "governance": {
            "eps_revision_same_fiscal_period_pit_only": True,
            "first_eps_snapshot_has_no_revision": True,
            "amf_absence_never_imputed_to_zero": True,
            "amf_evidence_level": "A",
            "new_cdc_fields_are_observed_inputs_not_new_optimised_weights": True,
        },
    }
    audit = root / "outputs" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "V21_6_3_CDC_REFRESH.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
