"""
v182/hebdo/confirmation_entry.py
HEBDO AT META - confirmation J+1 avec sélection chronologique sûre et fail-closed sur ambiguïté.
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
        if pd.isna(close_prev) or pd.isna(open_next) or pd.isna(close_next) or close_prev <= 0:
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

    def _next_bar_for_row(self, row: pd.Series, bars: pd.DataFrame) -> Tuple[pd.Series, Optional[str]]:
        ticker = row.get('ticker')
        subset = bars[bars['ticker'].eq(ticker)].copy()
        if subset.empty:
            return pd.Series(dtype=float), 'BLOCK_DATA_NEXT_BAR'

        if 'date' in row.index and 'date' in subset.columns and pd.notna(row.get('date')):
            friday_date = pd.to_datetime(row.get('date'), errors='coerce', utc=True)
            subset['_date'] = pd.to_datetime(subset['date'], errors='coerce', utc=True)
            subset = subset[subset['_date'].notna() & (subset['_date'] > friday_date)].sort_values('_date')
            if subset.empty:
                return pd.Series(dtype=float), 'BLOCK_DATA_NEXT_BAR'
            return subset.iloc[0].drop(labels=['_date']), None

        # Sans date explicite, plusieurs barres sont ambiguës : ne jamais choisir arbitrairement.
        if len(subset) != 1:
            return pd.Series(dtype=float), 'BLOCK_DATA_NEXT_BAR_AMBIGUOUS'
        return subset.iloc[0], None

    def filter_batch_j1(self, df_friday: pd.DataFrame, df_next_bars: pd.DataFrame):
        df = df_friday.copy()
        if 'ticker' not in df.columns or 'ticker' not in df_next_bars.columns:
            df['enter_confirmed'] = None
            df['confirm_reason'] = 'BLOCK_DATA_NEXT_BAR'
            return df
        results = []
        for _, row in df.iterrows():
            next_bar, block_reason = self._next_bar_for_row(row, df_next_bars)
            if block_reason:
                results.append((None, block_reason))
                continue
            enter, reason = self.should_enter(row, next_bar)
            results.append((enter, reason))
        df['enter_confirmed'] = [r[0] for r in results]
        df['confirm_reason'] = [r[1] for r in results]
        return df[df['enter_confirmed'] != False].copy()
