"""Research-only PIT empirical-payoff ablation for HEBDO AT META.

The current ExpectedValueRanker uses fixed +14% / -9% payoffs. This study asks
whether replacing only those payoff assumptions with realized historical
payoffs, learned strictly from already-mature outcomes, improves the ranking.

2010-2022 is expanding walk-forward. 2023-2026 is evaluation-only and all
training inputs are frozen before 2023. No signal rule, J1 rule, stop, holding
horizon, sizing or portfolio constraint is changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.backtests.v21_8_1_backtest_B_v2 import compute_true_26w_pnl
from v182.hebdo.expected_value_ranker import ExpectedValueRanker, EV_VALID_STATUS, MAE_VALID_STATUS
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_longitudinal_audit73 import load_governed_ohlcv
from v182.hebdo.tabport_meta_component_ablation import StopRiskIsotonic
from v182.hebdo.tabport_meta_walkforward_research import (
    EMBARGO_DAYS,
    HOLDOUT_START,
    build_pre_meta_candidates,
    training_cutoff_for_year,
)
from v182.hebdo.tabport_publish import build_weekly_meta_signals


def build_labeled_with_pnl(candidates: pd.DataFrame, ohlcv: pd.DataFrame) -> pd.DataFrame:
    by = {t: g.sort_values('date').reset_index(drop=True) for t, g in ohlcv.groupby('ticker', sort=False)}
    rows = []
    for _, r in candidates.iterrows():
        g = by.get(str(r['ticker']))
        if g is None:
            continue
        d = pd.Timestamp(r['market_snapshot_date'])
        future = g[g['date'] > d].head(126)
        res = compute_true_26w_pnl(float(r['close']), future[['open','high','low','close']], 0.09, 126)
        z = r.to_dict()
        z.update({
            'mfe': res.get('mfe'), 'mae': res.get('mae'), 'hit_stop': res.get('hit_stop'),
            'outcome_pnl': res.get('pnl'), 'outcome_block_reason': res.get('block_reason'),
        })
        rows.append(z)
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError('BLOCK_META_PAYOFF_NO_OUTCOMES')
    complete = (
        out[['mfe','mae','hit_stop','outcome_pnl']].notna().all(axis=1)
        & out['outcome_block_reason'].isna()
    )
    out = out.loc[complete].copy()
    out = MetaLabeler(label_horizon_days=EMBARGO_DAYS).build_meta_label(out)
    out['outcome_pnl'] = pd.to_numeric(out['outcome_pnl'], errors='coerce')
    if out['outcome_pnl'].isna().any() or not np.isfinite(out['outcome_pnl'].to_numpy(dtype=float)).all():
        raise ValueError('BLOCK_META_PAYOFF_NONFINITE_OUTCOME')
    return out


def empirical_payoffs(train: pd.DataFrame) -> dict:
    pnl = pd.to_numeric(train.get('outcome_pnl'), errors='coerce').dropna()
    pnl = pnl[np.isfinite(pnl.to_numpy(dtype=float))]
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    if len(pnl) < 100 or len(wins) < 20 or len(losses) < 20:
        return {'status':'BLOCK_PAYOFF_INSUFFICIENT_SAMPLE','n':int(len(pnl)),'wins':int(len(wins)),'losses':int(len(losses))}
    avg_win = float(wins.mean())
    avg_loss = float(losses.mean())
    if not np.isfinite(avg_win) or not np.isfinite(avg_loss) or avg_win <= 0 or avg_loss >= 0:
        return {'status':'BLOCK_PAYOFF_INVALID','n':int(len(pnl)),'wins':int(len(wins)),'losses':int(len(losses))}
    return {
        'status':EV_VALID_STATUS, 'n':int(len(pnl)), 'wins':int(len(wins)), 'losses':int(len(losses)),
        'avg_win':avg_win, 'avg_loss':avg_loss, 'rr':float(avg_win/abs(avg_loss)),
    }


def _fit_meta(train: pd.DataFrame):
    model = MetaLabeler(label_horizon_days=EMBARGO_DAYS)
    result = {'status':'BLOCK_NO_TRAINING_ROWS','n':0}
    if len(train):
        result = model.train(train)
    return model, result


def walkforward_variant(candidates, labeled, *, use_meta, use_stop_cal, use_empirical_payoff, variant):
    parts = []
    audits = []
    for year in sorted(candidates['date'].dt.year.unique()):
        year = int(year)
        score = candidates[candidates['date'].dt.year == year].copy()
        cutoff = training_cutoff_for_year(year)
        train = labeled[labeled['date'] <= cutoff].copy()
        meta_model, meta_result = _fit_meta(train)
        meta_ok = meta_result.get('status') == 'TRAINED_PURGED_TEMPORAL_OOS'
        stop_model = StopRiskIsotonic()
        stop_result = stop_model.fit(train) if len(train) else stop_model.audit()
        stop_ok = stop_result.get('status') == MAE_VALID_STATUS
        payoff_result = empirical_payoffs(train) if len(train) else {'status':'BLOCK_NO_TRAINING_ROWS','n':0}
        payoff_ok = payoff_result.get('status') == EV_VALID_STATUS

        for decision, grp in score.groupby('date', sort=True):
            s = grp.copy()
            if use_meta and meta_ok:
                s = meta_model.predict_proba(s)
            else:
                s['prob_meta'] = 0.5
                s['meta_model_status'] = meta_result.get('status','UNTRAINED') if use_meta else 'ABLATION_META_FIXED_0_5'
            if use_stop_cal and stop_ok:
                s = stop_model.transform(s)
            if use_empirical_payoff and payoff_ok:
                ranker = ExpectedValueRanker(
                    avg_win=payoff_result['avg_win'], avg_loss=payoff_result['avg_loss'],
                    fee=0.003, parameter_status=EV_VALID_STATUS,
                )
            else:
                ranker = ExpectedValueRanker()
            ranked = ranker.rank_batch(s)
            ranked['date'] = decision
            ranked['wf_train_cutoff'] = cutoff
            ranked['wf_train_n'] = len(train)
            ranked['wf_meta_trained'] = bool(meta_ok)
            ranked['wf_stop_calibrated'] = bool(stop_ok)
            ranked['wf_payoff_empirical'] = bool(payoff_ok and use_empirical_payoff)
            ranked['payoff_avg_win'] = payoff_result.get('avg_win', 0.14) if use_empirical_payoff else 0.14
            ranked['payoff_avg_loss'] = payoff_result.get('avg_loss', -0.09) if use_empirical_payoff else -0.09
            ranked['ablation_variant'] = variant
            parts.append(ranked)
        audits.append({
            'variant':variant, 'year':year, 'score_rows':int(len(score)), 'training_cutoff':str(cutoff),
            'training_rows':int(len(train)), 'meta_training_status':meta_result.get('status'),
            'stop_training_status':stop_result.get('status'), 'payoff_training_status':payoff_result.get('status'),
            'payoff_n':payoff_result.get('n'), 'payoff_avg_win':payoff_result.get('avg_win'),
            'payoff_avg_loss':payoff_result.get('avg_loss'), 'payoff_rr':payoff_result.get('rr'),
            'holdout_training_frozen':bool(year >= 2023),
        })
    if not parts:
        raise ValueError(f'BLOCK_META_PAYOFF_NO_SIGNALS:{variant}')
    out = pd.concat(parts, ignore_index=True)
    out = out[out['tier'].isin(['TCT','CT_WATCH']) & (pd.to_numeric(out['EV_net'], errors='coerce') >= 0)].copy()
    return out.sort_values(['date','EV_net','ticker'], ascending=[True,False,True]).reset_index(drop=True), pd.DataFrame(audits)


def _segment_run(signals, ohlcv, start, end):
    s = signals.copy()
    if start is not None: s = s[s['date'] >= start]
    if end is not None: s = s[s['date'] < end]
    if s.empty: return {'status':'EMPTY'}
    needed = set(s['ticker'].astype(str))
    prices = ohlcv[ohlcv['ticker'].astype(str).isin(needed)][['date','ticker','open','high','low','close']].copy()
    r = Tabport65k(TabportConfig()).run(s, prices)
    m = r['metrics'].copy(); m['status'] = 'OK'; return m


def run(pre2023:Path, manifest:Path, holdout_cache:Path, output_dir:Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_governed_ohlcv(pre2023, manifest, holdout_cache)
    baseline, baseline_audit = build_weekly_meta_signals(ohlcv)
    candidates, candidate_audit = build_pre_meta_candidates(ohlcv)
    labeled = build_labeled_with_pnl(candidates, ohlcv)

    definitions = {
        'EMP_PAYOFF_ONLY': (False, False, True),
        'STOP_CAL_EMP_PAYOFF': (False, True, True),
        'META_EMP_PAYOFF': (True, False, True),
        'META_STOP_EMP_PAYOFF': (True, True, True),
    }
    variants = {'BASELINE_UNTRAINED': baseline}
    audit_parts = []
    for name, (use_meta, use_stop, use_payoff) in definitions.items():
        scored, audit = walkforward_variant(candidates, labeled, use_meta=use_meta, use_stop_cal=use_stop,
                                             use_empirical_payoff=use_payoff, variant=name)
        variants[name] = scored; audit_parts.append(audit)

    feature_tickers = set().union(*(set(x['ticker'].astype(str)) for x in variants.values()))
    features = add_antifp_features(ohlcv[ohlcv['ticker'].isin(feature_tickers)].copy())
    confirmed = {}
    for name, signals in variants.items():
        confirmed[name], _ = apply_j1_confirmation(signals, features)

    rows = []
    for name, s in confirmed.items():
        for segment, start, end in [('DEVELOPMENT_2010_2022',None,HOLDOUT_START),('HOLDOUT_2023_2026',HOLDOUT_START,None)]:
            m = _segment_run(s, ohlcv, start, end); m.update({'model':name,'segment':segment}); rows.append(m)
    comparison = pd.DataFrame(rows)
    train_audit = pd.concat(audit_parts, ignore_index=True)
    summary = {
        'status':'SUCCESS','version':'TABPORT_META_EMPIRICAL_PAYOFF_V1','production_promotion':False,
        'governance':{
            'development':'EXPANDING_WALK_FORWARD_2010_2022','label_embargo_days':EMBARGO_DAYS,
            'holdout':'2023_2026_EVALUATION_ONLY','holdout_training_frozen_before_2023':True,
            'holdout_used_for_tuning':False,'same_signal_family':True,'same_fp_filter':True,
            'same_j1_confirmation':True,'same_stop_pct':0.09,'same_hold_horizon_sessions':126,
            'same_position_budget_eur':4500,'payoff_source':'REALIZED_126_SESSION_OUTCOME_PIT_ONLY',
            'synthetic_imputation':False,
        },
        'quality':quality,'baseline_audit':baseline_audit,'candidate_audit':candidate_audit,
        'labeled_training_rows':int(len(labeled)),'variants':list(variants),
    }
    comparison.to_csv(output_dir/'TABPORT_META_PAYOFF_COMPARISON.csv', index=False)
    train_audit.to_csv(output_dir/'TABPORT_META_PAYOFF_TRAIN_AUDIT.csv', index=False)
    (output_dir/'TABPORT_META_PAYOFF_SUMMARY.json').write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')
    print(json.dumps(summary, indent=2, default=str)); print('---COMPARISON---'); print(comparison.to_csv(index=False)); print('---TRAIN---'); print(train_audit.to_csv(index=False))
    return summary


def main():
    p=argparse.ArgumentParser(); p.add_argument('--pre2023',required=True); p.add_argument('--manifest',required=True); p.add_argument('--holdout-cache',required=True); p.add_argument('--output-dir',required=True)
    a=p.parse_args(); run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir))

if __name__=='__main__': main()
