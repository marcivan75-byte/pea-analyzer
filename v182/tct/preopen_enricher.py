"""
v182/tct/preopen_enricher.py
HEBDO AT META - enrichissement préopen as-of aware, vrai gap open/close précédent, statut données explicite.
"""
import pandas as pd
import yfinance as yf


class PreopenEnricher:
    def __init__(self, finnhub_key=None):
        self.finnhub_key=finnhub_key

    def enrich(self, df_tct_ct: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
        if 'ticker' not in df_tct_ct.columns:
            raise ValueError('BLOCK_DATA_PREOPEN: ticker missing')
        df=df_tct_ct.copy().drop_duplicates(subset=['ticker']).head(40)
        tickers=df['ticker'].astype(str).tolist()
        as_of=pd.Timestamp(as_of_date)
        if as_of.tzinfo is not None:
            as_of=as_of.tz_convert('Europe/Paris').tz_localize(None)
        start=(as_of.normalize()-pd.Timedelta(days=10)).date().isoformat()
        end=(as_of.normalize()+pd.Timedelta(days=1)).date().isoformat()

        gaps={}; status={}
        try:
            data=yf.download(
                tickers, start=start, end=end, group_by='ticker',
                progress=False, auto_adjust=False
            )
            for t in tickers:
                try:
                    frame=data[t] if len(tickers)>1 else data
                    frame=frame.dropna(how='all')
                    if len(frame)<2 or 'Open' not in frame or 'Close' not in frame:
                        gaps[t]=float('nan'); status[t]='BLOCK_DATA_PREOPEN'
                        continue
                    latest=frame.iloc[-1]
                    prev=frame.iloc[-2]
                    open_today=latest['Open']; prev_close=prev['Close']
                    if pd.isna(open_today) or pd.isna(prev_close) or prev_close<=0:
                        gaps[t]=float('nan'); status[t]='BLOCK_DATA_PREOPEN'
                    else:
                        gaps[t]=float(open_today/prev_close-1)
                        status[t]='OK'
                except (KeyError, TypeError, ValueError, IndexError):
                    gaps[t]=float('nan'); status[t]='BLOCK_DATA_PREOPEN'
        except Exception:
            for t in tickers:
                gaps[t]=float('nan'); status[t]='BLOCK_DATA_PREOPEN_SOURCE'

        df['gap_overnight']=df['ticker'].map(gaps)
        df['preopen_data_status']=df['ticker'].map(status).fillna('BLOCK_DATA_PREOPEN')
        df['news_boost']=0.0
        df['preopen_boost']=0.0
        ok=df['preopen_data_status'].eq('OK')
        df.loc[ok & (df['gap_overnight']>0.02), 'preopen_boost']=0.15
        df.loc[ok & (df['gap_overnight']<-0.02), 'preopen_boost']=-0.20
        if 'hebdo_score' in df.columns:
            df['score_preopen']=df['hebdo_score']+df['preopen_boost']+df['news_boost']
        else:
            df['score_preopen']=df['preopen_boost']
        return df.sort_values('score_preopen', ascending=False)
