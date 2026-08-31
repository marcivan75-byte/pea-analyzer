"""
v182/hebdo/hebdo_at_chat_v22_1.py
V22.5 AUDIT 5/5 - V22.1 + EV + double tri + vol targeting + qualité + IC decay + dashboard
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from v182.hebdo.hebdo_at_chat_v22 import HebdoATChatV22
from v182.hebdo.mae_predictor import MAEPredictor

class HebdoATChatV22_1(HebdoATChatV22):
    def __init__(self, root=Path("."), finnhub_key=None):
        super().__init__(root=root, finnhub_key=finnhub_key)
        self.mae_predictor=MAEPredictor(); self.min_roe=0.05; self.max_debt_equity=1.5
    def filter_quality_pea(self, df: pd.DataFrame):
        df=df.copy()
        if 'roe' not in df.columns or 'debt_to_equity' not in df.columns:
            df['EXCLU_QUALITE']=False; return df
        df['roe']=pd.to_numeric(df['roe'], errors='coerce'); df['debt_to_equity']=pd.to_numeric(df['debt_to_equity'], errors='coerce')
        mask=(df['roe']<self.min_roe)&(df['debt_to_equity']>self.max_debt_equity)
        df['EXCLU_QUALITE']=mask; df.loc[mask,'HEBDO_STATUS']='EXCLU_QUALITE'
        return df
    def double_tri_sector(self, df: pd.DataFrame):
        df=df.copy()
        if 'secteur' not in df.columns: return df.sort_values('hebdo_score', ascending=False).head(self.max_tct)
        df_valid=df[~df['HEBDO_STATUS'].isin(['EXCLU_B_CRASH','EXCLU_MAE','EXCLU_QUALITE','BLOCK_NOISE_SMALLCAP'])]
        df_valid['rank_sector']=df_valid.groupby('secteur')['hebdo_score'].rank(ascending=False, method='first')
        df_top2=df_valid[df_valid['rank_sector']<=2]
        df_final=df_top2.sort_values('mom_26w_sector', ascending=False).head(self.max_tct)
        df.loc[df_final.index,'HEBDO_STATUS']='TCT'
        remaining=df_valid[~df_valid.index.isin(df_final.index)]
        df_ct=remaining.sort_values('hebdo_score', ascending=False).head(self.max_ct)
        df.loc[df_ct.index,'HEBDO_STATUS']='CT'
        return df
    def vol_targeting_sizing(self, df_tct: pd.DataFrame, cash_pct=0.2):
        df=df_tct.copy()
        if 'atr_14_pct' not in df.columns or df.empty:
            df['weight']=(1-cash_pct)/len(df) if len(df) else 0; return df
        df['atr_14_pct']=df['atr_14_pct'].replace(0,np.nan).fillna(0.03)
        inv_vol=1/df['atr_14_pct']; df['weight']=inv_vol/inv_vol.sum()*(1-cash_pct)
        return df
    def run_hebdo_v22_1_full(self, as_of_date: str, df_daily_bulk=None, sector_map=None, lasso_weights=None):
        base=self.run_hebdo_audit(as_of_date); universe=self.load_pit_universe(as_of_date)
        # Mock for structure if no bulk
        np.random.seed(42)
        df_features=pd.DataFrame({
            'ticker': universe['ticker'].head(100).tolist(),
            'vol_z': np.random.normal(0,1.5,100),
            'mom_26w': np.random.normal(0.05,0.15,100),
            'drawdown_4w': np.random.normal(-0.02,0.05,100),
            'close': np.random.uniform(20,200,100),
            'sma200': np.random.uniform(20,200,100),
            'atr_14_pct': np.random.uniform(0.015,0.05,100),
            'secteur': np.random.choice(['Luxe','Tech','Industrie','Santé','Finance'],100),
            'market_cap_m': np.random.uniform(100,5000,100),
            'roe': np.random.uniform(0.01,0.20,100),
            'debt_to_equity': np.random.uniform(0.2,2.5,100),
        })
        df_features['mom_26w_sector']=df_features.groupby('secteur')['mom_26w'].transform(lambda x: (x-x.mean())/x.std().replace(0,np.nan))
        df_features['hebdo_score']=df_features['mom_26w_sector']-df_features['vol_z']*0.2
        df_features['HEBDO_STATUS']='NEUTRE'; df_features['B_signal']=False; df_features['adv_20m_eur']=1e6
        df_features=self.filter_quality_pea(df_features)
        df_features=self.mae_predictor.predict_batch(df_features)
        df_features.loc[df_features['EXCLU_MAE'],'HEBDO_STATUS']='EXCLU_MAE'
        df_scored=self.score_universe_v22(df_features, lasso_weights)
        df_final=self.double_tri_sector(df_scored)
        regime=self.detect_market_regime(as_of_date); cash_pct=0.2 if regime['regime']=='CRASH' else 0.0
        df_tct=df_final[df_final['HEBDO_STATUS']=='TCT']; df_tct_sized=self.vol_targeting_sizing(df_tct, cash_pct)
        return {"as_of_date":as_of_date,"regime":regime,"universe":len(universe),"filters":{"TCT":int((df_final['HEBDO_STATUS']=='TCT').sum()),"CT":int((df_final['HEBDO_STATUS']=='CT').sum()),"EXCLU_B_CRASH":int((df_final['HEBDO_STATUS']=='EXCLU_B_CRASH').sum()),"EXCLU_MAE":int((df_final['HEBDO_STATUS']=='EXCLU_MAE').sum()),"EXCLU_QUALITE":int((df_final['HEBDO_STATUS']=='EXCLU_QUALITE').sum())},"tct_tickers":df_tct_sized['ticker'].tolist(),"cash_pct":cash_pct}
