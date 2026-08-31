"""
v182/hebdo/false_positive_filter.py
V22.5 AUDIT 5/5 - Etage 1: 1790->400, soft thresholds + adaptive, garde winners
"""
import pandas as pd, numpy as np
from typing import Tuple

class FalsePositiveFilter:
    def __init__(self):
        self.min_adv_eur=500_000; self.max_debt=2.0; self.min_roe=0.03
    def is_loser_certain(self, row: pd.Series)->Tuple[bool,str]:
        roe=row.get('roe',np.nan); debt=row.get('debt_to_equity',np.nan)
        if pd.notna(roe) and pd.notna(debt):
            if roe<self.min_roe and debt>self.max_debt: return True,"EXCLU_QUALITE_POURRIE"
        close=row.get('close',np.nan); sma200=row.get('sma200',np.nan); dd=row.get('drawdown_4w',0)
        if pd.notna(close) and pd.notna(sma200):
            if close<sma200 and dd<-0.15: return True,"EXCLU_TECH_CASSEE"
        atr=row.get('atr_14_pct',0); vol_z=row.get('vol_z',0)
        if atr>0.06 and vol_z>4: return True,"EXCLU_VOL_PIEGE"
        dte=row.get('days_to_earnings',999)
        if pd.notna(dte) and dte<=5: return True,"EXCLU_EARNINGS_5J"
        adv=row.get('adv_20m_eur', row.get('volume_avg20',0)*row.get('close',0))
        if pd.notna(adv) and adv<self.min_adv_eur: return True,"EXCLU_ILLIQUIDE"
        return False,""
    def filter_batch(self, df: pd.DataFrame)->pd.DataFrame:
        df=df.copy()
        res=df.apply(self.is_loser_certain, axis=1)
        df['is_loser']=[r[0] for r in res]; df['exclu_reason']=[r[1] for r in res]
        # Audit 3: garde winners même si 1 flag si mom_sector>1.5 (évite perdre bons trades)
        mask_keep=(df['is_loser']) & (df.get('mom_26w_sector',0)>1.5)
        df.loc[mask_keep,'is_loser']=False
        df.loc[mask_keep,'exclu_reason']=df.loc[mask_keep,'exclu_reason']+"_OVERRIDDEN_BY_MOM"
        return df[~df['is_loser']].copy()
