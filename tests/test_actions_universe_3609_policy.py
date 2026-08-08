import pandas as pd

from v182.decision.actions_universe_3609_policy import _isin_valid, _horizon_and_timing


def test_isin_checksum_validation():
    assert _isin_valid('FR0000120271') is True
    assert _isin_valid('NL6549621184') is False


def test_unverified_high_score_is_structural_not_buy():
    df = pd.DataFrame({
        'identity_confidence':[0.35],
        'committee_score_3609':[82.0],
        'score_short_term':[80.0],
        'score_medium_term':[78.0],
        'score_long_term':[79.0],
        'short_thesis_score':[20.0],
        'Cours €':[100.0],
        'RSI 14j':[60.0],
    })
    out = _horizon_and_timing(df)
    assert out.loc[0,'decision'] == 'STRUCTURAL_CANDIDATE'
    assert pd.isna(out.loc[0,'T1_entry_low'])
    assert out.loc[0,'execution'] == 'RESEARCH_ONLY'


def test_verified_multi_horizon_candidate_can_be_buy_recommendation_only():
    df = pd.DataFrame({
        'identity_confidence':[0.98],
        'committee_score_3609':[80.0],
        'score_short_term':[79.0],
        'score_medium_term':[76.0],
        'score_long_term':[75.0],
        'short_thesis_score':[15.0],
        'Cours €':[100.0],
        'RSI 14j':[60.0],
    })
    out = _horizon_and_timing(df)
    assert out.loc[0,'decision'] == 'BUY_CANDIDATE'
    assert out.loc[0,'execution'] == 'RECOMMENDATION_ONLY'
    assert out.loc[0,'T1_entry_low'] == 98.0


def test_short_thesis_never_enables_execution():
    df = pd.DataFrame({
        'identity_confidence':[0.98],
        'committee_score_3609':[55.0],
        'score_short_term':[30.0],
        'score_medium_term':[35.0],
        'score_long_term':[32.0],
        'short_thesis_score':[85.0],
        'Cours €':[100.0],
        'RSI 14j':[40.0],
    })
    out = _horizon_and_timing(df)
    assert out.loc[0,'decision'] == 'SHORT_THESIS'
    assert out.loc[0,'execution'] == 'RESEARCH_ONLY'
