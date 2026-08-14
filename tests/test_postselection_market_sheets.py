from __future__ import annotations

import pandas as pd

from v182.sources.postselection_market_sheets import (
    enrich_postselection,
    extract_boursorama_action,
    extract_investing_technical,
    normalize_signal,
    technical_alignment,
)


class _Response:
    def __init__(self,text:str,status_code:int=200):
        self.text=text
        self.status_code=status_code


class _Requests:
    def get(self,url,*args,**kwargs):
        if "investing.com" in url and url.endswith("-technical"):
            return _Response("<div>Weekly Strong Buy</div><div>Monthly Buy</div>")
        if "boursorama.com" in url and "/consensus/" in url:
            return _Response("<div>Consensus des analystes : Achat</div><div>Objectif de cours moyen 150,50 €</div><div>Potentiel +12,5 %</div>")
        if "boursorama.com" in url:
            return _Response("<div>PER 18,4</div><div>Rendement 3,2 %</div><div>Plus haut 52 semaines 145,0</div><div>Plus bas 52 semaines 112,0</div>")
        return _Response("",404)


def test_signal_normalization_and_weekly_monthly_alignment():
    assert normalize_signal("Achat fort")=="STRONG_BUY"
    assert normalize_signal("Vente forte")=="STRONG_SELL"
    technical=extract_investing_technical("<div>Weekly Strong Buy</div><div>Monthly Sell</div>")
    assert technical["investing_weekly_signal"]=="STRONG_BUY"
    assert technical["investing_monthly_signal"]=="SELL"
    assert technical_alignment("STRONG_BUY","SELL")=="DIVERGENCE"


def test_boursorama_action_extracts_only_observed_fields():
    fields=extract_boursorama_action("Consensus Achat Objectif de cours 123,40 € Potentiel +8,2 % PER 15,5 Rendement 4,1 %")
    assert fields["boursorama_consensus_signal"]=="BUY"
    assert fields["boursorama_target_price"]==123.40
    assert fields["boursorama_target_upside_pct"]==8.2
    assert fields["boursorama_per"]==15.5
    assert fields["boursorama_dividend_yield_pct"]==4.1


def test_postselection_enrichment_is_shadow_and_scoped_to_requested_isin():
    actions=pd.DataFrame([
        {"isin":"FR0000120073","name":"Air Liquide","yahoo_ticker":"AI.PA","investing_url":"https://www.investing.com/equities/air-liquide"},
        {"isin":"FR0000120271","name":"TotalEnergies","yahoo_ticker":"TTE.PA","investing_url":"https://www.investing.com/equities/total"},
    ])
    enriched,failures=enrich_postselection(actions,{"FR0000120073"},requests_module=_Requests(),max_workers=1,delay_seconds=0)
    assert set(enriched["isin"])=={"FR0000120073"}
    row=enriched.iloc[0]
    assert row["investing_weekly_signal"]=="STRONG_BUY"
    assert row["investing_monthly_signal"]=="BUY"
    assert row["investing_weekly_monthly_alignment"]=="CONFIRMS_LONG"
    assert row["boursorama_consensus_signal"]=="BUY"
    assert row["postselection_confirmation"]=="CONFIRMS_LONG"
    assert row["postselection_decision_influence"]==0.0
    assert failures.empty
