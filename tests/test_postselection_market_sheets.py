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
            return _Response("<div>Consensus des analystes : Achat</div><div>Objectif de cours 3 mois : 150,50 EUR</div><div>Potentiel : +12,5 %</div>")
        if "boursorama.com" in url:
            return _Response("<div>rendement estimé 2026 3,2 %</div><div>PER estimé 2026 18,4</div><div>1 an +12,2% 145,0 112,0</div>")
        return _Response("",404)


def test_signal_normalization_and_weekly_monthly_alignment():
    assert normalize_signal("Achat fort")=="STRONG_BUY"
    assert normalize_signal("Renforcer")=="BUY"
    assert normalize_signal("Alléger")=="SELL"
    assert normalize_signal("Vente forte")=="STRONG_SELL"
    technical=extract_investing_technical("<div>Weekly Strong Buy</div><div>Monthly Sell</div>")
    assert technical["investing_weekly_signal"]=="STRONG_BUY"
    assert technical["investing_monthly_signal"]=="SELL"
    assert technical_alignment("STRONG_BUY","SELL")=="DIVERGENCE"


def test_boursorama_action_extracts_realistic_quote_and_direct_target_formats():
    html="""
    Consensus Acheter
    Objectif de cours 3 mois : 2 048,31 EUR Potentiel: 49,62%
    rendement estimé 2026 0,90%
    PER estimé 2026 32,38
    1 an +126,75% 1 999,9600 683,4800
    """
    fields=extract_boursorama_action(html)
    assert fields["boursorama_consensus_signal"]=="BUY"
    assert fields["boursorama_target_price"]==2048.31
    assert fields["boursorama_target_currency"]=="EUR"
    assert fields["boursorama_target_upside_pct"]==49.62
    assert fields["boursorama_per"]==32.38
    assert fields["boursorama_dividend_yield_pct"]==0.90
    assert fields["boursorama_52w_high"]==1999.96
    assert fields["boursorama_52w_low"]==683.48


def test_boursorama_current_consensus_table_extracts_note_counts_and_latest_median_target():
    html="""
    Objectif de cours Opinion Il y a 3 mois Il y a 2 mois Il y a 1 mois Il y a 7 jours le 14/07/2026
    1. Acheter 31 32 30 32 32
    2. Renforcer 5 5 5 6 6
    3. Conserver 5 6 5 4 4
    4. Alléger 3 2 3 3 3
    5. Vendre 0 0 0 0 0
    Nombre d'analystes 44 45 43 45 45
    Note médiane 1,55 1,51 1,56 1,51 1,51
    Historique des objectifs de cours médian (en EUR) 1 417,56 1 466,58 1 563,71 1 743,62 1 803,52 EUR
    Potentiel : 47,57%
    Note médiane* des analystes au 27.07.2026 1,42
    1. Acheter 2. Renforcer 3. Conserver 4. Alléger 5. Vendre
    """
    fields=extract_boursorama_action(html)
    assert fields["boursorama_consensus_note_median"]==1.42
    assert fields["boursorama_consensus_bucket"]=="ACHETER"
    assert fields["boursorama_consensus_signal"]=="BUY"
    assert fields["boursorama_acheter_n"]==32
    assert fields["boursorama_renforcer_n"]==6
    assert fields["boursorama_conserver_n"]==4
    assert fields["boursorama_alleger_n"]==3
    assert fields["boursorama_vendre_n"]==0
    assert fields["boursorama_analyst_count"]==45
    assert fields["boursorama_target_price"]==1803.52
    assert fields["boursorama_target_currency"]=="EUR"
    assert fields["boursorama_target_upside_pct"]==47.57


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
    assert row["boursorama_per"]==18.4
    assert row["boursorama_dividend_yield_pct"]==3.2
    assert row["postselection_confirmation"]=="CONFIRMS_LONG"
    assert row["postselection_decision_influence"]==0.0
    assert failures.empty
