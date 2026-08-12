import pandas as pd

from v182.decision.committee_master import resolve_field, classify_sector


def test_sector_neutral_valuation_discount_uses_exact_45_20_35_formula_with_renormalisation():
    df=pd.DataFrame({
        "sector_yf":["Technology","Technology","Technology"],
        "per_forward_yf":[10.0,20.0,30.0],
        "pb":[1.0,2.0,3.0],
        "free_cash_flow":[30.0,20.0,10.0],
        "market_cap":[100.0,100.0,100.0],
    })
    score,source=resolve_field(df,"valuation_discount_score")
    assert score is not None
    assert score.notna().all()
    assert score.iloc[0] > score.iloc[1] > score.iloc[2]
    assert "SECTOR_NEUTRAL_VALUATION_45_20_35" in source


def test_valuation_discount_does_not_invent_missing_component():
    df=pd.DataFrame({
        "sector_yf":["Financial Services","Financial Services"],
        "per_forward_yf":[8.0,16.0],
        "pb":[1.0,2.0],
    })
    score,source=resolve_field(df,"valuation_discount_score")
    assert score is not None and score.notna().all()
    assert score.iloc[0] > score.iloc[1]
    assert "free_cash_flow" not in source


def test_revision_aliases_are_used_directly():
    df=pd.DataFrame({"net_upgrades_30d":[2,-1],"broker_weighted_revision_30d":[5.0,-2.0]})
    upgrades,src1=resolve_field(df,"net_upgrades_30d_v21")
    revisions,src2=resolve_field(df,"broker_weighted_revision_30d")
    assert list(upgrades)==[2,-1]
    assert list(revisions)==[5.0,-2.0]
    assert src1=="ALIAS:net_upgrades_30d"
    assert src2=="DIRECT:broker_weighted_revision_30d"


def test_sector_classification_prefers_real_sector_and_maps_common_cases():
    assert classify_sector(pd.Series({"name":"BNP Paribas","sector_yf":"Financial Services"}),"ACTION")=="FINANCE"
    assert classify_sector(pd.Series({"name":"ASML","sector_yf":"Technology"}),"ACTION")=="TECHNOLOGIE"
    assert classify_sector(pd.Series({"name":"Shell","industry_yf":"Oil & Gas Integrated"}),"ACTION")=="ENERGIE"
    assert classify_sector(pd.Series({"name":"Unknown Company","sector_yf":"Specialty Services"}),"ACTION")=="SPECIALTY SERVICES"
