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


def test_extract_category_ranks_requires_explicit_rank_fraction_near_period():
    html="""
    <div>1 mois - Classement catégorie : 10 / 100</div>
    <div>1 an - Rang catégorie 3 sur 100</div>
    <div>3 ans - Classement catégorie : 20 / 200</div>
    """
    ranks=extract_category_ranks(html)
    assert ranks["1m"]["rank"]==10
    assert ranks["1y"]["rank"]==3
    assert ranks["3y"]["total"]==200
    assert ranks["1y"]["score"]>ranks["1m"]["score"]


def test_rank_history_produces_trend_only_from_previous_snapshot(tmp_path):
    etfs=pd.DataFrame([{"isin":"LU0000000001","source_url":"https://www.boursorama.com/bourse/trackers/cours/1rTTEST/"}])
    history=tmp_path/"rank.csv"
    req1=_Requests("<div>1 an Classement catégorie 50 / 100</div>")
    obs1,fail1=fetch_boursorama_etf_rankings(etfs,history,requests_module=req1,observed_at=datetime(2026,8,14,tzinfo=timezone.utc),max_workers=1,delay_seconds=0)
    assert not fail1
    assert not any(row["field"]=="boursorama_category_rank_trend_shadow" for row in obs1)

    req2=_Requests("<div>1 an Classement catégorie 10 / 100</div>")
    obs2,fail2=fetch_boursorama_etf_rankings(etfs,history,requests_module=req2,observed_at=datetime(2026,8,15,tzinfo=timezone.utc),max_workers=1,delay_seconds=0)
    assert not fail2
    assert float(_value(obs2,"boursorama_category_rank_trend_shadow"))>0
