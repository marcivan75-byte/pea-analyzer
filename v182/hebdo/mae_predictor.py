"""
v182/hebdo/mae_predictor.py
HEBDO AT META - proxy heuristique de risque de stop -9%, explicitement NON calibrée.
"""

import pandas as pd, numpy as np

class MAEPredictor:
    def __init__(self):
        self.weights={'vol_z':2.0,'drawdown_4w':1.5,'below_sma200':1.0,'atr_14_pct':10.0}
        self.threshold=3.0
        self.model_status='HEURISTIC_UNCALIBRATED'

    def predict_stop_risk(self, row: pd.Series)->float:
        score=0.0
        vol_z=row.get('vol_z',np.nan)
        if pd.notna(vol_z):
            if vol_z>4: score+=self.weights['vol_z']
            elif vol_z>3: score+=self.weights['vol_z']*0.5
        dd=row.get('drawdown_4w',np.nan)
        if pd.notna(dd):
            if dd<-0.12: score+=self.weights['drawdown_4w']
            elif dd<-0.08: score+=self.weights['drawdown_4w']*0.5
        close=row.get('close',np.nan); sma200=row.get('sma200',np.nan)
        if pd.notna(close) and pd.notna(sma200) and close<sma200: score+=self.weights['below_sma200']
        atr_pct=row.get('atr_14_pct',np.nan)
        if pd.notna(atr_pct): score+=atr_pct*self.weights['atr_14_pct']
        # Transformation logistique utile pour borner le proxy entre 0 et 1,
        # mais ce n'est PAS une probabilité calibrée.
        return float(1/(1+np.exp(-(score-self.threshold))))

    # Alias de compatibilité ; le statut empêche son usage comme probabilité calibrée.
    def predict_stop_prob(self, row: pd.Series)->float:
        return self.predict_stop_risk(row)

    def predict_batch(self, df: pd.DataFrame)->pd.DataFrame:
        df=df.copy()
        df['risk_stop_9_proxy']=df.apply(self.predict_stop_risk, axis=1)
        df['prob_stop_9']=df['risk_stop_9_proxy']
        df['mae_model_status']=self.model_status
        if len(df)>=20:
            thr=max(0.40, min(0.60, df['risk_stop_9_proxy'].quantile(0.75)))
        else:
            thr=0.45
        df['EXCLU_MAE']=df['risk_stop_9_proxy']>thr
        df['mae_threshold_used']=thr
        return df
