from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from v182.reporting import daily_consolidated_runner_v21_15_4 as deployed
from v182.reporting import daily_consolidated_runner_v21_15_6 as consolidated
from v182.reporting import daily_tactical_super_runner_v21_15_6 as tactical
from v182.reporting import tct_postmarket_bundle_run as postmarket


def test_deployed_entrypoint_routes_to_v21_15_6():
    assert deployed.VERSION == consolidated.VERSION == "DAILY_CONSOLIDATED_RUNTIME_V21_15_6"
    assert deployed.run is consolidated.run


def test_daily_seed_guard_fails_closed_without_weekly_or_daily_state():
    with pytest.raises(RuntimeError, match="DAILY_WEEKLY_BASELINE_MISSING"):
        consolidated._require_valid_daily_seed(pd.DataFrame(), pd.DataFrame(), {}, "DISABLED")


def test_daily_seed_guard_accepts_valid_reconcile_state():
    actions = pd.DataFrame({"isin": ["A"]})
    etf = pd.DataFrame({"isin": ["E"]})
    result = consolidated._require_valid_daily_seed(actions, etf, {"source": "WEEKLY_MASTER_SNAPSHOT_V1"}, "RECONCILE_CACHE")
    assert result[3] == "RECONCILE_CACHE"


def test_daily_action_ct_latest_isolated_and_restored(monkeypatch, tmp_path: Path):
    bundle = tactical.base.base.tactical.action_ct_bundle
    v220, v221 = bundle.v220, bundle.v221
    old220, old221 = v220.LATEST, v221.LATEST

    def fake_run(root):
        assert v220.LATEST.name == "ACTION_CT_V22_0_0_DAILY_LATEST.csv"
        assert v221.LATEST.name == "ACTION_CT_V22_1_0_DAILY_LATEST.csv"
        return {"status": "SUCCESS", "version": "FAKE"}

    monkeypatch.setattr(tactical.base, "run", fake_run)
    payload = tactical.run(tmp_path)
    assert payload["action_ct_daily_latest_isolated"] is True
    assert v220.LATEST == old220
    assert v221.LATEST == old221


def test_postmarket_lineage_storage_cast_is_restored(monkeypatch, tmp_path: Path):
    original = postmarket.lineage.apply_lineage
    observed = {"dtype": None}

    def fake_apply(catalyst_ledger, ohlc_ledger_frame, **kwargs):
        observed["dtype"] = str(catalyst_ledger["pit_label_evaluable"].dtype)
        catalyst_ledger.loc[:, "pit_label_evaluable"] = False
        return catalyst_ledger, {"fingerprint_mismatches": 0}

    monkeypatch.setattr(postmarket.lineage, "apply_lineage", fake_apply)
    patched_reference = postmarket.lineage.apply_lineage

    def fake_run(root):
        frame = pd.DataFrame({"pit_label_evaluable": [float("nan")]})
        output, _ = postmarket.lineage.apply_lineage(frame, pd.DataFrame(), minimum_snapshot_coverage=0.8, labeled_at_utc="x", cfg={})
        assert output["pit_label_evaluable"].iloc[0] == False
        return {"status": "SUCCESS"}

    monkeypatch.setattr(postmarket.lineage, "run", fake_run)
    payload = postmarket._run_lineage_dtype_safe(tmp_path)
    assert payload["status"] == "SUCCESS"
    assert observed["dtype"] == "object"
    assert postmarket.lineage.apply_lineage is patched_reference
    assert original is not None
