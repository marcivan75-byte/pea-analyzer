"""
v182/tct/preopen_enricher.py
HEBDO AT META - enrichissement préopen fail-closed avec source de quote explicitement injectée.

Contrat du provider:
    provider(tickers: list[str], as_of: pd.Timestamp) -> pd.DataFrame
Colonnes requises: ticker, prev_close, prev_close_time, preopen_price, quote_time.
Le gap préopen reste un signal d'exécution heuristique séparé du score/EV Meta.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


class PreopenEnricher:
    def __init__(self, quote_provider=None, max_quote_age_minutes: int = 30):
        self.quote_provider = quote_provider
        self.max_quote_age_minutes = int(max_quote_age_minutes)
        if self.max_quote_age_minutes < 1:
            raise ValueError('BLOCK_DATA_PREOPEN: max_quote_age_minutes must be >= 1')

    @staticmethod
    def _normalize_as_of(as_of_date) -> pd.Timestamp:
        try:
            ts = pd.Timestamp(as_of_date)
        except Exception as exc:
            raise ValueError(f'BLOCK_DATA_PREOPEN: invalid as_of {as_of_date!r}') from exc
        if pd.isna(ts):
            raise ValueError('BLOCK_DATA_PREOPEN: invalid as_of NaT')
        return ts.tz_localize('Europe/Paris') if ts.tzinfo is None else ts.tz_convert('Europe/Paris')

    @staticmethod
    def _base_rank(df: pd.DataFrame) -> pd.Series:
        if 'EV_net' in df.columns:
            return pd.to_numeric(df['EV_net'], errors='coerce')
        if 'hebdo_score' in df.columns:
            return pd.to_numeric(df['hebdo_score'], errors='coerce')
        return pd.Series(np.nan, index=df.index, dtype=float)

    def enrich(self, df_tct_ct: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
        if 'ticker' not in df_tct_ct.columns:
            raise ValueError('BLOCK_DATA_PREOPEN: ticker missing')
        df = df_tct_ct.copy()
        df['ticker'] = df['ticker'].astype(str).str.strip().str.upper()
        if df['ticker'].isin(['', 'NAN', 'NONE']).any():
            raise ValueError('BLOCK_DATA_PREOPEN: invalid ticker')
        if df['ticker'].duplicated().any():
            raise ValueError('BLOCK_DATA_PREOPEN: duplicate candidates')
        df = df.head(40).copy()
        base = self._base_rank(df)
        df['score_preopen'] = base
        df['preopen_rank_basis'] = np.where(df['EV_net'].notna(), 'EV_NET', 'HEBDO_SCORE') if 'EV_net' in df.columns else ('HEBDO_SCORE' if 'hebdo_score' in df.columns else 'UNAVAILABLE')
        df['preopen_adjustment_status'] = 'HEURISTIC_UNCALIBRATED_NOT_APPLIED_TO_RANK'
        df['news_data_status'] = 'NOT_CONFIGURED'
        df['news_boost'] = 0.0
        df['preopen_boost'] = 0.0

        if df.empty:
            df['gap_overnight'] = pd.Series(dtype=float)
            df['preopen_data_status'] = pd.Series(dtype=object)
            df['preopen_signal'] = pd.Series(dtype=object)
            return df

        as_of = self._normalize_as_of(as_of_date)
        tickers = df['ticker'].tolist()
        if self.quote_provider is None:
            df['gap_overnight'] = np.nan
            df['preopen_quote_time'] = pd.NaT
            df['prev_close_time'] = pd.NaT
            df['preopen_data_status'] = 'BLOCK_DATA_PREOPEN_SOURCE_REQUIRED'
            df['preopen_signal'] = 'BLOCK'
            return df.sort_values('score_preopen', ascending=False, na_position='last')

        quotes = self.quote_provider(tickers, as_of)
        if not isinstance(quotes, pd.DataFrame):
            raise ValueError('BLOCK_DATA_PREOPEN_SOURCE: provider must return DataFrame')
        required = {'ticker','prev_close','prev_close_time','preopen_price','quote_time'}
        missing = required - set(quotes.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_PREOPEN_SOURCE: missing {sorted(missing)}")
        q = quotes.copy()
        q['ticker'] = q['ticker'].astype(str).str.strip().str.upper()
        if q['ticker'].duplicated().any():
            raise ValueError('BLOCK_DATA_PREOPEN_SOURCE: duplicate ticker quotes')
        q['prev_close'] = pd.to_numeric(q['prev_close'], errors='coerce')
        q['preopen_price'] = pd.to_numeric(q['preopen_price'], errors='coerce')
        q['prev_close_time'] = pd.to_datetime(q['prev_close_time'], errors='coerce', utc=True).dt.tz_convert('Europe/Paris')
        q['quote_time'] = pd.to_datetime(q['quote_time'], errors='coerce', utc=True).dt.tz_convert('Europe/Paris')
        q['preopen_data_status'] = 'OK'

        numeric = q[['prev_close','preopen_price']].to_numpy(dtype=float)
        invalid = ~np.isfinite(numeric).all(axis=1)
        invalid |= (q['prev_close'] <= 0) | (q['preopen_price'] <= 0)
        invalid |= q['prev_close_time'].isna() | q['quote_time'].isna()
        invalid |= q['quote_time'] > as_of
        invalid |= q['quote_time'].dt.date != as_of.date()
        invalid |= q['prev_close_time'] >= q['quote_time']
        age_minutes = (as_of - q['quote_time']).dt.total_seconds() / 60.0
        invalid |= (age_minutes < 0) | (age_minutes > self.max_quote_age_minutes)
        q.loc[invalid, 'preopen_data_status'] = 'BLOCK_DATA_PREOPEN_QUOTE'
        q['gap_overnight'] = np.where(
            q['preopen_data_status'].eq('OK'),
            q['preopen_price'] / q['prev_close'] - 1,
            np.nan,
        )

        df = df.merge(
            q[['ticker','gap_overnight','prev_close_time','quote_time','preopen_data_status']],
            on='ticker', how='left'
        ).rename(columns={'quote_time':'preopen_quote_time'})
        df['preopen_data_status'] = df['preopen_data_status'].fillna('BLOCK_DATA_PREOPEN_MISSING_TICKER')
        ok = df['preopen_data_status'].eq('OK')
        df['preopen_signal'] = 'BLOCK'
        df.loc[ok, 'preopen_signal'] = 'NEUTRAL'
        df.loc[ok & (df['gap_overnight'] > 0.02), 'preopen_signal'] = 'GAP_UP_GT_2PCT'
        df.loc[ok & (df['gap_overnight'] < -0.02), 'preopen_signal'] = 'GAP_DOWN_LT_MINUS_2PCT'
        # Compatibilité: le "boost" est journalisé mais n'est PAS ajouté à la note Meta.
        df.loc[ok & (df['gap_overnight'] > 0.02), 'preopen_boost'] = 0.15
        df.loc[ok & (df['gap_overnight'] < -0.02), 'preopen_boost'] = -0.20
        df['_preopen_ok'] = ok.astype(int)
        return df.sort_values(['_preopen_ok','score_preopen'], ascending=[False,False], na_position='last').drop(columns=['_preopen_ok'])
