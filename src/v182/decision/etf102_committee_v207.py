from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) >= 4 else Path('.')
CONFIG = ROOT / 'data/reference/V20.7_ETF102_CONFIG.json'
FUNNEL_CONFIG = ROOT / 'data/reference/V20.7_FUNNEL_CONFIG.json'
IN = ROOT / 'outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv'
SMART = ROOT / 'outputs/V18.3_PEA_ETF_SMART_MONEY_SHADOW.csv'
OUT = ROOT / 'outputs/V20.7_ETF102_COMMITTEE.csv'
COMPAT_OUT = ROOT / 'outputs/V20.4.3_ETF102_COMMITTEE.csv'
AUDIT = ROOT / 'outputs/audit/V20.7_ETF102_COMMITTEE_AUDIT.json'
SUMMARY = ROOT / 'outputs/V20.7_ETF102_COMMITTEE_SUMMARY.md'


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors='coerce')


def _rank(s: pd.Series, higher: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors='coerce')
    p = x.rank(pct=True, method='average') * 100.0
    return p if higher else 100.0 - p


def _zone(s: pd.Series, target: float, slope: float) -> pd.Series:
    x = pd.to_numeric(s, errors='coerce')
    y = (100.0 - (x - target).abs() * slope).clip(0, 100)
    return y.where(x.notna())


def _bool_score(s: pd.Series) -> pd.Series:
    x = s.astype('string').str.strip().str.lower()
    out = pd.Series(np.nan, index=s.index, dtype=float)
    out.loc[x.isin(['true','1','yes','y','oui'])] = 100.0
    out.loc[x.isin(['false','0','no','n','non'])] = 0.0
    return out


def _distribution_score(s: pd.Series, mapping: dict) -> pd.Series:
    x = s.astype('string').str.strip().str.upper()
    out = pd.Series(np.nan, index=s.index, dtype=float)
    for key, value in mapping.items():
        out.loc[x.eq(str(key).upper())] = float(value)
    return out


def _weighted_available(parts: dict[str, tuple[pd.Series, float]]) -> tuple[pd.Series, pd.Series, dict[str, pd.Series]]:
    index = next(iter(parts.values()))[0].index
    numerator = pd.Series(0.0, index=index)
    denominator = pd.Series(0.0, index=index)
    total = sum(float(w) for _, w in parts.values())
    eff: dict[str, pd.Series] = {}
    for field, (series, weight) in parts.items():
        s = pd.to_numeric(series, errors='coerce')
        w = float(weight)
        numerator += s.fillna(0.0) * w
        denominator += s.notna().astype(float) * w
    score = numerator / denominator.replace(0.0, np.nan)
    for field, (series, weight) in parts.items():
        s = pd.to_numeric(series, errors='coerce')
        w = float(weight)
        eff[field] = pd.Series(np.where(s.notna() & denominator.gt(0), w / denominator, 0.0), index=index)
    coverage = denominator / total if total else pd.Series(0.0, index=index)
    return score.clip(0,100), coverage.clip(0,1), eff


def _topdown(df: pd.DataFrame, fcfg: dict) -> pd.DataFrame:
    mapping = {
        'global_macro':'funnel_global_macro_score',
        'country_macro':'funnel_country_macro_score',
        'global_news':'funnel_global_news_score',
        'country_news':'funnel_country_news_score',
        'sector_news':'funnel_sector_news_score',
        'instrument_news':'funnel_instrument_news_score',
        'market_sentiment':'funnel_market_sentiment_score',
    }
    parts = {k: (_num(df, col), float(fcfg['context_weights'][k])) for k,col in mapping.items()}
    score, cov, eff = _weighted_available(parts)
    mult_cfg = fcfg['context_multiplier']
    mult = float(mult_cfg['min']) + (float(mult_cfg['max'])-float(mult_cfg['min'])) * (score/100.0)
    min_cov = float(fcfg['minimum_context_coverage_for_positive_multiplier'])
    mult = mult.where(~((cov < min_cov) & (mult > 1.0)), 1.0).clip(float(mult_cfg['min']), float(mult_cfg['max']))
    gates = fcfg['risk_gates']
    gate = pd.Series('PASS', index=df.index, dtype='object')
    gate.loc[score < float(gates['review_only_below'])] = 'REVIEW_ONLY'
    gate.loc[score < float(gates['block_buy_below'])] = 'BLOCK_BUY'
    out = pd.DataFrame(index=df.index)
    out['v207_topdown_score'] = score.round(2)
    out['v207_topdown_coverage'] = cov.round(3)
    out['v207_topdown_multiplier'] = mult.round(4)
    out['v207_topdown_gate'] = gate
    for key, series in eff.items():
        out[f'v207_topdown_effective_weight_{key}'] = series.round(4)
    return out


