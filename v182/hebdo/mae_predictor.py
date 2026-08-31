"""
v182/hebdo/mae_predictor.py
V22.5 AUDIT 5/5 - Proba stop -9% calibrée, vol_z + dd + sma200 + atr, seuil adaptatif
"""

import pandas as pd, numpy as np

class MAEPredictor:
    def __init__(self):
        self.weights={'vol_z':2.0,'drawdown_4w':1.5,'below_sma200':1.0,'atr_14_pct':10.0}
        self.threshold=3.0
    def predict_stop_prob(self, row: pd.Series)->float:
        score=0.0
        vol_z=row.get('vol_z',0)
        if pd.notna(vol_z):
            if vol_z>4: score+=self.weights['vol_z']
            elif vol_z>3: score+=self.weights['vol_z']*0.5
        dd=row.get('drawdown_4w',0)
        if pd.notna(dd):
            if dd<-0.12: score+=self.weights['drawdown_4w']
            elif dd<-0.08: score+=self.weights['drawdown_4w']*0.5
        close=row.get('close',np.nan); sma200=row.get('sma200',np.nan)
        if pd.notna(close) and pd.notna(sma200) and close<sma200: score+=self.weights['below_sma200']
        atr_pct=row.get('atr_14_pct',0)
        if pd.notna(atr_pct): score+=atr_pct*self.weights['atr_14_pct']
        prob=1/(1+np.exp(-(score-self.threshold)))
        return float(prob)
    def predict_batch(self, df: pd.DataFrame)->pd.DataFrame:
        df=df.copy(); df['prob_stop_9']=df.apply(self.predict_stop_prob, axis=1)
        # Seuil adaptatif quantile 75% au lieu fixe 0.45 pour éviter perdre winners
        if len(df)>=20:
            thr=max(0.40, min(0.60, df['prob_stop_9'].quantile(0.75)))
        else: thr=0.45
        df['EXCLU_MAE']=df['prob_stop_9']>thr
        df['mae_threshold_used']=thr
        return df
