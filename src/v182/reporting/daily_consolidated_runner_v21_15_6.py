from __future__ import annotations

from pathlib import Path
import json

from v182.reporting import daily_consolidated_runner_v21_15_5 as base
from v182.reporting import daily_tactical_super_runner_v21_15_6 as tactical


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_6"


def _require_valid_daily_seed(actions, etf, manifest, mode):
    """Forbid a Daily full fallback that would silently omit weekly-only W09 values."""
    if mode == "DISABLED" or actions is None or etf is None or actions.empty or etf.empty:
        raise RuntimeError(
            "DAILY_WEEKLY_BASELINE_MISSING: run Weekly Heavy once to seed the validated "
            "W09/master snapshot before Daily V21.15.6"
        )
    return actions, etf, manifest, mode


def _guarded_loader():
    return _require_valid_daily_seed(*base._load_fast_state_compatible())


def _patch_audit(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    enriched = dict(payload or {})
    enriched["version"] = VERSION
    enriched["daily_seed_policy"] = {
        "weekly_w09_seed_required": True,
        "silent_full_fallback_without_w09_forbidden": True,
        "failure_mode": "FAIL_CLOSED",
    }
    enriched["tactical_runtime_version"] = tactical.VERSION
    text = json.dumps(enriched, ensure_ascii=False, indent=2, default=str)
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_6.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_5.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_4.json").write_text(text, encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    """Final Daily runtime: weekly W09 seed required, bounded tactical work, functional cache identity."""
    original_loader = base._load_fast_state_compatible
    original_tactical = base.tactical
    original_version = base.VERSION

    base._load_fast_state_compatible = _guarded_loader
    base.tactical = tactical
    base.VERSION = VERSION
    try:
        payload = base.run(root=root)
    finally:
        base._load_fast_state_compatible = original_loader
        base.tactical = original_tactical
        base.VERSION = original_version

    payload = dict(payload or {})
    payload["version"] = VERSION
    payload["tactical_runtime_version"] = tactical.VERSION
    payload["weekly_w09_seed_required"] = True
    payload["silent_daily_w09_loss_forbidden"] = True
    payload["decision_logic_changed"] = False
    payload["criteria_changed"] = False
    payload["weights_changed"] = False
    payload["thresholds_changed"] = False
    _patch_audit(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