def _metric_score(df: pd.DataFrame, field: str, horizon: str, cfg: dict) -> pd.Series:
    modes = cfg['metric_modes']
    raw = df[field] if field in df.columns else pd.Series(np.nan, index=df.index)
    if field == 'positive_reversal_flag':
        return _bool_score(raw)
    if field == 'distribution_policy':
        return _distribution_score(raw, modes['distribution_policy_scores'])
    if field == 'diversification_direct_score':
        return pd.to_numeric(raw, errors='coerce').clip(0,100)
    if field == 'rsi14':
        if horizon == 'SHORT':
            return _zone(raw, float(modes['short_rsi_target']), float(modes['short_rsi_slope']))
        return _zone(raw, float(modes['ct_rsi_target']), float(modes['ct_rsi_slope']))
    if horizon == 'SHORT':
        if field in {'perf_1m_pct','perf_3m_pct','relative_strength','macd_hist'}:
            return _rank(raw, higher=False)
        if field in {'volatility_20d','volatility_60d','direct_beta3y'}:
            return _rank(raw, higher=True)
        if field == 'max_drawdown_1y':
            return _rank(raw, higher=False)
        if field in {'rvol20','fund_total_assets_eur_m','volume'}:
            return _rank(raw, higher=True)
        if field == 'sentiment_regime_score':
            return pd.to_numeric(raw, errors='coerce').clip(0,100)
    if field in set(modes['higher_better']):
        return _rank(raw, higher=True)
    if field in set(modes['lower_better']):
        return _rank(raw, higher=False)
    if field in set(modes['less_negative_better']):
        return _rank(raw, higher=True)
    return _rank(raw, higher=True)


def _bonus_malus(df: pd.DataFrame, cfg: dict) -> pd.Series:
    out = pd.Series(0.0, index=df.index)
    stars = _num(df, 'morningstar_rating')
    for key,value in cfg['bonuses_maluses']['morningstar'].items():
        out += np.where(stars.eq(float(key)), float(value), 0.0)
    risk = _num(df, 'risk_indicator')
    out += np.where(risk.ge(6), float(cfg['bonuses_maluses']['risk_indicator_ge_6']), 0.0)
    spread = _num(df, 'spread_pct')
    out += np.where(spread.le(.20), float(cfg['bonuses_maluses']['spread_le_020']), 0.0)
    out += np.where(spread.gt(.70), float(cfg['bonuses_maluses']['spread_gt_070']), 0.0)
    out += np.where(spread.gt(1.20), float(cfg['bonuses_maluses']['spread_gt_120']), 0.0)
    return out


def _merge_smart_money(df: pd.DataFrame) -> pd.DataFrame:
    if not SMART.exists():
        return df
    sm = pd.read_csv(SMART, sep=';', dtype=object, encoding='utf-8-sig', low_memory=False)
    keep=[c for c in ['isin','ifs_raw','ifs_effective','smart_money_confidence','institutional_flow_label','flow_status','flow_history_snapshots','flow_observations','smart_money_data_status','smart_money_source_completeness'] if c in sm.columns]
    if 'isin' not in keep:
        return df
    sm=sm[keep].drop_duplicates('isin',keep='last')
    conflicts=[c for c in keep if c!='isin' and c in df.columns]
    if conflicts:
        df=df.drop(columns=conflicts)
    return df.merge(sm,on='isin',how='left')


def _decision(score: float, thresholds: dict) -> str:
    if score >= float(thresholds['BUY_CANDIDATE']): return 'BUY_CANDIDATE'
    if score >= float(thresholds['WATCH']): return 'WATCH'
    if score >= float(thresholds['REVIEW']): return 'REVIEW'
    return 'REJECT'


