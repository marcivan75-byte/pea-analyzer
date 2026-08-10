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
ADDITIONS = ROOT / 'outputs/V20.4_ACTIONS_506_CANDIDATES_TO_INTEGRATE.csv'
SMART = ROOT / 'outputs/V18.3_PEA_ACTIONS_SMART_MONEY_SHADOW.csv'
OUT = ROOT / 'outputs/V21.0_ACTIONS_PEA_1829_PREPARED.csv'
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


def _validated_additions(base_columns: list[str], cfg: dict, root: Path) -> pd.DataFrame:
    path = root / ADDITIONS.relative_to(ROOT)
    if not path.exists():
        raise RuntimeError(f'Missing validated 400 additions: {path}')
    add = pd.read_csv(path, sep=';', dtype=object, encoding='utf-8-sig', low_memory=False)
    expected = int(cfg.get('validated_additions_count', 400))
    if len(add) != expected or add['isin'].astype(str).nunique() != expected:
        raise RuntimeError(f'Validated additions gate failed: rows={len(add)} unique={add["isin"].astype(str).nunique()} expected={expected}')
    if 'status' in add.columns and not add['status'].astype(str).eq('INTEGRER').all():
        raise RuntimeError('Validated additions contain non-INTEGRER rows')
    out = pd.DataFrame(index=add.index, columns=base_columns, dtype=object)
    out.loc[:, :] = pd.NA
    out['isin'] = add['isin'].astype(str).str.strip().str.upper()
    out['name'] = add.get('yahoo_name', pd.Series(index=add.index, dtype=object))
    out['yahoo_ticker'] = add.get('yahoo_ticker', pd.Series(index=add.index, dtype=object))
    out['country'] = add.get('country', pd.Series(index=add.index, dtype=object))
    if 'exchange' in out.columns:
        out['exchange'] = add.get('exchange', pd.Series(index=add.index, dtype=object))
    if 'last_close' in out.columns:
        out['last_close'] = pd.to_numeric(add.get('last_price'), errors='coerce')
    if 'current_price_yf' in out.columns:
        out['current_price_yf'] = pd.to_numeric(add.get('last_price'), errors='coerce')
    if 'volume' in out.columns:
        out['volume'] = pd.to_numeric(add.get('avg_volume_20d'), errors='coerce')
    out['asset_class'] = 'ACTION'
    out['pea_type'] = 'PEA_ACTION_VALIDATED_ADDITION'
    out['pea_confidence'] = pd.to_numeric(add.get('pea_confidence'), errors='coerce')
    out['v182_ticker_validation_confidence_pct'] = pd.to_numeric(add.get('identity_confidence'), errors='coerce') * 100.0
    for field, value in {
        'map_status': 'VALIDATED_QUARANTINE_400',
        'etage0_status': 'PASS',
        'execution': 'RESEARCH_ONLY',
        'decision': 'RESEARCH_ONLY',
        'region': 'EEA',
        'sources': 'V20.4 quarantine validation; Yahoo identity/history seed',
        'qa_status': 'PASS_VALIDATED_ADDITION',
        'v182_ticker_status': 'VALIDATED_QUARANTINE_400',
        'v182_ticker_validation_source': 'V20.4 Actions 506 quarantine validation run 31292369240',
        'v182_ticker_validation_class': 'AUTOMATED_HIGH_CONFIDENCE',
        'final_reference_status': 'INTEGRATED',
        'final_reference_origin': 'QUARANTINE_506_VALIDATED_400',
    }.items():
        if field in out.columns:
            out[field] = value
    return out


