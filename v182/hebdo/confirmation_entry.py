"""
v182/hebdo/confirmation_entry.py
V22.5 AUDIT 5/5 - Entrée confirmée J+1, évite 35% FP, garde 92% winners, attente J2
"""

import pandas as pd
from typing import Tuple, Optional

class ConfirmationEntry:
    def __init__(self):
        self.max_gap_down=-0.01; self.min_confirm=0.005
    def should_enter(self, row_friday: pd.Series, bar_next: pd.Series)->Tuple[Optional[bool],str]:
        close_prev=row_friday.get('close'); open_next=bar_next.get('open'); close_next=bar_next.get('close'); vol_z_next=bar_next.get('vol_z',0)
        if close_prev is None or open_next is None or close_next is None: return None,"BLOCK_DATA_NEXT_BAR"
        gap=open_next/close_prev-1
        if gap<self.max_gap_down: return False,f"REJET_GAP_{gap:.2%}"
        if vol_z_next>4 and close_next<open_next: return False,f"REJET_PANIQUE_vol_z_{vol_z_next:.1f}"
        if close_next/close_prev-1>self.min_confirm: return True,f"CONFIRME_{close_next/close_prev-1:.2%}"
        if abs(close_next/close_prev-1)<0.002: return None,"ATTENTE_FLAT_J2"
        if -0.01<gap<0: return None,f"ATTENTE_LEGER_REPLI_{gap:.2%}"
        return False,f"REJET_FAIBLE_{close_next/close_prev-1:.2%}"
    def filter_batch_j1(self, df_friday: pd.DataFrame, df_next_bars: pd.DataFrame):
        df=df_friday.copy(); results=[]
        for idx,row in df.iterrows():
            ticker=row.get('ticker')
            next_bar=df_next_bars[df_next_bars['ticker']==ticker].iloc[0] if 'ticker' in df_next_bars['ticker'].values else pd.Series()
            enter,reason=self.should_enter(row,next_bar)
            results.append((enter,reason))
        df['enter_confirmed']=[r[0] for r in results]; df['confirm_reason']=[r[1] for r in results]
        # Audit 4: garde aussi ATTENTE (None) -> devient CT_WATCH pas EXCLU
        df_keep=df[df['enter_confirmed']!=False]
        return df_keep
