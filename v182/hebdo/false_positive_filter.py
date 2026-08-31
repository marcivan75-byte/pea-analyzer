"""
v182/hebdo/false_positive_filter.py
HEBDO AT META - étage 1 faux positifs, règles conservatrices et auditables.
"""
import pandas as pd, numpy as np
from typing import Tuple

class FalsePositiveFilter:
    def __init__(self):
        self.min_adv_eur=500_000; self.max_debt=2.0; self.min_roe=0.03

    def is_loser_certain(self, row: pd.Series)->Tuple[bool,str]:
        roe=row.get('roe',np.nan); debt=row.get('debt_to_equity',np.nan)
        if pd.notna(roe) and pd.notna(debt):
            if roe<self.min_roe and debt>self.max_debt:
                return True,"EXCLU_QUALITE_POURRIE"

        close=row.get('close',np.nan); sma200=row.get('sma200',np.nan); dd=row.get('drawdown_4w',0)
        if pd.notna(close) and pd.notna(sma200):
            if close<sma200 and dd<-0.15:
                return True,"EXCLU_TECH_CASSEE"

        atr=row.get('atr_14_pct',0); vol_z=row.get('vol_z',0)
        if pd.notna(atr) and pd.notna(vol_z) and atr>0.06 and vol_z>4:
            return True,"EXCLU_VOL_PIEGE"

        dte=row.get('days_to_earnings',np.nan)
        if pd.notna(dte) and 0 <= dte <= 5:
            return True,"EXCLU_EARNINGS_5J"

        adv=row.get('adv_20m_eur',np.nan)
        if pd.isna(adv):
            volume_avg20=row.get('volume_avg20',np.nan)
            if pd.notna(volume_avg20) and pd.notna(close):
                adv=volume_avg20*close
        if pd.isna(adv):
            return True,"BLOCK_LIQUIDITY_DATA"
        if adv<self.min_adv_eur:
            return True,"EXCLU_ILLIQUIDE"
        return False,""

    def filter_batch(self, df: pd.DataFrame)->pd.DataFrame:
        df=df.copy()
        res=df.apply(self.is_loser_certain, axis=1)
        df['is_loser']=[r[0] for r in res]
        df['exclu_reason']=[r[1] for r in res]
        # Sauvegarde uniquement les exclusions de signal, jamais un blocage de données.
        overridable=~df['exclu_reason'].str.startswith('BLOCK_', na=False)
        mask_keep=(df['is_loser']) & overridable & (df.get('mom_26w_sector',0)>1.5)
        df.loc[mask_keep,'is_loser']=False
        df.loc[mask_keep,'exclu_reason']=df.loc[mask_keep,'exclu_reason']+"_OVERRIDDEN_BY_MOM"
        return df[~df['is_loser']].copy()
