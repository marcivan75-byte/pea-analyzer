from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
REF = ROOT / 'data' / 'reference' / 'PEA_ACTIONS_1429_CANONICAL_ISIN.csv'
ENRICHED = ROOT / 'outputs' / 'V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv'
OUT = ROOT / 'outputs' / 'V20.4_GITOK_ACTIONS_1429_MASTER_ENRICHED.csv'
AUDIT = ROOT / 'outputs' / 'audit' / 'V20.4_ACTIONS_1429_MASTER_AUDIT.json'


def build_1429_master(root: Path | None = None) -> dict:
    root = root or ROOT
    ref = pd.read_csv(root / REF.relative_to(ROOT), dtype=str)
    enriched = pd.read_csv(root / ENRICHED.relative_to(ROOT), sep=';', dtype=object, encoding='utf-8-sig')
    if 'isin' not in ref.columns or 'isin' not in enriched.columns:
        raise RuntimeError('1429 master requires isin in reference and enriched source')
    ref['isin'] = ref['isin'].astype(str).str.strip().str.upper()
    enriched['isin'] = enriched['isin'].astype(str).str.strip().str.upper()
    if len(ref) != 1429 or ref['isin'].nunique() != 1429:
        raise RuntimeError(f'Canonical 1429 reference invalid: rows={len(ref)} unique={ref["isin"].nunique()}')
    if enriched['isin'].duplicated().any():
        enriched = enriched.sort_values(['isin','data_trust_pct'], ascending=[True,False], na_position='last').drop_duplicates('isin', keep='first')
    out = ref.merge(enriched, on='isin', how='left', validate='one_to_one', indicator=True)
    missing = out.loc[out['_merge'] != 'both', 'isin'].tolist()
    if missing:
        raise RuntimeError(f'{len(missing)} canonical 1429 ISIN absent from enriched master; first={missing[:10]}')
    out = out.drop(columns=['_merge'])
    out.insert(1, 'canonical_universe', 'PEA_ACTIONS_1429')
    out.insert(2, 'canonical_validation', 'API_YFINANCE_CONFIRMED')
    out.insert(3, 'canonical_execution_guard', 'NO_LIVE_EXECUTION')
    if len(out) != 1429:
        raise RuntimeError(f'Expected 1429 rows, got {len(out)}')
    if len(out.columns) < 150:
        raise RuntimeError(f'Expected >=150 columns, got {len(out.columns)}')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(root / OUT.relative_to(ROOT), sep=';', index=False, encoding='utf-8-sig')
    audit = {
        'rows': int(len(out)),
        'columns': int(len(out.columns)),
        'unique_isin': int(out['isin'].nunique()),
        'minimum_columns_gate': 150,
        'source': str(ENRICHED.relative_to(ROOT)),
        'reference': str(REF.relative_to(ROOT)),
        'smart_money_enabled': False,
        'live_order_execution_enabled': False,
        'passed': bool(len(out)==1429 and out['isin'].nunique()==1429 and len(out.columns)>=150),
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    (root / AUDIT.relative_to(ROOT)).write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return audit


def main() -> None:
    print('V20_4_ACTIONS_1429_MASTER', build_1429_master())


if __name__ == '__main__':
    main()
