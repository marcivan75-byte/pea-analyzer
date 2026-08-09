from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
EXCLUSIONS = ROOT / 'data' / 'reference' / 'PEA_ACTIONS_1429_EXCLUSIONS.csv'
ENRICHED = ROOT / 'outputs' / 'V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv'
OUT = ROOT / 'outputs' / 'V20.4_GITOK_ACTIONS_1429_MASTER_ENRICHED.csv'
AUDIT = ROOT / 'outputs' / 'audit' / 'V20.4_ACTIONS_1429_MASTER_AUDIT.json'


def build_1429_master(root: Path | None = None) -> dict:
    root = root or ROOT
    exclusions = pd.read_csv(root / EXCLUSIONS.relative_to(ROOT), dtype=str)
    enriched = pd.read_csv(root / ENRICHED.relative_to(ROOT), sep=';', dtype=object, encoding='utf-8-sig')
    if 'isin' not in exclusions.columns or 'isin' not in enriched.columns:
        raise RuntimeError('1429 master requires isin in exclusions and enriched source')
    exclusions['isin'] = exclusions['isin'].astype(str).str.strip().str.upper()
    enriched['isin'] = enriched['isin'].astype(str).str.strip().str.upper()
    if len(exclusions) != 57 or exclusions['isin'].nunique() != 57:
        raise RuntimeError(f'Canonical exclusions invalid: rows={len(exclusions)} unique={exclusions["isin"].nunique()}')
    if enriched['isin'].duplicated().any():
        sort_cols=['isin'] + (['data_trust_pct'] if 'data_trust_pct' in enriched.columns else [])
        asc=[True] + ([False] if len(sort_cols)>1 else [])
        enriched = enriched.sort_values(sort_cols, ascending=asc, na_position='last').drop_duplicates('isin', keep='first')
    exclusion_set=set(exclusions['isin'])
    absent=sorted(exclusion_set-set(enriched['isin']))
    if absent:
        raise RuntimeError(f'{len(absent)} audited exclusions absent from enriched source; first={absent[:10]}')
    out = enriched.loc[~enriched['isin'].isin(exclusion_set)].copy()
    out.insert(1, 'canonical_universe', 'PEA_ACTIONS_1429')
    out.insert(2, 'canonical_validation', 'API_YFINANCE_CONFIRMED_2026_08_09')
    out.insert(3, 'canonical_execution_guard', 'NO_LIVE_EXECUTION')
    out.insert(4, 'canonical_reference_rule', 'V18.2_ENRICHED_MINUS_57_AUDITED_EXCLUSIONS')
    if len(out) != 1429 or out['isin'].nunique() != 1429:
        raise RuntimeError(f'Expected 1429 unique rows, got rows={len(out)} unique={out["isin"].nunique()}')
    if len(out.columns) < 150:
        raise RuntimeError(f'Expected >=150 columns, got {len(out.columns)}')
    (root / OUT.relative_to(ROOT)).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(root / OUT.relative_to(ROOT), sep=';', index=False, encoding='utf-8-sig')
    audit = {
        'rows': int(len(out)),
        'columns': int(len(out.columns)),
        'unique_isin': int(out['isin'].nunique()),
        'excluded_isin': int(len(exclusions)),
        'minimum_columns_gate': 150,
        'source': str(ENRICHED.relative_to(ROOT)),
        'reference': str(EXCLUSIONS.relative_to(ROOT)),
        'reference_rule': '1486 enriched master minus 57 audited non-confirmed ISIN = 1429',
        'smart_money_enabled': False,
        'live_order_execution_enabled': False,
        'passed': bool(len(out)==1429 and out['isin'].nunique()==1429 and len(out.columns)>=150),
    }
    (root / AUDIT.relative_to(ROOT)).parent.mkdir(parents=True, exist_ok=True)
    (root / AUDIT.relative_to(ROOT)).write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return audit


def main() -> None:
    print('V20_4_ACTIONS_1429_MASTER', build_1429_master())


if __name__ == '__main__':
    main()
