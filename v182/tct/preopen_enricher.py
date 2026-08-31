"""
v182/tct/preopen_enricher.py
HEBDO AT META - enrichissement préopen fail-closed avec source de quote explicitement injectée.

Contrat du provider:
    provider(tickers: list[str], as_of: pd.Timestamp) -> pd.DataFrame
Le DataFrame retourné doit contenir: ticker, prev_close, preopen_price, quote_time.
Aucune donnée journalière `Open` n'est assimilée à une quote préopen.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


class PreopenEnricher:
    def __init__(self, quote_provider=None):
        self.quote_provider=quote_provider

    @staticmethod
    def _normalize_as_of(as_of_date) -> pd.Timestamp:
        ts=pd.Timestamp(as_of_date)
        if ts.tzinfo is None:
            ts=ts.tz_localize('Europe/Paris')
        else:
            ts=ts.tz_convert('Europe/Paris')
        return ts

    def enrich(self, df_tct_ct: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
        if 'ticker' not in df_tct_ct.columns:
            raise ValueError('BLOCK_DATA_PREOPEN: ticker missing')
        df=df_tct_ct.copy().drop_duplicates(subset=['ticker']).head(40)
        if df.empty:
            df['gap_overnight']=pd.Series(dtype=float)
            df['preopen_data_status']=pd.Series(dtype=object)
            df['preopen_boost']=pd.Series(dtype=float)
            df['score_preopen']=pd.Series(dtype=float)
            return df
        as_of=self._normalize_as_of(as_of_date)
        tickers=df['ticker'].astype(str).tolist()

        if self.quote_provider is None:
            df['gap_overnight']=np.nan
            df['preopen_quote_time']=pd.NaT
            df['preopen_data_status']='BLOCK_DATA_PREOPEN_SOURCE_REQUIRED'
            df['news_boost']=0.0
            df['preopen_boost']=0.0
            df['score_preopen']=df['hebdo_score'] if 'hebdo_score' in df.columns else 0.0
            return df.sort_values('score_preopen', ascending=False)

        quotes=self.quote_provider(tickers, as_of)
        if not isinstance(quotes, pd.DataFrame):
            raise ValueError('BLOCK_DATA_PREOPEN_SOURCE: provider must return DataFrame')
        required={'ticker','prev_close','preopen_price','quote_time'}
        missing=required-set(quotes.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_PREOPEN_SOURCE: missing {sorted(missing)}")
        q=quotes.copy()
        q['ticker']=q['ticker'].astype(str)
        if q['ticker'].duplicated().any():
            raise ValueError('BLOCK_DATA_PREOPEN_SOURCE: duplicate ticker quotes')
        q['prev_close']=pd.to_numeric(q['prev_close'], errors='coerce')
        q['preopen_price']=pd.to_numeric(q['preopen_price'], errors='coerce')
        q['quote_time']=pd.to_datetime(q['quote_time'], errors='coerce', utc=True).dt.tz_convert('Europe/Paris')
        q['preopen_data_status']='OK'

        invalid=(~np.isfinite(q[['prev_close','preopen_price']].to_numpy(dtype=float)).all(axis=1))
        invalid |= (q['prev_close']<=0) | (q['preopen_price']<=0) | q['quote_time'].isna()
        invalid |= q['quote_time']>as_of
        # La quote doit appartenir au jour de décision en heure de Paris.
        invalid |= q['quote_time'].dt.date != as_of.date()
        q.loc[invalid,'preopen_data_status']='BLOCK_DATA_PREOPEN_QUOTE'
        q['gap_overnight']=np.where(
            q['preopen_data_status'].eq('OK'),
            q['preopen_price']/q['prev_close']-1,
            np.nan,
        )

        df=df.merge(q[['ticker','gap_overnight','quote_time','preopen_data_status']], on='ticker', how='left')
        df=df.rename(columns={'quote_time':'preopen_quote_time'})
        df['preopen_data_status']=df['preopen_data_status'].fillna('BLOCK_DATA_PREOPEN_MISSING_TICKER')
        df['news_boost']=0.0
        df['preopen_boost']=0.0
        ok=df['preopen_data_status'].eq('OK')
        df.loc[ok & (df['gap_overnight']>0.02), 'preopen_boost']=0.15
        df.loc[ok & (df['gap_overnight']<-0.02), 'preopen_boost']=-0.20
        base=df['hebdo_score'] if 'hebdo_score' in df.columns else pd.Series(0.0,index=df.index)
        df['score_preopen']=base+df['preopen_boost']+df['news_boost']
        return df.sort_values('score_preopen', ascending=False)
