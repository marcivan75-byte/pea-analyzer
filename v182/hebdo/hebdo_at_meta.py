"""HEBDO AT META - orchestrateur gouverné de la branche parallèle."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd

from v182.hebdo.false_positive_filter import FalsePositiveFilter
from v182.hebdo.mae_predictor import MAEPredictor
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.expected_value_ranker import ExpectedValueRanker

REQUIRED = {
    'ticker','close','sma200','vol_z','drawdown_4w','atr_14_pct',
    'mom_26w_sector','adv_20m_eur'
}

class HebdoATMeta:
    def __init__(self, meta_labeler=None):
        self.fp = FalsePositiveFilter()
        self.mae = MAEPredictor()
        self.meta = meta_labeler or MetaLabeler()
        self.ev = ExpectedValueRanker()

    def validate(self, df: pd.DataFrame):
        missing = REQUIRED - set(df.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_META: missing {sorted(missing)}")
        if df.empty:
            raise ValueError("BLOCK_DATA_META: empty universe")
        if df['ticker'].isna().all() or df['ticker'].astype(str).str.strip().eq('').all():
            raise ValueError("BLOCK_DATA_META: no valid ticker")
        critical=['close','sma200','vol_z','drawdown_4w','atr_14_pct','mom_26w_sector','adv_20m_eur']
        all_missing=[c for c in critical if df[c].isna().all()]
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
        df = self.fp.filter_batch(df)
        if df.empty:
            raise ValueError(f"BLOCK_DATA_META: universe fully rejected by false-positive filter (initial={n0})")
        df['stage_fp_kept'] = True
        df = self.mae.predict_batch(df)
        # MAE is a risk flag, not an automatic hard exclusion at this stage.
        df = self.meta.predict_proba(df)
        df = self.ev.rank_batch(df)
        if df.empty:
            raise ValueError("BLOCK_DATA_META: empty universe after ranking")
        df['META_STATUS'] = df['tier']
        df['meta_universe_initial'] = n0
        df['meta_universe_after_fp'] = len(df)
        return df

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
    p.add_argument('--output-dir', default='outputs/hebdo_meta')
    args=p.parse_args()
    src=Path(args.features); out=Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    if not src.exists() or src.stat().st_size == 0:
        _write_block(out, f"missing features {src}")
        raise SystemExit(2)
    try:
        df=pd.read_csv(src)
        ranked=HebdoATMeta().run(df)
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        _write_block(out, str(exc))
        raise SystemExit(2)
    ranked.to_csv(out/'HEBDO_AT_META_RANKED.csv', index=False)
    summary={
        'status':'OK', 'version':'HEBDO_AT_META_V1',
        'universe_initial':int(ranked['meta_universe_initial'].iloc[0]),
        'universe_after_fp':int(ranked['meta_universe_after_fp'].iloc[0]),
        'TCT':int((ranked['tier']=='TCT').sum()),
        'CT_WATCH':int((ranked['tier']=='CT_WATCH').sum()),
        'EXCLU':int((ranked['tier']=='EXCLU').sum()),
        'meta_model_status': str(ranked['meta_model_status'].iloc[0]) if 'meta_model_status' in ranked else 'UNAVAILABLE',
        'real_orders_enabled':False,
    }
    (out/'HEBDO_AT_META_SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))

if __name__ == '__main__':
    main()
