"""HEBDO AT META - orchestrateur gouverné et multi-étages de la branche parallèle."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from v182.audit.pit_loader import PITLoader
from v182.hebdo.false_positive_filter import FalsePositiveFilter
from v182.hebdo.mae_predictor import MAEPredictor
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.expected_value_ranker import ExpectedValueRanker
from v182.hebdo.confirmation_entry import ConfirmationEntry
from v182.hebdo.fp_early_exit import FPEarlyExit
from v182.tct.preopen_enricher import PreopenEnricher

REQUIRED = {
    'ticker','close','sma200','vol_z','drawdown_4w','atr_14_pct',
    'mom_26w_sector','adv_20m_eur'
}
CRITICAL_NUMERIC = ['close','sma200','vol_z','drawdown_4w','atr_14_pct','mom_26w_sector','adv_20m_eur']

class HebdoATMeta:
    """Process Meta par étapes temporelles distinctes."""
    def __init__(self, meta_labeler=None, preopen_enricher=None):
        self.fp = FalsePositiveFilter()
        self.mae = MAEPredictor()
        self.meta = meta_labeler or MetaLabeler()
        self.ev = ExpectedValueRanker()
        self.preopen = preopen_enricher or PreopenEnricher()
        self.confirm = ConfirmationEntry()
        self.early_exit = FPEarlyExit()

    def validate(self, df: pd.DataFrame):
        missing = REQUIRED - set(df.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_META: missing {sorted(missing)}")
        if df.empty:
            raise ValueError("BLOCK_DATA_META: empty universe")
        if df['ticker'].isna().all() or df['ticker'].astype(str).str.strip().eq('').all():
            raise ValueError("BLOCK_DATA_META: no valid ticker")
        all_missing=[c for c in CRITICAL_NUMERIC if df[c].isna().all()]
        if all_missing:
            raise ValueError(f"BLOCK_DATA_META: entirely missing critical columns {all_missing}")

    def run(self, df_features: pd.DataFrame) -> pd.DataFrame:
        self.validate(df_features)
        df = df_features.copy()
        df['ticker']=df['ticker'].astype(str).str.strip()
        df=df[df['ticker'].ne('') & df['ticker'].ne('nan')]
        df=df.drop_duplicates('ticker', keep='last')
        n0 = len(df)
        if n0 == 0:
            raise ValueError("BLOCK_DATA_META: no valid ticker after cleanup")

        for col in CRITICAL_NUMERIC:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        numeric = df[CRITICAL_NUMERIC].to_numpy(dtype=float)
        invalid = ~np.isfinite(numeric).all(axis=1)
        invalid |= (df['close'] <= 0) | (df['sma200'] <= 0) | (df['atr_14_pct'] <= 0) | (df['adv_20m_eur'] <= 0)
        n_invalid = int(invalid.sum())
        df = df.loc[~invalid].copy()
        if df.empty:
            raise ValueError(f"BLOCK_DATA_META: all rows invalid after critical-data validation (initial={n0})")

        df = self.fp.filter_batch(df)
        if df.empty:
            raise ValueError(f"BLOCK_DATA_META: universe fully rejected by false-positive filter (initial={n0})")
        n_after_fp = len(df)
        df['stage_fp_kept'] = True
        df = self.mae.predict_batch(df)
        df = self.meta.predict_proba(df)
        df = self.ev.rank_batch(df)
        if df.empty:
            raise ValueError("BLOCK_DATA_META: empty universe after ranking")
        df['META_STATUS'] = df['tier']
        df['meta_universe_initial'] = n0
        df['meta_invalid_rows_dropped'] = n_invalid
        df['meta_universe_after_fp'] = n_after_fp
        df['process_stage']='RANKED'
        return df

    def run_preopen(self, ranked: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
        if ranked.empty or 'tier' not in ranked.columns:
            raise ValueError('BLOCK_DATA_PREOPEN: ranked candidates missing')
        candidates=ranked[ranked['tier'].isin(['TCT','CT_WATCH'])].copy()
        if candidates.empty:
            candidates['process_stage']='PREOPEN_NO_CANDIDATE'
            return candidates
        out=self.preopen.enrich(candidates, as_of_date)
        out['process_stage']='PREOPEN_ENRICHED'
        return out

    def run_confirmation_j1(self, candidates: pd.DataFrame, next_bars: pd.DataFrame) -> pd.DataFrame:
        if candidates.empty:
            return candidates.copy()
        out=self.confirm.filter_batch_j1(candidates, next_bars)
        out['process_stage']='J1_CONFIRMATION'
        return out

    def check_early_exit(self, entry_price: float, current_bar: dict, days_held: int, sector_bar: dict=None):
        return self.early_exit.check_exit(entry_price, current_bar, days_held, sector_bar)

def _write_block(out: Path, reason: str):
    summary={
        'status':'BLOCK_DATA_META',
        'version':'HEBDO_AT_META_V1',
        'reason':reason,
        'real_orders_enabled':False,
    }
    (out/'HEBDO_AT_META_BLOCK.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--features', required=True)
    p.add_argument('--as-of', required=True, help='Date/heure de décision; cutoff PIT = T-1 22h Europe/Paris')
    p.add_argument('--output-dir', default='outputs/hebdo_meta')
    args=p.parse_args()
    src=Path(args.features); out=Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    if not src.exists() or src.stat().st_size == 0:
        _write_block(out, f"missing features {src}")
        raise SystemExit(2)
    try:
        provenance=PITLoader(root=Path('.'), strict_provenance=True).validate_explicit_provenance(src, pd.Timestamp(args.as_of))
        df=pd.read_csv(src)
        ranked=HebdoATMeta().run(df)
    except (ValueError, FileNotFoundError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        _write_block(out, str(exc))
        raise SystemExit(2)
    ranked.to_csv(out/'HEBDO_AT_META_RANKED.csv', index=False)
    summary={
        'status':'OK', 'version':'HEBDO_AT_META_V1',
        'as_of':str(args.as_of),
        'pit_snapshot_time':provenance['snapshot_time'],
        'pit_cutoff':provenance['cutoff'],
        'pit_provenance_source':provenance['source'],
        'universe_initial':int(ranked['meta_universe_initial'].iloc[0]),
        'invalid_rows_dropped':int(ranked['meta_invalid_rows_dropped'].iloc[0]),
        'universe_after_fp':int(ranked['meta_universe_after_fp'].iloc[0]),
        'TCT':int((ranked['tier']=='TCT').sum()),
        'CT_WATCH':int((ranked['tier']=='CT_WATCH').sum()),
        'EXCLU':int((ranked['tier']=='EXCLU').sum()),
        'meta_model_status': str(ranked['meta_model_status'].iloc[0]) if 'meta_model_status' in ranked else 'UNAVAILABLE',
        'mae_model_status': str(ranked['mae_model_status'].iloc[0]) if 'mae_model_status' in ranked else 'UNAVAILABLE',
        'ev_model_status': str(ranked['ev_model_status'].iloc[0]) if 'ev_model_status' in ranked else 'UNAVAILABLE',
        'real_orders_enabled':False,
    }
    (out/'HEBDO_AT_META_SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))

if __name__ == '__main__':
    main()
