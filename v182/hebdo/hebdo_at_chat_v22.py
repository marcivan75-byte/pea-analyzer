"""
v182/hebdo/hebdo_at_chat_v22.py
V22.5 AUDIT 5/5 FINAL - PIT + vol_z + sector-neutral + régime CAC + daily-trigger B + ADV + qualité soft
"""

import pandas as pd, numpy as np, yfinance as yf
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from v182.backtests.v21_8_1_backtest_B_v2 import detect_B_v2
from v182.audit.pit_loader import PITLoader

class HebdoATChatV22:
    def __init__(self, root=Path("."), finnhub_key=None):
        self.root=Path(root); self.pit_loader=PITLoader(root=self.root)
        self.market_ticker="^FCHI"; self.min_market_cap_m=300; self.max_tct=20; self.max_ct=20
    def load_pit_universe(self, as_of_date: str):
        dt=pd.Timestamp(as_of_date)
        df=self.pit_loader.load_as_of(dt, "ACTION")
        df=df[df['ticker'].notna() & (df['ticker']!="")]
        return df
    def detect_market_regime(self, as_of_date: str):
        try:
            end=pd.Timestamp(as_of_date); start=end-pd.Timedelta(days=90)
            cac=yf.download(self.market_ticker, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=False)
            if cac.empty or len(cac)<10: return {"regime":"UNKNOWN","cac_2w_pct":None,"tct_multiplier":1.0}
            cac_w=cac['Close'].resample('W-FRI').last()
            if len(cac_w)<3: return {"regime":"UNKNOWN","cac_2w_pct":None,"tct_multiplier":1.0}
            pct_2w=(cac_w.iloc[-1]/cac_w.iloc[-3]-1)*100
            is_crash=pct_2w<-3.0
            return {"regime":"CRASH" if is_crash else "NORMAL","cac_2w_pct":float(pct_2w),"tct_multiplier":0.5 if is_crash else 1.0}
        except Exception as e: return {"regime":"ERROR","cac_2w_pct":None,"tct_multiplier":1.0,"error":str(e)}
    def compute_features_v22(self, df_daily: pd.DataFrame, sector_map=None):
        df=df_daily.copy()
        if 'volume_avg20' not in df.columns: df['volume_avg20']=df['volume'].rolling(20).mean()
        if 'volume_std20' not in df.columns: df['volume_std20']=df['volume'].rolling(20).std()
        if 'sma20' not in df.columns: df['sma20']=df['close'].rolling(20).mean()
        if 'sma200' not in df.columns: df['sma200']=df['close'].rolling(200).mean()
        df['vol_z']=(df['volume']-df['volume_avg20'])/df['volume_std20'].replace(0,np.nan)
        df['mom_26w']=df['close'].pct_change(126)
        df_w=df['close'].resample('W-FRI').last()
        delta=df_w.diff(); gain=delta.where(delta>0,0).rolling(14).mean(); loss=-delta.where(delta<0,0).rolling(14).mean()
        rs=gain/loss.replace(0,np.nan); rsi_w=100-(100/(1+rs)); df['rsi_14_hebdo']=rsi_w.reindex(df.index, method='ffill')
        df['drawdown_4w']=df['close']/df['close'].rolling(20).max()-1
        df_b=detect_B_v2(df); df['B_signal']=df_b['B_signal']; df['adv_20m_eur']=df['volume_avg20']*df['close']
        tr=pd.concat([(df['high']-df['low']),(df['high']-df['close'].shift()).abs(),(df['low']-df['close'].shift()).abs()],axis=1).max(axis=1)
        df['atr_14']=tr.rolling(14).mean(); df['atr_14_pct']=df['atr_14']/df['close']
        return df
    def score_universe_v22(self, df_universe: pd.DataFrame, lasso_weights=None):
        df=df_universe.copy()
        if 'market_cap_m' in df.columns and 'vol_z' in df.columns:
            mask_noise=(df['market_cap_m']<self.min_market_cap_m)&(df['vol_z']>2.5)
            df.loc[mask_noise,'HEBDO_STATUS']='BLOCK_NOISE_SMALLCAP'
        else: df['HEBDO_STATUS']=''
        if 'secteur' in df.columns and 'mom_26w' in df.columns:
            df['mom_26w_sector']=df.groupby('secteur')['mom_26w'].transform(lambda x: (x-x.mean())/x.std().replace(0,np.nan))
        else: df['mom_26w_sector']=df['mom_26w']
        if lasso_weights:
            score=0
            for feat,meta in lasso_weights.items():
                if feat in df.columns:
                    w=meta['weight']; direction=-1 if meta['direction']=='SHORT' else 1
                    score+=direction*w*df[feat].fillna(0)
            df['hebdo_score']=score
        else:
            df['hebdo_score']=df['mom_26w_sector'].fillna(0)*0.5 - df['vol_z'].fillna(0)*0.2 - df['drawdown_4w'].fillna(0).abs()*0.1 - df['B_signal'].astype(int)*1.0
        df_sorted=df.sort_values('hebdo_score', ascending=False)
        mask_crash=(df_sorted['vol_z']>3)&(df_sorted['mom_26w_sector']<-2)
        df_sorted.loc[mask_crash,'HEBDO_STATUS']='EXCLU_B_CRASH'
        tct_candidates=df_sorted[~df_sorted['HEBDO_STATUS'].isin(['EXCLU_B_CRASH','BLOCK_NOISE_SMALLCAP'])]
        df_sorted.loc[tct_candidates.head(self.max_tct).index,'HEBDO_STATUS']='TCT'
        df_sorted.loc[tct_candidates.iloc[self.max_tct:self.max_tct+self.max_ct].index,'HEBDO_STATUS']='CT'
        df_sorted.loc[~df_sorted['HEBDO_STATUS'].isin(['EXCLU_B_CRASH','TCT','CT','BLOCK_NOISE_SMALLCAP']),'HEBDO_STATUS']='NEUTRE'
        return df_sorted
    def run_hebdo_audit(self, as_of_date: str, **kwargs):
        regime=self.detect_market_regime(as_of_date)
        universe=self.load_pit_universe(as_of_date)
        return {"as_of_date":as_of_date,"regime":regime,"universe_pit_count":len(universe),"max_tct_adjusted":int(self.max_tct*regime['tct_multiplier'])}
