"""
v182/hebdo/hebdo_at_chat_v22_1.py
LEGACY CHAT COMPATIBILITY MODULE.

Sur la branche HEBDO AT META, le runner officiel est v182/hebdo/hebdo_at_meta.py.
Aucune génération synthétique n'est autorisée ici.
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from v182.hebdo.hebdo_at_chat_v22 import HebdoATChatV22
from v182.hebdo.mae_predictor import MAEPredictor

class HebdoATChatV22_1(HebdoATChatV22):
    def __init__(self, root=Path("."), finnhub_key=None):
        super().__init__(root=root, finnhub_key=finnhub_key)
        self.mae_predictor=MAEPredictor(); self.min_roe=0.05; self.max_debt_equity=1.5

    def filter_quality_pea(self, df: pd.DataFrame):
        df=df.copy()
        if 'roe' not in df.columns or 'debt_to_equity' not in df.columns:
            df['EXCLU_QUALITE']=False; return df
        df['roe']=pd.to_numeric(df['roe'], errors='coerce')
        df['debt_to_equity']=pd.to_numeric(df['debt_to_equity'], errors='coerce')
        mask=(df['roe']<self.min_roe)&(df['debt_to_equity']>self.max_debt_equity)
        df['EXCLU_QUALITE']=mask; df.loc[mask,'HEBDO_STATUS']='EXCLU_QUALITE'
        return df

    def double_tri_sector(self, df: pd.DataFrame):
        df=df.copy()
        if 'secteur' not in df.columns:
            return df.sort_values('hebdo_score', ascending=False).head(self.max_tct)
        excluded=['EXCLU_B_CRASH','EXCLU_MAE','EXCLU_QUALITE','BLOCK_NOISE_SMALLCAP']
        df_valid=df.loc[~df['HEBDO_STATUS'].isin(excluded)].copy()
        df_valid['rank_sector']=df_valid.groupby('secteur')['hebdo_score'].rank(ascending=False, method='first')
        df_top2=df_valid[df_valid['rank_sector']<=2]
        df_final=df_top2.sort_values('mom_26w_sector', ascending=False).head(self.max_tct)
        df.loc[df_final.index,'HEBDO_STATUS']='TCT'
        remaining=df_valid[~df_valid.index.isin(df_final.index)]
        df_ct=remaining.sort_values('hebdo_score', ascending=False).head(self.max_ct)
        df.loc[df_ct.index,'HEBDO_STATUS']='CT'
        return df

    def vol_targeting_sizing(self, df_tct: pd.DataFrame, cash_pct=0.2):
        df=df_tct.copy()
        if not 0 <= cash_pct < 1:
            raise ValueError('BLOCK_DATA_SIZING: cash_pct must be in [0,1)')
        if 'atr_14_pct' not in df.columns or df.empty:
            df['weight']=(1-cash_pct)/len(df) if len(df) else 0
            return df
        atr=pd.to_numeric(df['atr_14_pct'], errors='coerce')
        if atr.isna().any() or (atr<=0).any():
            raise ValueError('BLOCK_DATA_SIZING: invalid atr_14_pct')
        inv_vol=1/atr
        df['weight']=inv_vol/inv_vol.sum()*(1-cash_pct)
        return df

    def run_hebdo_v22_1_full(self, as_of_date: str, df_daily_bulk=None, sector_map=None, lasso_weights=None):
        raise RuntimeError(
            'LEGACY_CHAT_RUNNER_DISABLED_ON_META: use v182.hebdo.hebdo_at_meta.HebdoATMeta; '
            'synthetic fallback has been permanently removed.'
        )
