"""
v182/hebdo/hebdo_at_chat_v22.py
LEGACY CHAT COMPATIBILITY MODULE sur la branche HEBDO AT META.
Le runner officiel est v182/hebdo/hebdo_at_meta.py.
"""

import pandas as pd, numpy as np, yfinance as yf
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from v182.backtests.v21_8_1_backtest_B_v2 import detect_B_v2
from v182.audit.pit_loader import PITLoader

class HebdoATChatV22:
    def __init__(self, root=Path('.'), finnhub_key=None):
        self.root=Path(root)
        self.pit_loader=PITLoader(root=self.root, strict_provenance=True)
        self.market_ticker='^FCHI'; self.min_market_cap_m=300; self.max_tct=20; self.max_ct=20

    def load_pit_universe(self, as_of_date: str):
        df=self.pit_loader.load_as_of(pd.Timestamp(as_of_date),'ACTION')
        if 'ticker' not in df.columns:
            raise ValueError('BLOCK_DATA_LEGACY_CHAT: ticker missing')
        return df[df['ticker'].notna() & df['ticker'].astype(str).str.strip().ne('')].copy()

    def detect_market_regime(self, as_of_date: str):
        try:
            end=pd.Timestamp(as_of_date); start=end-pd.Timedelta(days=90)
            cac=yf.download(self.market_ticker,start=start.strftime('%Y-%m-%d'),end=end.strftime('%Y-%m-%d'),progress=False,auto_adjust=False)
            if cac.empty or len(cac)<10:
                return {'regime':'UNKNOWN','cac_2w_pct':None,'tct_multiplier':0.0}
            close=cac['Close']
            if isinstance(close,pd.DataFrame):
                if close.shape[1]!=1:
                    return {'regime':'UNKNOWN','cac_2w_pct':None,'tct_multiplier':0.0}
                close=close.iloc[:,0]
            cac_w=close.resample('W-FRI').last()
            if len(cac_w)<3:
                return {'regime':'UNKNOWN','cac_2w_pct':None,'tct_multiplier':0.0}
            pct_2w=(cac_w.iloc[-1]/cac_w.iloc[-3]-1)*100
            is_crash=pct_2w<-3.0
            return {'regime':'CRASH' if is_crash else 'NORMAL','cac_2w_pct':float(pct_2w),'tct_multiplier':0.5 if is_crash else 1.0}
        except Exception as e:
            return {'regime':'ERROR','cac_2w_pct':None,'tct_multiplier':0.0,'error':str(e)}

    def compute_features_v22(self, df_daily: pd.DataFrame, sector_map=None):
        df=df_daily.copy()
        if 'ticker' in df.columns and df['ticker'].nunique(dropna=True)>1:
            raise ValueError('BLOCK_DATA_LEGACY_CHAT: compute_features_v22 expects one ticker at a time')
        required={'close','volume','high','low'}
        missing=required-set(df.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_LEGACY_CHAT: missing {sorted(missing)}")
        if not isinstance(df.index,pd.DatetimeIndex):
            if 'date' not in df.columns:
                raise ValueError('BLOCK_DATA_LEGACY_CHAT: DatetimeIndex or date required')
            dates=pd.to_datetime(df['date'],errors='coerce')
            if dates.isna().any():
                raise ValueError('BLOCK_DATA_LEGACY_CHAT: invalid date')
            df=df.set_index(dates)
        df=df.sort_index()
        if 'volume_avg20' not in df.columns: df['volume_avg20']=df['volume'].rolling(20,min_periods=20).mean()
        if 'volume_std20' not in df.columns: df['volume_std20']=df['volume'].rolling(20,min_periods=20).std()
        if 'sma20' not in df.columns: df['sma20']=df['close'].rolling(20,min_periods=20).mean()
        if 'sma200' not in df.columns: df['sma200']=df['close'].rolling(200,min_periods=200).mean()
        df['vol_z']=(df['volume']-df['volume_avg20'])/df['volume_std20'].replace(0,np.nan)
        df['mom_26w']=df['close'].pct_change(126)
        df_w=df['close'].resample('W-FRI').last()
        delta=df_w.diff(); gain=delta.where(delta>0,0).rolling(14,min_periods=14).mean(); loss=-delta.where(delta<0,0).rolling(14,min_periods=14).mean()
        rs=gain/loss.replace(0,np.nan); rsi_w=100-(100/(1+rs)); df['rsi_14_hebdo']=rsi_w.reindex(df.index,method='ffill')
        df['drawdown_4w']=df['close']/df['close'].rolling(20,min_periods=20).max()-1
        df_b=detect_B_v2(df); df['B_signal']=df_b['B_signal']; df['adv_20m_eur']=df['volume_avg20']*df['close']
        prev=df['close'].shift(); tr=pd.concat([(df['high']-df['low']),(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
        df['atr_14']=tr.rolling(14,min_periods=14).mean(); df['atr_14_pct']=df['atr_14']/df['close'].replace(0,np.nan)
        return df

    def score_universe_v22(self, df_universe: pd.DataFrame, lasso_weights=None):
        df=df_universe.copy()
        df['HEBDO_STATUS']=''
        if 'market_cap_m' in df.columns and 'vol_z' in df.columns:
            mask_noise=(df['market_cap_m']<self.min_market_cap_m)&(df['vol_z']>2.5)
            df.loc[mask_noise,'HEBDO_STATUS']='BLOCK_NOISE_SMALLCAP'
        if 'mom_26w' not in df.columns:
            raise ValueError('BLOCK_DATA_LEGACY_CHAT: mom_26w missing')
        if 'secteur' in df.columns:
            def zscore(x):
                sd=x.std()
                return (x-x.mean())/sd if pd.notna(sd) and sd>0 else pd.Series(np.nan,index=x.index)
            df['mom_26w_sector']=df.groupby('secteur')['mom_26w'].transform(zscore)
        else:
            df['mom_26w_sector']=df['mom_26w']
        if lasso_weights:
            score=pd.Series(0.0,index=df.index)
            for feat,meta in lasso_weights.items():
                if feat in df.columns:
                    direction=-1 if meta['direction']=='SHORT' else 1
                    score=score+direction*float(meta['weight'])*pd.to_numeric(df[feat],errors='coerce').fillna(0)
            df['hebdo_score']=score
        else:
            df['hebdo_score']=df['mom_26w_sector'].fillna(0)*0.5-df.get('vol_z',0)*0.2-df.get('drawdown_4w',0).abs()*0.1-df.get('B_signal',False).astype(int)*1.0
        return df.sort_values('hebdo_score',ascending=False)

    def run_hebdo_audit(self, as_of_date: str, **kwargs):
        raise RuntimeError('LEGACY_CHAT_RUNNER_DISABLED_ON_META: use v182.hebdo.hebdo_at_meta.HebdoATMeta')