def _scaled_drawdown(dd: pd.Series) -> pd.Series:
    x=pd.to_numeric(dd,errors='coerce')
    return x.where(x.abs() > 1.5, x*100.0)


def build(df: pd.DataFrame, cfg: dict, fcfg: dict) -> pd.DataFrame:
    if len(df) != int(cfg['universe_size']) or df['isin'].astype(str).nunique() != int(cfg['universe_size']):
        raise RuntimeError('V20.7 ETF102 universe gate failed')
    if not df['ticker_identity_status'].astype(str).eq('FINAL_VALIDATED').all():
        raise RuntimeError('V20.7 requires FINAL_VALIDATED identity for all 102 ETF')
    if 'asset_class' in df.columns and not df['asset_class'].astype(str).str.upper().eq('ETF').all():
        raise RuntimeError('V20.7 ETF-only gate failed')
    out=_merge_smart_money(df.copy())

    td=_topdown(out,fcfg)
    for c in td.columns: out[c]=td[c]
    out['funnel_context_score']=out['v207_topdown_score']
    out['funnel_context_coverage']=out['v207_topdown_coverage']
    out['funnel_macro_multiplier']=out['v207_topdown_multiplier']
    out['funnel_risk_gate']=out['v207_topdown_gate']
    out['sentiment_regime_score']=(100.0-out['v207_topdown_score']).clip(0,100).round(2)

    bonus=_bonus_malus(out,cfg)
    out['etf102_bonus_malus']=bonus.round(2)
    cov_floor=float(cfg['coverage']['coverage_penalty_floor'])
    dd=_scaled_drawdown(out.get('max_drawdown_1y',pd.Series(np.nan,index=out.index)))
    aum=_num(out,'fund_total_assets_eur_m')

    derived_cols: dict[str, pd.Series] = {}
    for horizon in ['CT','MT','LT','SHORT']:
        weights=cfg['direct_weights'][horizon]
        metric_scores={f:_metric_score(out,f,horizon,cfg) for f in weights}
        parts={f:(metric_scores[f],float(w)) for f,w in weights.items()}
        base,cov,eff=_weighted_available(parts)
        raw=base*(cov_floor+(1.0-cov_floor)*cov)
        penalty=pd.Series(0.0,index=out.index)
        if horizon in {'CT','MT','LT'}:
            penalty += bonus
            g=cfg['gates'][horizon]
            penalty += np.where(dd < float(g['drawdown_penalty_below_pct']), float(g['drawdown_penalty_points']), 0.0)
            if 'aum_soft_below_eur_m' in g:
                soft=(aum.notna() & (aum < float(g['aum_soft_below_eur_m'])) & (aum >= float(g['aum_reject_below_eur_m'])))
                penalty += np.where(soft,float(g['aum_soft_penalty_points']),0.0)
            if horizon=='CT':
                raw=(raw+penalty)*out['v207_topdown_multiplier']
            else:
                raw=raw+penalty
        else:
            g=cfg['gates']['SHORT']
            penalty += np.where(dd < float(g['drawdown_late_short_below_pct']), float(g['drawdown_late_short_penalty_points']), 0.0)
            raw=raw+penalty
        raw=raw.clip(0,100)
        hz=horizon.lower()
        derived_cols[f'score_{hz}_raw']=raw.round(2)
        derived_cols[f'weight_coverage_{hz}']=cov.round(3)
        derived_cols[f'v207_gate_penalty_{hz}']=penalty.round(2)
        for field,series in eff.items():
            safe=field.replace('%','pct')
            derived_cols[f'effective_weight_{hz}_{safe}']=series.round(4)
            derived_cols[f'contrib_{hz}_{safe}']=(metric_scores[field].fillna(0)*series).round(2)
    out=pd.concat([out,pd.DataFrame(derived_cols,index=out.index)],axis=1).copy()

    cal=cfg['score_calibration']
    rw=float(cal['raw_weight']); pw=float(cal['percentile_weight'])
    for hz in ['ct','mt','lt']:
        raw=_num(out,f'score_{hz}_raw')
        out[f'score_{hz}']=(rw*raw+pw*_rank(raw,True)).clip(0,100).round(2)
    srw=float(cal['short_raw_weight'])
    sraw=_num(out,'score_short_raw')
    out['score_short']=(srw*sraw+(1.0-srw)*_rank(sraw,True)).clip(0,100).round(2)

    identity=(_num(out,'ticker_confidence_pct')/100.0).clip(0,1)
    sm_conf=_num(out,'smart_money_confidence'); ifs=_num(out,'ifs_effective'); sm_cfg=cfg['smart_money']
    min_buy_cov=float(cfg['coverage']['min_weight_coverage_buy']); min_watch_cov=float(cfg['coverage']['min_weight_coverage_watch']); id_min=float(cfg['coverage']['identity_min_buy'])

    for hz in ['ct','mt','lt']:
        H=hz.upper(); score=_num(out,f'score_{hz}'); cov=_num(out,f'weight_coverage_{hz}'); g=cfg['gates'][H]
        decisions=[]; reasons=[]; smg=[]
        for i in out.index:
            s=float(score.loc[i]) if pd.notna(score.loc[i]) else 0.0
            dec=_decision(s,cfg['thresholds'][H]); reason='SCORE'; gate='NONE'
            if identity.loc[i] < id_min:
                dec,reason='REVIEW','IDENTITY_CONFIDENCE'
            elif cov.loc[i] < min_watch_cov:
                dec,reason='REVIEW','DATA_COVERAGE_LOW'
            elif pd.isna(aum.loc[i]):
                if dec=='BUY_CANDIDATE': dec,reason='WATCH','AUM_REQUIRED_FOR_BUY'
            elif aum.loc[i] < float(g['aum_reject_below_eur_m']):
                dec,reason='REJECT','AUM_HARD_GATE'
            elif dec=='BUY_CANDIDATE' and cov.loc[i] < min_buy_cov:
                dec,reason='WATCH','DATA_COVERAGE_BUY_GATE'
            if pd.notna(sm_conf.loc[i]) and pd.notna(ifs.loc[i]) and sm_conf.loc[i] >= float(sm_cfg['min_confidence_for_gate']):
                if ifs.loc[i] <= float(sm_cfg['block_buy_ifs_lte']):
                    if dec=='BUY_CANDIDATE': dec,reason='REVIEW','SMART_MONEY_NEGATIVE_BLOCK'
                    gate='BLOCK_BUY'
                elif ifs.loc[i] <= float(sm_cfg['review_ifs_lte']):
                    if dec=='BUY_CANDIDATE': dec,reason='WATCH','SMART_MONEY_NEGATIVE_REVIEW'
                    gate='REVIEW_BUY'
            tdg=str(out.loc[i,'v207_topdown_gate'])
            if tdg in {'BLOCK_BUY','REVIEW_ONLY'} and dec in {'BUY_CANDIDATE','WATCH'}:
                dec,reason='REVIEW',f'TOPDOWN_{tdg}'
            decisions.append(dec); reasons.append(reason); smg.append(gate)
        out[f'decision_{hz}']=decisions; out[f'decision_reason_{hz}']=reasons; out[f'smart_money_gate_{hz}']=smg
        out[f'rank_{hz}']=score.rank(method='min',ascending=False).astype(int)

    st=cfg['thresholds']['SHORT']; sc=_num(out,'score_short'); scov=_num(out,'weight_coverage_short'); sg=cfg['gates']['SHORT']
    sdec=[]; sreason=[]
    for i in out.index:
        if identity.loc[i] < id_min:
            dec,reason='NO_SHORT','IDENTITY_CONFIDENCE'
        elif scov.loc[i] < min_watch_cov:
            dec,reason='NO_SHORT','DATA_COVERAGE_LOW'
        elif pd.isna(aum.loc[i]) or aum.loc[i] < float(sg['aum_no_short_below_eur_m']):
            dec,reason='NO_SHORT','AUM_SHORTABILITY_GATE'
        elif sc.loc[i] >= float(st['SHORT_CANDIDATE']):
            dec,reason='SHORT_CANDIDATE','SCORE'
        elif sc.loc[i] >= float(st['WATCH_SHORT']):
            dec,reason='WATCH_SHORT','SCORE'
        else:
            dec,reason='NO_SHORT','SCORE'
        sdec.append(dec); sreason.append(reason)
    out['decision_short']=sdec; out['decision_reason_short']=sreason
    out['rank_short']=sc.rank(method='min',ascending=False).astype(int)

    limits=cfg['selection_limits']
    for hz in ['ct','mt','lt']:
        out[f'selection_{hz}']=(out[f'rank_{hz}']<=int(limits[hz.upper()])) & out[f'decision_{hz}'].isin(['BUY_CANDIDATE','WATCH'])
    out['selection_short']=(out['rank_short']<=int(limits['SHORT'])) & out['decision_short'].isin(['SHORT_CANDIDATE','WATCH_SHORT'])
    out['execution']='RESEARCH_ONLY'; out['v207_version']=cfg['version']; out['legacy_266_used']=False
    return out


