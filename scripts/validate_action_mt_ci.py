from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from v182.decision.action_mt_decision_v1 import ActionCandidate, MarketRegime, select_action_mt_candidates, validate_decision_contract


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "ACTION_MT_V1_0_0_SHADOW.json"
REPORT = ROOT / "outputs" / "audit" / "ACTION_MT_CI_DECISION.json"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    issues = validate_decision_contract(cfg)
    probe = ActionCandidate("CI-PROBE", 90.0, 100.0, "CI", "ENTRY_STRONG_SHADOW", 1.0)
    favorable = select_action_mt_candidates([probe], MarketRegime(0.65, 0.0, 5.0, True), cfg)
    adverse = select_action_mt_candidates([probe], MarketRegime(0.35, -3.0, -8.0, False), cfg)
    if [item.isin for item in favorable.selected] != ["CI-PROBE"]:
        issues.append("FAVORABLE_REGIME_SELECTION_PROBE_FAILED")
    if adverse.selected or adverse.abstention_reason != "MARKET_REGIME_BLOCK":
        issues.append("ADVERSE_REGIME_ABSTENTION_PROBE_FAILED")

    report = {
        "version": cfg.get("version"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
        "checks": {
            "configuration_contract": not validate_decision_contract(cfg),
            "favorable_regime_selects": bool(favorable.selected),
            "adverse_regime_abstains": not adverse.selected,
            "real_orders_disabled": cfg["governance"]["real_orders_enabled"] is False,
            "holdout_locked": cfg["governance"]["holdout_locked"] is True,
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())

