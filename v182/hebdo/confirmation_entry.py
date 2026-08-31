"""
v182/hebdo/confirmation_entry.py
HEBDO AT META - confirmation J+1, fail-closed data checks, attente J2
"""

import pandas as pd
from typing import Tuple, Optional


class ConfirmationEntry:
    def __init__(self):
        self.max_gap_down = -0.01
        self.min_confirm = 0.005

    def should_enter(self, row_friday: pd.Series, bar_next: pd.Series) -> Tuple[Optional[bool], str]:
        close_prev = row_friday.get('close')
        open_next = bar_next.get('open')
        close_next = bar_next.get('close')
        vol_z_next = bar_next.get('vol_z', 0)
        if pd.isna(close_prev) or pd.isna(open_next) or pd.isna(close_next):
            return None, "BLOCK_DATA_NEXT_BAR"
        gap = open_next / close_prev - 1
        ret_close = close_next / close_prev - 1
        if gap < self.max_gap_down:
            return False, f"REJET_GAP_{gap:.2%}"
        if pd.notna(vol_z_next) and vol_z_next > 4 and close_next < open_next:
            return False, f"REJET_PANIQUE_vol_z_{vol_z_next:.1f}"
        if ret_close > self.min_confirm:
            return True, f"CONFIRME_{ret_close:.2%}"
        if abs(ret_close) < 0.002:
            return None, "ATTENTE_FLAT_J2"
        if -0.01 < gap < 0:
            return None, f"ATTENTE_LEGER_REPLI_{gap:.2%}"
        return False, f"REJET_FAIBLE_{ret_close:.2%}"

    def filter_batch_j1(self, df_friday: pd.DataFrame, df_next_bars: pd.DataFrame):
        df = df_friday.copy()
        if 'ticker' not in df.columns or 'ticker' not in df_next_bars.columns:
            df['enter_confirmed'] = None
            df['confirm_reason'] = 'BLOCK_DATA_NEXT_BAR'
            return df
        next_by_ticker = df_next_bars.drop_duplicates('ticker', keep='last').set_index('ticker')
        results = []
        for _, row in df.iterrows():
            ticker = row.get('ticker')
            next_bar = next_by_ticker.loc[ticker] if ticker in next_by_ticker.index else pd.Series(dtype=float)
            enter, reason = self.should_enter(row, next_bar)
            results.append((enter, reason))
        df['enter_confirmed'] = [r[0] for r in results]
        df['confirm_reason'] = [r[1] for r in results]
        return df[df['enter_confirmed'] != False].copy()