def _audit(out: pd.DataFrame, cfg: dict, fcfg: dict) -> dict:
    return {
        'passed': True,
        'version': cfg['version'],
        'funnel_version': fcfg['version'],
        'rows': len(out),
        'unique_isin': int(out['isin'].astype(str).nunique()),
        'universe_contract': cfg['universe_contract'],
        'legacy_266_used': False,
        'missing_data_policy': cfg['missing_data_policy'],
        'direct_weight_sums': {h: round(sum(map(float,w.values())),8) for h,w in cfg['direct_weights'].items()},
        'topdown_weight_sum': round(sum(map(float,fcfg['context_weights'].values())),8),
        'mean_weight_coverage': {h: round(float(_num(out,f'weight_coverage_{h.lower()}').mean()),4) for h in ['CT','MT','LT','SHORT']},
        'decisions': {h: out[f'decision_{h}'].value_counts().to_dict() for h in ['ct','mt','lt','short']},
        'selection_counts': {h: int(out[f'selection_{h}'].sum()) for h in ['ct','mt','lt','short']},
        'topdown_gate_counts': out['v207_topdown_gate'].value_counts().to_dict(),
        'topdown_mean_coverage': round(float(_num(out,'v207_topdown_coverage').mean()),4),
        'aum_gate_counts': {
            'ct_below_50': int((_num(out,'fund_total_assets_eur_m')<50).sum()),
            'mt_below_50': int((_num(out,'fund_total_assets_eur_m')<50).sum()),
            'lt_below_40': int((_num(out,'fund_total_assets_eur_m')<40).sum()),
            'short_below_80': int((_num(out,'fund_total_assets_eur_m')<80).sum()),
            'missing': int(_num(out,'fund_total_assets_eur_m').isna().sum()),
        },
        'smart_money_rows_present': int(_num(out,'ifs_effective').notna().sum()),
        'smart_money_positive_score_boost_allowed': False,
        'execution': 'RESEARCH_ONLY',
    }


