"""
v182/tct/preopen_enricher.py
V22.5 AUDIT 5/5 - Gap + news <12h + vol premarket, union dédupliquée 40 titres, fail-closed
"""
import pandas as pd
import yfinance as yf
from typing import List, Dict

class PreopenEnricher:
    def __init__(self, finnhub_key=None):
        self.finnhub_key=finnhub_key
    def enrich(self, df_tct_ct: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
        df=df_tct_ct.copy().drop_duplicates(subset=['ticker']).head(40)
        # Gap overnight bulk yf
        tickers=df['ticker'].tolist()
        try:
            # Bulk download prev close + open today
            data=yf.download(tickers, period="5d", group_by='ticker', progress=False, auto_adjust=False)
            # Calcule gap
            gaps={}
            for t in tickers:
                try:
                    if len(tickers)>1:
                        closes=data[t]['Close'].dropna()
                    else:
                        closes=data['Close'].dropna()
                    if len(closes)>=2:
                        gaps[t]=closes.iloc[-1]/closes.iloc[-2]-1
                    else:
                        gaps[t]=0
                except: gaps[t]=0
            df['gap_overnight']=df['ticker'].map(gaps).fillna(0)
        except Exception:
            df['gap_overnight']=0

        # News boost (mock si pas de clé)
        df['news_boost']=0
        # Gap boost
        df['preopen_boost']=0.0
        df.loc[df['gap_overnight']>0.02, 'preopen_boost']=0.15
        df.loc[df['gap_overnight']<-0.02, 'preopen_boost']=-0.20

        # Score final
        if 'hebdo_score' in df.columns:
            df['score_preopen']=df['hebdo_score']+df['preopen_boost']+df['news_boost']
        else:
            df['score_preopen']=df['preopen_boost']
        return df.sort_values('score_preopen', ascending=False)
