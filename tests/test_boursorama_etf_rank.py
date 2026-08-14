from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd

from v182.sources.boursorama_etf import extract_category_ranks, fetch_boursorama_etf_rankings


class _Response:
    def __init__(self,text:str):
        self.text=text
        self.status_code=200


class _Requests:
    def __init__(self,text:str):
        self.text=text
    def get(self,*args,**kwargs):
        return _Response(self.text)


def _value(observations:list[dict],field:str):
    return next(row["value"] for row in observations if row["field"]==field)


def _page(ranks: list[int]) -> str:
    joined=" ".join(str(x) for x in ranks)
    return f"""
    <div>catégorie morningstar Actions France Grandes Cap.</div>
    <div>PERFORMANCES ANNUELLES DES 5 DERNIÈRES ANNÉES</div>
    <div>2021 2022 2023 2024 2025</div>
    <div>Tracker +10% +11% +12% +13% +14%</div>
    <div>Catégorie +9% +10% +11% +12% +13%</div>
    <div>Rang {joined}</div>
    <div>Calcul fin de mois au 31/07/2026</div>
    <div>performance volatilité</div>
    """


def test_extract_category_ranks_keeps_real_raw_ranks_without_invented_percentile():
    ranks=extract_category_ranks(_page([7,10,8,30,32]))
    assert ranks=={"2021":7,"2022":10,"2023":8,"2024":30,"2025":32}


def test_rank_history_produces_run_improvement_only_from_previous_snapshot(tmp_path):
    etfs=pd.DataFrame([{"isin":"LU0000000001","source_url":"https://www.boursorama.com/bourse/trackers/cours/1rTTEST/"}])
    history=tmp_path/"rank.csv"
    req1=_Requests(_page([50,40,30,20,15]))
    obs1,fail1=fetch_boursorama_etf_rankings(etfs,history,requests_module=req1,observed_at=datetime(2026,8,14,tzinfo=timezone.utc),max_workers=1,delay_seconds=0)
    assert not fail1
    assert _value(obs1,"boursorama_category_rank_latest")==15
    assert _value(obs1,"boursorama_category_rank_annual_improvement")==35
    assert not any(row["field"]=="boursorama_category_rank_run_improvement" for row in obs1)

    req2=_Requests(_page([50,40,30,20,10]))
    obs2,fail2=fetch_boursorama_etf_rankings(etfs,history,requests_module=req2,observed_at=datetime(2026,8,15,tzinfo=timezone.utc),max_workers=1,delay_seconds=0)
    assert not fail2
    assert _value(obs2,"boursorama_category_rank_latest")==10
    assert _value(obs2,"boursorama_category_rank_run_improvement")==5
