from datetime import datetime, timezone

import pandas as pd

from v182.reporting import ci_challenger_publication_v2 as publication


def _write(root, relative, rows):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def test_publishes_dated_combined_and_etf_shadow_rankings(tmp_path):
    rows = [
        {"name": "Action A", "isin": "A", "asset_class": "ACTION", "horizon": "CT", "OR_COMPOSITE_SHADOW": 70, "OR_WEEKLY_SOURCE_GATE": "PASS"},
        {"name": "ETF B", "isin": "B", "asset_class": "ETF", "OR_COMPOSITE_SHADOW": 80, "OR_WEEKLY_SOURCE_GATE": "PASS"},
    ]
    _write(tmp_path, "outputs/committee_master/OBJECTIVES_RISK_CHALLENGER_V2.csv", rows)
    _write(tmp_path, "outputs/committee_master/CI_SELECTION_ALL_V4.csv", [{"name": "Action A", "isin": "A"}])
    _write(tmp_path, "outputs/committee_master/CI_LIGHT_V4.csv", [{"name": "ETF B", "isin": "B"}])
    payload = publication.run(tmp_path)
    date = datetime.now(timezone.utc).date().isoformat()
    combined = tmp_path / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_COMBINED_{date}.csv"
    etf = tmp_path / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ETF_ONLY_{date}.csv"
    action = tmp_path / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ACTION_CT_ONLY_{date}.csv"
    etf_mt = tmp_path / f"outputs/committee_master/OR_RANKING_ETF_MT_SHADOW_{date}.csv"
    top_action = tmp_path / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_TOP15_ACTION_{date}.csv"
    top_etf = tmp_path / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_TOP15_ETF_{date}.csv"
    forward = tmp_path / "state/objectives_risk/OR_HEBDO_FORWARD_VALIDATION_SCHEDULE.csv"
    assert combined.exists() and etf.exists() and action.exists() and etf_mt.exists()
    assert top_action.exists() and top_etf.exists() and forward.exists()
    assert pd.read_csv(combined, sep=";")["isin"].tolist() == ["B", "A"]
    assert pd.read_csv(etf, sep=";")["isin"].tolist() == ["B"]
    assert pd.read_csv(action, sep=";")["isin"].tolist() == ["A"]
    assert len(pd.read_csv(forward, sep=";")) == 6
    assert payload["or_source_gate_pass"] == 2
    assert payload["reference_modified"] is False
    assert payload["real_orders_enabled"] is False
