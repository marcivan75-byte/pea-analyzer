from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'data/reference/V21.0_ACTIONS_PEA_CONFIG.json'
EXCLUSIONS = ROOT / 'data/reference/PEA_ACTIONS_1429_EXCLUSIONS.csv'
SOURCE = ROOT / 'outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv'
SMART = ROOT / 'outputs/V18.3_PEA_ACTIONS_SMART_MONEY_SHADOW.csv'
OUT = ROOT / 'outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv'
AUDIT = ROOT / 'outputs/audit/V21.0_ACTIONS_PREPARE_AUDIT.json'

SM_KEEP = [
    'isin','insider_score','significant_holder_score','short_seller_score','whale_tape_score','wis_raw','wis_effective',
    'smart_money_confidence','smart_money_label','insider_cluster_flag','insider_distinct_buyers','public_short_censored',
    'public_short_pct','short_delta_public','short_holders','short_comparable_holders','volume_z20','dollar_volume_z20','cmf20',
    'obv_slope10','ad_slope10','smart_money_active_scoring_allowed','smart_money_risk_review',
    'smart_money_preorder_block_shadow','smart_money_data_status','smart_money_source_completeness'
]


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors='coerce')


def _first_num(df: pd.DataFrame, fields: list[str]) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for field in fields:
        s = _num(df, field)
        out = out.where(out.notna(), s)
    return out


def _pct_from_decimal(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors='coerce')
    # Direct Yahoo ratios are decimals. Legacy percentages are usually >1 in absolute value.
    return x.where(x.abs() > 1.5, x * 100.0)


def _rank(s: pd.Series, higher: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors='coerce')
    p = x.rank(pct=True, method='average') * 100.0
    return p if higher else 100.0 - p


def _weighted_available(parts: list[tuple[pd.Series, float]]) -> tuple[pd.Series, pd.Series]:
    idx = parts[0][0].index
    num = pd.Series(0.0, index=idx)
    den = pd.Series(0.0, index=idx)
    total = sum(w for _, w in parts)
    for s, w in parts:
        x = pd.to_numeric(s, errors='coerce')
        num += x.fillna(0) * w
        den += x.notna().astype(float) * w
    return (num / den.replace(0, np.nan)).clip(0, 100), (den / total).clip(0, 1)


