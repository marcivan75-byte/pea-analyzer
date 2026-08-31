"""
v182/hebdo/mae_predictor.py
HEBDO AT META - proxy heuristique de risque de stop -9%, explicitement NON calibrée.
"""

import pandas as pd, numpy as np

class MAEPredictor:
    def __init__(self):
        self.weights={'vol_z':2.0,'drawdown_4w':1.5,'below_sma200':1.0,'atr_14_pct':10.0}; self.threshold=3.0; self.model_status='HEURISTIC_UNCALIBRATED'

    @staticmethod
    def _num(v):
        x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0]
        return float(x) if pd.notna(x) and np.isfinite(float(x)) else np.nan

    def predict_stop_risk(self,row:pd.Series)->float:
        score=0.0; vol_z=self._num(row.get('vol_z')); dd=self._num(row.get('drawdown_4w')); close=self._num(row.get('close')); sma200=self._num(row.get('sma200')); atr_pct=self._num(row.get('atr_14_pct'))
        if pd.notna(vol_z):
            if vol_z>4: score+=self.weights['vol_z']
            elif vol_z>3: score+=self.weights['vol_z']*0.5
        if pd.notna(dd):
            if dd<-0.12: score+=self.weights['drawdown_4w']
            elif dd<-0.08: score+=self.weights['drawdown_4w']*0.5
        if pd.notna(close) and pd.notna(sma200) and close<sma200: score+=self.weights['below_sma200']
        if pd.notna(atr_pct): score+=max(0.0,atr_pct)*self.weights['atr_14_pct']
        z=float(np.clip(score-self.threshold,-60,60))
        return float(1/(1+np.exp(-z)))

    def predict_stop_prob(self,row:pd.Series)->float: return self.predict_stop_risk(row)

    def predict_batch(self,df:pd.DataFrame)->pd.DataFrame:
        df=df.copy()
        if df.empty:
            for col in ['risk_stop_9_proxy','prob_stop_9','mae_model_status','EXCLU_MAE','mae_threshold_used']:
                df[col]=pd.Series(dtype='object' if col=='mae_model_status' else 'float64')
            return df
        df['risk_stop_9_proxy']=df.apply(self.predict_stop_risk,axis=1)
        if not np.isfinite(df['risk_stop_9_proxy'].to_numpy(dtype=float)).all(): raise ValueError('BLOCK_DATA_MAE: non-finite risk proxy')
        df['prob_stop_9']=df['risk_stop_9_proxy']; df['mae_model_status']=self.model_status
        thr=float(np.clip(df['risk_stop_9_proxy'].quantile(0.75),0.40,0.60)) if len(df)>=20 else 0.45
        df['EXCLU_MAE']=df['risk_stop_9_proxy']>thr; df['mae_threshold_used']=thr
        return df
