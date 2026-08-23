from __future__ import annotations

from pathlib import Path
import json

from v182.reporting import daily_tactical_super_runner_v21_15_5 as base


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_TACTICAL_DAG_V21_15_6"


def _patch_audit_version(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    enriched = dict(payload or {})
    enriched["version"] = VERSION
    enriched["action_ct_daily_latest_isolated"] = True
    enriched["weekly_action_ct_latest_preserved"] = True
    text = json.dumps(enriched, ensure_ascii=False, indent=2, default=str)
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_6.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_5.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_4.json").write_text(text, encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    """Execute V21.15.5 Daily scope without overwriting weekly full-universe LATEST files."""
    bundle = base.base.tactical.action_ct_bundle
    v220 = bundle.v220
    v221 = bundle.v221
    original_v220_latest = v220.LATEST
    original_v221_latest = v221.LATEST
    original_version = base.VERSION

    v220.LATEST = v220.STATE_DIR / "ACTION_CT_V22_0_0_DAILY_LATEST.csv"
    v221.LATEST = v221.STATE_DIR / "ACTION_CT_V22_1_0_DAILY_LATEST.csv"
    base.VERSION = VERSION
    try:
        payload = base.run(root=root)
    finally:
        v220.LATEST = original_v220_latest
        v221.LATEST = original_v221_latest
        base.VERSION = original_version

    payload = dict(payload or {})
    payload["version"] = VERSION
    payload["action_ct_daily_latest_isolated"] = True
    payload["weekly_action_ct_latest_preserved"] = True
    payload["decision_logic_changed"] = False
    payload["criteria_changed"] = False
    payload["weights_changed"] = False
    payload["thresholds_changed"] = False
    _patch_audit_version(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