def _merge_smart(df: pd.DataFrame) -> pd.DataFrame:
    if not SMART.exists():
        return df
    sm = pd.read_csv(SMART, sep=';', dtype=object, encoding='utf-8-sig', low_memory=False)
    if 'isin' not in sm.columns:
        return df
    keep = [c for c in SM_KEEP if c in sm.columns]
    sm = sm[keep].drop_duplicates('isin', keep='last')
    conflicts = [c for c in keep if c != 'isin' and c in df.columns]
    if conflicts:
        df = df.drop(columns=conflicts)
    return df.merge(sm, on='isin', how='left')


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    cfg = json.loads((root / CONFIG.relative_to(ROOT)).read_text(encoding='utf-8'))
    src = pd.read_csv(root / SOURCE.relative_to(ROOT), sep=';', dtype=object, encoding='utf-8-sig', low_memory=False)
    exc = pd.read_csv(root / EXCLUSIONS.relative_to(ROOT), dtype=str)
    if len(src) != int(cfg['source_universe_size']) or src['isin'].astype(str).nunique() != int(cfg['source_universe_size']):
        raise RuntimeError(f"Expected {cfg['source_universe_size']} unique source actions")
    if len(exc) != int(cfg['excluded_isin_count']) or exc['isin'].astype(str).nunique() != int(cfg['excluded_isin_count']):
        raise RuntimeError('Canonical exclusions gate failed')
    src['isin'] = src['isin'].astype(str).str.strip().str.upper()
    excluded = set(exc['isin'].astype(str).str.strip().str.upper())
    df = src.loc[~src['isin'].isin(excluded)].copy()
    if len(df) != int(cfg['canonical_universe_size']) or df['isin'].nunique() != int(cfg['canonical_universe_size']):
        raise RuntimeError('Canonical Actions PEA 1429 gate failed')

    df = _merge_smart(df)
    # Canonical governance columns are added without deleting any inherited field.
    for col, value in [
        ('canonical_universe','PEA_ACTIONS_1429'),('canonical_validation','AUDITED_1486_MINUS_57_EXCLUSIONS'),
        ('canonical_execution_guard','NO_LIVE_EXECUTION'),('v210_version',cfg['version']),('execution','RESEARCH_ONLY')
    ]:
        df[col] = value

    # Canonical/coalesced action fields. No missing value is replaced by a neutral score.
    df['per_forward_v21'] = _first_num(df, ['per_forward','per_forward_yf'])
    df['per_ttm_v21'] = _first_num(df, ['per_ttm','per_ttm_yf'])
    df['pb_v21'] = _first_num(df, ['pb'])
    df['beta_v21'] = _first_num(df, ['beta'])
    df['debt_to_equity_v21'] = _first_num(df, ['debt_to_equity'])
    df['roe_v21_pct'] = _pct_from_decimal(_first_num(df, ['roe_api','roe']))
    df['roa_v21_pct'] = _pct_from_decimal(_first_num(df, ['roa']))
    df['operating_margin_v21_pct'] = _pct_from_decimal(_first_num(df, ['marge_ebit']))
    df['net_margin_v21_pct'] = _pct_from_decimal(_first_num(df, ['marge_nette']))
    df['revenue_growth_v21_pct'] = _pct_from_decimal(_first_num(df, ['croiss_ca_3y']))
    df['earnings_growth_v21_pct'] = _pct_from_decimal(_first_num(df, ['croiss_eps_3y']))
    df['free_cash_flow_v21'] = _first_num(df, ['free_cash_flow'])
    df['market_cap_v21'] = _first_num(df, ['market_cap'])
    df['market_cap'] = _first_num(df, ['market_cap','market_cap_v21'])
    df['dividend_yield_v21_pct'] = _first_num(df, ['dividend_yield_pct'])
    df['payout_ratio_v21_pct'] = _pct_from_decimal(_first_num(df, ['payout_ratio']))
    df['target_mean_v21'] = _first_num(df, ['target_price','target_mean_yf'])
    df['target_low_v21'] = _first_num(df, ['target_low','target_low_yf'])
    df['target_high_v21'] = _first_num(df, ['target_high','target_high_yf'])
    df['n_analysts_v21'] = _first_num(df, ['n_analysts','n_analysts_yf','n_analysts_counts_yf'])
    df['consensus_score_100_v21'] = _first_num(df, ['consensus_score_100','consensus_score_yf'])
    last = _first_num(df, ['last_close','current_price_yf'])
    target = df['target_mean_v21']
    df['target_upside_pct_v21'] = ((target / last) - 1.0) * 100.0
    df.loc[last.le(0) | last.isna() | target.isna(), 'target_upside_pct_v21'] = np.nan
    df['potential_gt_15_flag'] = df['target_upside_pct_v21'].where(df['target_upside_pct_v21'].notna()).ge(15).where(df['target_upside_pct_v21'].notna())
    df['consensus_delta_4w'] = _first_num(df, ['consensus_delta_1m','consensus_delta_yf','consensus_delta'])
    net = _first_num(df, ['net_upgrades_30d'])
    up, down = _num(df,'upgrades_30d'), _num(df,'downgrades_30d')
    df['net_upgrades_30d_v21'] = net.where(net.notna(), up - down)
    df['broker_weighted_revision_30d'] = _first_num(df, ['weighted_target_revision_30d_pct','target_revision_signal_pct','target_change_run_pct'])
    df['sector_v21'] = df.get('sector_yf', pd.Series(index=df.index,dtype=object)).fillna(df.get('sector_yahoo')).fillna(df.get('industry_yf')).fillna(df.get('industry_yahoo'))

    # Smart Money remains shadow-only. It may only produce a negative gate downstream.
    df['action_smart_money_score'] = _first_num(df, ['wis_effective','score_shadow'])
    df['action_smart_money_confidence'] = _first_num(df, ['smart_money_confidence'])
    risk_review = df.get('smart_money_risk_review', pd.Series(False,index=df.index)).astype(str).str.lower().isin({'true','1','yes'})
    df['action_smart_money_gate'] = np.where(risk_review, 'REVIEW_BUY', 'NONE')

    # Market-data liquidity percentile is descriptive + a downstream soft gate.
    volume = _num(df,'volume')
    df['liquidity_percentile'] = volume.rank(pct=True, method='average')
    pea_high = df.get('pea_confidence', pd.Series('',index=df.index)).astype(str).str.upper().str.startswith('HIGH')
    df['pea_validation_gate'] = np.where(pea_high, 'PASS', 'REVIEW_ONLY')
    identity = _num(df,'v182_ticker_validation_confidence_pct') / 100.0
    df['identity_gate'] = np.where(identity.ge(float(cfg['coverage']['identity_min'])), 'PASS', 'REVIEW_ONLY')

    # Enrichment priority is multi-style to avoid a pure-momentum bias.
    technical, tcov = _weighted_available([
        (_rank(_num(df,'perf_1m_pct')), .12), (_rank(_num(df,'perf_3m_pct')), .18),
        (_rank(_num(df,'perf_6m_pct')), .16), (_rank(_num(df,'perf_1y_pct')), .10),
        (_rank(_num(df,'relative_strength')), .16), (_rank(_num(df,'macd_hist')), .08),
        (_rank(_num(df,'rvol20')), .08), (_rank(_num(df,'max_drawdown_1y')), .12)
    ])
    legacy = _rank(_first_num(df,['committee_score_with_analyst_momentum','score_brut']))
    long_style, _ = _weighted_available([
        (_rank(_num(df,'perf_3y_pct')), .35), (_rank(_num(df,'perf_5y_pct')), .35),
        (_rank(_num(df,'dividend_yield_pct')), .15), (_rank(_num(df,'roe_v21_pct')), .15)
    ])
    priority, pcov = _weighted_available([(technical,.50),(legacy,.30),(long_style,.20)])
    df['v210_enrichment_priority_score'] = priority.round(2)
    rank = priority.rank(method='min', ascending=False)
    df['v210_enrichment_priority'] = rank.le(int(cfg['enrichment_priority_limit']))
    df['v210_pre_screen_coverage'] = pcov.round(3)

    # Create the full announced V21 schema now; collection steps fill what they can later.
    missing_schema=[field for field in cfg.get('new_reference_fields', []) if field not in df.columns]
    if missing_schema:
        df=pd.concat([df,pd.DataFrame({field:np.nan for field in missing_schema},index=df.index)],axis=1)
    df['backtest_12m_status'] = 'PENDING_VALIDATION'
    df['backtest_18m_status'] = 'PENDING_VALIDATION'
    df['backtest_36m_status'] = 'PENDING_VALIDATION'

    out_path = root / OUT.relative_to(ROOT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, sep=';', index=False, encoding='utf-8-sig')
    audit = {
        'passed': True, 'version': cfg['version'], 'rows': int(len(df)), 'unique_isin': int(df['isin'].nunique()),
        'source_columns': int(len(src.columns)), 'prepared_columns': int(len(df.columns)),
        'excluded_isin': int(len(excluded)), 'enrichment_priority': int(df['v210_enrichment_priority'].fillna(False).astype(bool).sum()),
        'pea_high_confidence': int(pea_high.sum()), 'pea_review_only': int((~pea_high).sum()),
        'mean_pre_screen_coverage': round(float(pcov.mean()),4),
        'missing_data_policy': cfg['missing_data_policy'], 'neutral_50_imputation': False,
        'smart_money_positive_score_boost_allowed': False,
        'generated_at_utc': datetime.now(timezone.utc).isoformat()
    }
    ap = root / AUDIT.relative_to(ROOT); ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return audit


def main() -> None:
    print('V21_ACTIONS_PREPARE_OK', build())

if __name__ == '__main__':
    main()