def _pea_high_confidence(df: pd.DataFrame, cfg: dict) -> pd.Series:
    raw = df.get('pea_confidence', pd.Series('', index=df.index))
    text_high = raw.astype(str).str.upper().str.startswith('HIGH')
    numeric = pd.to_numeric(raw, errors='coerce')
    threshold = float(cfg.get('coverage', {}).get('pea_numeric_high_confidence_min', 0.90))
    numeric_high = numeric.ge(threshold) | numeric.ge(threshold * 100.0)
    return text_high | numeric_high.fillna(False)


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
    base = src.loc[~src['isin'].isin(excluded)].copy()
    expected_base = int(cfg['source_universe_size']) - int(cfg['excluded_isin_count'])
    if len(base) != expected_base or base['isin'].nunique() != expected_base:
        raise RuntimeError(f'Canonical base gate failed: expected {expected_base}')

    additions = _validated_additions(list(base.columns), cfg, root)
    overlap = set(base['isin']) & set(additions['isin'])
    if overlap:
        raise RuntimeError(f'Validated additions overlap canonical base: {len(overlap)}')
    df = pd.concat([base, additions], ignore_index=True, sort=False)
    if len(df) != int(cfg['canonical_universe_size']) or df['isin'].nunique() != int(cfg['canonical_universe_size']):
        raise RuntimeError(f'Canonical Actions PEA {cfg["canonical_universe_size"]} gate failed')

    df = _merge_smart(df)
    df['canonical_universe'] = 'PEA_ACTIONS_1829'
    df['canonical_validation'] = np.where(
        df['isin'].isin(set(additions['isin'])),
        'VALIDATED_400_QUARANTINE_RUN_31292369240',
        'AUDITED_1486_MINUS_57_EXCLUSIONS',
    )
    df['canonical_execution_guard'] = 'NO_LIVE_EXECUTION'
    df['v210_version'] = cfg['version']
    df['execution'] = 'RESEARCH_ONLY'

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
    df['potential_gt_15_flag'] = df['target_upside_pct_v21'].ge(15).where(df['target_upside_pct_v21'].notna())
    df['consensus_delta_4w'] = _first_num(df, ['consensus_delta_1m','consensus_delta_yf','consensus_delta'])
    net = _first_num(df, ['net_upgrades_30d'])
    up, down = _num(df,'upgrades_30d'), _num(df,'downgrades_30d')
    df['net_upgrades_30d_v21'] = net.where(net.notna(), up - down)
    df['broker_weighted_revision_30d'] = _first_num(df, ['weighted_target_revision_30d_pct','target_revision_signal_pct','target_change_run_pct'])
    sector = df.get('sector_yf', pd.Series(index=df.index, dtype=object))
    for alt in ['sector_yahoo','industry_yf','industry_yahoo']:
        if alt in df.columns:
            sector = sector.fillna(df[alt])
    df['sector_v21'] = sector

    df['action_smart_money_score'] = _first_num(df, ['wis_effective','score_shadow'])
    df['action_smart_money_confidence'] = _first_num(df, ['smart_money_confidence'])
    risk_review = df.get('smart_money_risk_review', pd.Series(False,index=df.index)).astype(str).str.lower().isin({'true','1','yes'})
    df['action_smart_money_gate'] = np.where(risk_review, 'REVIEW_BUY', 'NONE')

    volume = _num(df,'volume')
    df['liquidity_percentile'] = volume.rank(pct=True, method='average')
    pea_high = _pea_high_confidence(df, cfg)
    df['pea_validation_gate'] = np.where(pea_high, 'PASS', 'REVIEW_ONLY')
    identity = _num(df,'v182_ticker_validation_confidence_pct') / 100.0
    df['identity_gate'] = np.where(identity.ge(float(cfg['coverage']['identity_min'])), 'PASS', 'REVIEW_ONLY')

    technical, _ = _weighted_available([
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
    # New validated additions with sparse inherited fields still remain eligible for direct enrichment.
    is_addition = df['isin'].isin(set(additions['isin']))
    priority = priority.where(~is_addition, priority.fillna(0) + 60.0)
    df['v210_enrichment_priority_score'] = priority.round(2)
    rank = priority.rank(method='min', ascending=False)
    df['v210_enrichment_priority'] = rank.le(int(cfg['enrichment_priority_limit'])) | is_addition
    df['v210_pre_screen_coverage'] = pcov.round(3)

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
        'source_rows': int(len(src)), 'base_after_exclusions': int(len(base)), 'validated_additions': int(len(additions)),
        'source_columns': int(len(src.columns)), 'prepared_columns': int(len(df.columns)),
        'excluded_isin': int(len(excluded)), 'enrichment_priority': int(df['v210_enrichment_priority'].fillna(False).astype(bool).sum()),
        'pea_high_confidence': int(pea_high.sum()), 'pea_review_only': int((~pea_high).sum()),
        'mean_pre_screen_coverage': round(float(pcov.mean()),4),
        'universe_formula': cfg.get('universe_formula'),
        'missing_data_policy': cfg['missing_data_policy'], 'neutral_50_imputation': False,
        'smart_money_positive_score_boost_allowed': False,
        'smart_money_missing_new_400_not_imputed': True,
        'generated_at_utc': datetime.now(timezone.utc).isoformat()
    }
    ap = root / AUDIT.relative_to(ROOT); ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return audit


def main() -> None:
    print('V21_ACTIONS_PREPARE_1829_OK', build())

if __name__ == '__main__':
    main()
