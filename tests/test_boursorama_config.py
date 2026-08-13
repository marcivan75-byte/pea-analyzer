from __future__ import annotations

import json
from pathlib import Path


def test_boursorama_config_is_high_priority_attributed_and_weight_neutral():
    root=Path(__file__).resolve().parents[1]
    cfg=json.loads((root/"config"/"V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    spec=cfg["boursorama_import"]
    assert spec["enabled"] is True
    assert spec["priority"] == "HIGH"
    assert spec["direct_automated_fetch"] is False
    assert spec["evidence_level"] == "B"
    assert spec["missing_policy"] == "NO_IMPUTATION"
    assert spec["bulk_consensus_pages_supported"] is True
    assert spec["action_single_title_pages_supported"] is True
    assert spec["etf_morningstar_pages_supported"] is True
    for field in ("consensus_score_100_v21","consensus_delta_4w","target_upside_pct_v21","per_forward_v21"):
        assert field in spec["action_canonical_fields"]
    for field in ("morningstar_rating","morningstar_category","risk_indicator"):
        assert field in spec["etf_canonical_fields"]
