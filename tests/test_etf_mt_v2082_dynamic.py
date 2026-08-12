from pathlib import Path
import json
import numpy as np
import pandas as pd

from v182.features.etf_mt_v2082_dynamic import apply_dynamic_weighting

ROOT=Path(__file__).resolve().parents[1]


def test_partial_38_criterion_row_is_dynamically_reweighted_when_coverage_is_sufficient():
    mt=json.loads((ROOT/"config"/"V20.8_ETF_MT_HIGH_PRECISION.json").read_text())
    dyn=json.loads((ROOT/"config"/"V20.8.2_ETF_MT_DYNAMIC.json").read_text())
    expected=list(mt["dynamic_criteria"])
    ids=["ETF_A","ETF_B","ETF_C"]
    rows=[]
    for j,isin in enumerate(ids):
        row={"instrument_id":isin,"feature_as_of":"2026-08-12","history_sessions":800,"staleness_days":0,"criteria_complete":True,"score_raw":50.0+j,"score_rank_pct":50.0,"score_final":50.0,"rank_on_date":j+1,"decision":"REJECT_SCORE"}
        for i,name in enumerate(expected): row[name]=0.01*(i+1)+0.02*j
        rows.append(row)
    # One missing criterion is far above the 70% weighted-coverage gate.
    rows[0][expected[0]]=np.nan; rows[0]["criteria_complete"]=False
    strict=pd.DataFrame(rows)
    idx=pd.bdate_range("2023-01-02",periods=800)
    histories={isin:pd.DataFrame({"Close":np.linspace(100+j,160+j,800),"Volume":1_000_000},index=idx) for j,isin in enumerate(ids)}
    ref=pd.DataFrame({"isin":ids,"name":ids,"category":["A","B","C"]})
    out,summary=apply_dynamic_weighting(strict,histories,ref,mt,dyn)
    a=out[out["instrument_id"]=="ETF_A"].iloc[0]
    assert a["dynamic_weight_coverage_pct"] >= 70.0
    assert pd.notna(a["dynamic_score_raw"])
    assert pd.notna(a["dynamic_score_final"])
    assert a["dynamic_available_criteria"] == 37
    assert a["dynamic_missing_policy"] == "AVAILABLE_CRITERIA_RENORMALIZED_TO_100_PERCENT"
    assert summary["historical_performance_attribution"] == "NONE_FOR_V20.8.2"
    assert summary["partial_dynamic_scorable_etfs"] >= 1
