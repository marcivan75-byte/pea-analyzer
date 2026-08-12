import pandas as pd

from v182.sources.gdelt_news import lexical_score
from v182.sources.fred_macro import _series_score
from v182.features.topdown_features import _market_regime_score, _group_regime_scores


def test_gdelt_lexical_score_keeps_no_directional_evidence_missing():
    result=lexical_score(["Company holds annual general meeting", "Board publishes calendar"])
    assert result.article_count==2
    assert result.score is None


def test_gdelt_lexical_score_is_directional_when_evidence_exists():
    positive=lexical_score(["Company beats estimates with record profit and growth"])
    negative=lexical_score(["Company profit warning after downgrade and losses"])
    assert positive.score is not None and positive.score > 50
    assert negative.score is not None and negative.score < 50


def test_fred_series_percentile_respects_direction():
    values=[float(x) for x in range(1,101)]
    assert _series_score(values,"HIGH") > 90
    assert _series_score(values,"LOW") < 10


def test_market_regime_score_uses_real_breadth_and_momentum_not_neutral_fill():
    strong=pd.DataFrame({"perf_1m_pct":[2,3,4,5,6,7,8,9,10,11],"perf_6m_pct":[8,9,10,11,12,13,14,15,16,17]})
    weak=pd.DataFrame({"perf_1m_pct":[-2,-3,-4,-5,-6,-7,-8,-9,-10,-11],"perf_6m_pct":[-8,-9,-10,-11,-12,-13,-14,-15,-16,-17]})
    assert _market_regime_score(strong) > _market_regime_score(weak)
    assert _market_regime_score(pd.DataFrame()) is None


def test_country_proxy_requires_minimum_group_size():
    frame=pd.DataFrame({"perf_1m_pct":[1,2,3,4],"perf_6m_pct":[5,6,7,8]})
    groups=pd.Series(["France","France","France","Italy"])
    scores=_group_regime_scores(frame,groups,min_names=3)
    assert "France" in scores
    assert "Italy" not in scores
