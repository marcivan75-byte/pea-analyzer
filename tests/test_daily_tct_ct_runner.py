from pathlib import Path

from v182.reporting import daily_tct_ct_runner


def test_daily_runner_scope_excludes_heavy_modules():
    source = Path(daily_tct_ct_runner.__file__).read_text(encoding="utf-8")
    assert 'decisions_from_scores(actions, action_ref, "ACTION", ["CT"])' in source
    assert 'decisions_from_scores(etfs, etf_ref, "ETF", ["CT"])' in source
    assert "build_tct_baseline" in source
    assert "build_exact_timing_snapshot" in source
    assert "sector_rotation_v2" not in source
    assert "beta_correlation_engine" not in source
    assert "ipo_radar" not in source
    assert "etf_mt_v2081" not in source
    assert '"heavy_modules_executed": []' in source


def test_daily_runner_preserves_governance_guards():
    source = Path(daily_tct_ct_runner.__file__).read_text(encoding="utf-8")
    assert "apply_governance" in source
    assert "_load_temporal_state" in source
    assert "_persist_temporal_state" in source
    assert '"weights_unchanged": True' in source
    assert '"selection_thresholds_unchanged": True' in source
    assert '"holdout_opened": False' in source
    assert '"t1_t2_scope": "ACTION_TCT_ONLY"' in source
    assert '"fixed_take_profit_enabled": False' in source
    assert '"legacy_fixed_stop_enabled": False' in source
    assert '"real_orders_enabled": False' in source