def main() -> None:
    cfg=json.loads(CONFIG.read_text(encoding='utf-8')); fcfg=json.loads(FUNNEL_CONFIG.read_text(encoding='utf-8'))
    source=pd.read_csv(IN,sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    out=build(source,cfg,fcfg)
    OUT.parent.mkdir(parents=True,exist_ok=True); AUDIT.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(OUT,sep=';',index=False,encoding='utf-8-sig')
    out.to_csv(COMPAT_OUT,sep=';',index=False,encoding='utf-8-sig')
    audit=_audit(out,cfg,fcfg); AUDIT.write_text(json.dumps(audit,indent=2,ensure_ascii=False),encoding='utf-8')
    lines=['# V20.7 ETF102 Committee','',f"Universe: **{len(out)} PEA ETF / FINAL_VALIDATED only**  ",'Legacy 266: **OFF / forbidden**  ','Weights: **specific criterion-level CT / MT / LT / Short + V20.7 Top Down**  ','Missing data: **renormalized observed weights; no neutral 50**  ','Smart Money: **negative high-confidence gate only**  ','Execution: **RESEARCH_ONLY**','']
    for hz in ['ct','mt','lt','short']:
        lines += [f'## {hz.upper()}',str(out[f'decision_{hz}'].value_counts().to_dict()),f"Top selection count: {int(out[f'selection_{hz}'].sum())}",'']
    SUMMARY.write_text('\n'.join(lines),encoding='utf-8')
    print('V20_7_ETF102_COMMITTEE_OK',json.dumps(audit,ensure_ascii=False))


if __name__=='__main__':
    main()
