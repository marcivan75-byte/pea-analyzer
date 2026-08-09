from __future__ import annotations

from pathlib import Path
import csv
import re
import unicodedata

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
ALIASES = ROOT / 'data' / 'reference' / 'ETF_QUARANTINE_ALIAS_RECONCILIATION.csv'


def isin_valid(value: object) -> bool:
    s = str(value or '').strip().upper()
    if not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]', s):
        return False
    expanded = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in s)
    total = 0
    for i, ch in enumerate(expanded[::-1]):
        x = int(ch) * (2 if i % 2 else 1)
        total += x // 10 + x % 10
    return total % 10 == 0


def load_aliases() -> pd.DataFrame:
    df = pd.read_csv(ALIASES, dtype=object)
    required = {'bad_isin','canonical_isin','canonical_ticker','status'}
    if not required.issubset(df.columns):
        raise RuntimeError(f'Missing alias fields: {required-set(df.columns)}')
    if not df['canonical_isin'].map(isin_valid).all():
        raise RuntimeError('Alias map contains invalid canonical ISIN')
    if not df['status'].eq('RECONCILE_ALIAS_EXISTING').all():
        raise RuntimeError('Unexpected alias reconciliation status')
    return df


def reconcile(quarantine: pd.DataFrame, canonical: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    aliases = load_aliases()
    canon = canonical.copy()
    if 'isin' not in canon.columns:
        raise RuntimeError('Canonical ETF reference missing isin')
    if not canon['isin'].map(isin_valid).all():
        raise RuntimeError('Canonical ETF reference contains invalid ISIN')
    canon_isins = set(canon['isin'].astype(str))
    bad_to_good = dict(zip(aliases['bad_isin'].astype(str), aliases['canonical_isin'].astype(str)))
    audit_rows=[]
    for _, row in quarantine.iterrows():
        bad=str(row.get('ISIN','')).strip()
        good=bad_to_good.get(bad)
        if good:
            if good not in canon_isins:
                raise RuntimeError(f'Alias target not present in canonical reference: {good}')
            audit_rows.append({'bad_isin':bad,'canonical_isin':good,'result':'ALIAS_RECONCILED_EXISTING_NO_NEW_ROW'})
        else:
            audit_rows.append({'bad_isin':bad,'canonical_isin':'','result':'KEEP_QUARANTINE'})
    return canon, pd.DataFrame(audit_rows)


if __name__ == '__main__':
    print(load_aliases().to_string(index=False))
