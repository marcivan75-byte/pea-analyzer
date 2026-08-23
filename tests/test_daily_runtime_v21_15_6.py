from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting import daily_consolidated_runner_v21_15_4 as deployed
from v182.reporting import daily_consolidated_runner_v21_15_7 as final_daily
from v182.reporting import daily_tactical_super_runner_v21_15_6 as tactical
from v182.reporting import daily_w09_seed_v21_15_7 as w09_seed
from v182.reporting import tct_postmarket_bundle_run as postmarket


def test_deployed_entrypoint_routes_to_v21_15_7():
    assert deployed.VERSION == final_daily.VERSION == "DAILY_CONSOLIDATED_RUNTIME_V21_15_7"
    assert deployed.run is final_daily.run


def test_validated_daily_w09_seed_is_run8_and_zero_network():
    contract = w09_seed.audit_contract()
    assert contract["status"] == "VALID"
    assert contract["source_run_id"] == 32626511307
    assert contract["as_of"] == "2026-08-23"
    assert contract["daily_network_calls"] == 0


def test_daily_w09_seed_rehydrates_materialized_action_fields_without_network():
    actions = pd.DataFrame(
        {
            "isin": ["FR0010208488", "ZZ0000000000"],
            "country_yf": ["France", "France"],
            "sector_yf": ["Energy", "Industrials"],
        }
    )
    observations, diagnostics = w09_seed.action_observations(actions)
    fields = {row["field"] for row in observations}
    assert "funnel_global_macro_score" in fields
    assert "funnel_market_sentiment_score" in fields
    assert "funnel_country_macro_score" in fields
    assert "funnel_sector_news_score" in fields
    assert "funnel_instrument_news_score" in fields
    assert "news_catalyst_score" in fields
    assert diagnostics["source_run_id"] == 32626511307
    assert diagnostics["fred_calls"] == 0
    assert diagnostics["gdelt_calls"] == 0
    assert diagnostics["network_calls"] == 0
    assert diagnostics["etf_w09_fabricated"] is False


def test_empty_daily_tct_shadow_keeps_csv_schema():
    frame = tactical.base._empty_tct_shadow_schema()
    assert frame.empty
    required = {
        "asset_class",
        "horizon",
        "isin",
        "score",
        "status",
        "decision",
        "t1_t2_formula_version",
        "t1_t2_score_influence",
        "t1_t2_live_execution_allowed",
    }
    assert required.issubset(frame.columns)


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
        output, _ = postmarket.lineage.apply_lineage(
            frame,
            pd.DataFrame(),
            minimum_snapshot_coverage=0.8,
            labeled_at_utc="x",
            cfg={},
        )
        assert output["pit_label_evaluable"].iloc[0] == False
        return {"status": "SUCCESS"}

    monkeypatch.setattr(postmarket.lineage, "run", fake_run)
    payload = postmarket._run_lineage_dtype_safe(tmp_path)
    assert payload["status"] == "SUCCESS"
    assert observed["dtype"] == "object"
    assert postmarket.lineage.apply_lineage is patched_reference
    assert original is not None
