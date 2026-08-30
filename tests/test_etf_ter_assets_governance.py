from v182.etf.ter_assets_governance import assess_ter_assets_gate, proof_weight


def test_blocks_expensive_small_fund():
    result = assess_ter_assets_gate(0.61, 99.0, proof_tier="A")
    assert result.status == "BLOCK_DATA"
    assert result.reason == "TER_GT_0_60_AND_ASSETS_LT_100M_EUR"
    assert result.proof_weight == 1.0


def test_does_not_block_when_only_one_threshold_is_bad():
    assert assess_ter_assets_gate(0.61, 100.0, proof_tier="A").status == "ELIGIBLE_DATA"
    assert assess_ter_assets_gate(0.60, 99.0, proof_tier="B").status == "ELIGIBLE_DATA"


def test_missing_assets_fail_closed_without_fx_estimation():
    result = assess_ter_assets_gate(0.55, None, proof_tier="A")
    assert result.status == "BLOCK_DATA"
    assert result.reason == "MISSING_OR_INVALID_TER_OR_EUR_ASSETS"


def test_proof_weights_are_governed():
    assert proof_weight("A") == 1.0
    assert proof_weight("B") == 0.6
    assert proof_weight("C") == 0.0
