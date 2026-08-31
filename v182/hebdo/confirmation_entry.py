"""
v182/hebdo/confirmation_entry.py
HEBDO AT META - confirmation J+1 avec preuve chronologique obligatoire.
"""

import numpy as np
import pandas as pd
from typing import Tuple, Optional


class ConfirmationEntry:
    def __init__(self, max_calendar_gap_days: int = 5):
        self.max_gap_down=-0.01; self.min_confirm=0.005
        self.max_calendar_gap_days=int(max_calendar_gap_days)
        if self.max_calendar_gap_days<1:
            raise ValueError('BLOCK_DATA_CONFIRMATION: max_calendar_gap_days must be >=1')

    def should_enter(self, row_signal: pd.Series, bar_next: pd.Series) -> Tuple[Optional[bool], str]:
        close_prev=pd.to_numeric(pd.Series([row_signal.get('close')]),errors='coerce').iloc[0]
        open_next=pd.to_numeric(pd.Series([bar_next.get('open')]),errors='coerce').iloc[0]
        close_next=pd.to_numeric(pd.Series([bar_next.get('close')]),errors='coerce').iloc[0]
        vol_z_next=pd.to_numeric(pd.Series([bar_next.get('vol_z',np.nan)]),errors='coerce').iloc[0]
        if any(pd.isna(x) for x in [close_prev,open_next,close_next]) or close_prev<=0 or open_next<=0 or close_next<=0:
            return None,'BLOCK_DATA_NEXT_BAR'
        gap=open_next/close_prev-1; ret_close=close_next/close_prev-1
        if gap<self.max_gap_down: return False,f'REJET_GAP_{gap:.2%}'
        if pd.notna(vol_z_next) and vol_z_next>4 and close_next<open_next: return False,f'REJET_PANIQUE_vol_z_{vol_z_next:.1f}'
        if ret_close>self.min_confirm: return True,f'CONFIRME_{ret_close:.2%}'
        if abs(ret_close)<0.002: return None,'ATTENTE_FLAT_J2'
        if -0.01<gap<0: return None,f'ATTENTE_LEGER_REPLI_{gap:.2%}'
        return False,f'REJET_FAIBLE_{ret_close:.2%}'

    def _next_bar_for_row(self,row:pd.Series,bars:pd.DataFrame)->Tuple[pd.Series,Optional[str]]:
        ticker=row.get('ticker')
        subset=bars[bars['ticker'].eq(ticker)].copy()
        if subset.empty: return pd.Series(dtype=float),'BLOCK_DATA_NEXT_BAR'
        if 'date' not in row.index or 'date' not in subset.columns or pd.isna(row.get('date')):
            return pd.Series(dtype=float),'BLOCK_DATA_NEXT_BAR_DATE_REQUIRED'
        signal_date=pd.to_datetime(row.get('date'),errors='coerce',utc=True)
        if pd.isna(signal_date): return pd.Series(dtype=float),'BLOCK_DATA_NEXT_BAR_DATE_REQUIRED'
        subset['_date']=pd.to_datetime(subset['date'],errors='coerce',utc=True)
        if subset['_date'].isna().any():
            return pd.Series(dtype=float),'BLOCK_DATA_NEXT_BAR_INVALID_DATE'
        subset=subset[subset['_date']>signal_date].sort_values('_date')
        if subset.empty: return pd.Series(dtype=float),'BLOCK_DATA_NEXT_BAR'
        next_row=subset.iloc[0]
        gap_days=(next_row['_date']-signal_date).total_seconds()/86400
        if gap_days>self.max_calendar_gap_days:
            return pd.Series(dtype=float),f'BLOCK_DATA_NEXT_BAR_STALE_{gap_days:.1f}d'
        return next_row.drop(labels=['_date']),None

    def filter_batch_j1(self,df_friday:pd.DataFrame,df_next_bars:pd.DataFrame):
        df=df_friday.copy()
        if 'ticker' not in df.columns or 'ticker' not in df_next_bars.columns:
            df['enter_confirmed']=None; df['confirm_reason']='BLOCK_DATA_NEXT_BAR'; return df
        results=[]
        for _,row in df.iterrows():
            next_bar,block=self._next_bar_for_row(row,df_next_bars)
            results.append((None,block) if block else self.should_enter(row,next_bar))
        df['enter_confirmed']=[r[0] for r in results]; df['confirm_reason']=[r[1] for r in results]
        return df[df['enter_confirmed']!=False].copy()
