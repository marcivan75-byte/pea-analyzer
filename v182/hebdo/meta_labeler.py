"""
v182/hebdo/meta_labeler.py
V22.5 AUDIT 5/5 - Meta-labeling calibré isotonic, features 7, évite overfit
"""

import pandas as pd, numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold

class MetaLabeler:
    def __init__(self):
        self.calibrated=None
        self.features=['vol_z','drawdown_4w','rsi_14_hebdo','atr_14_pct','mom_26w_sector','prob_stop_9','close_vs_sma200']
    def build_meta_label(self, df_backtest: pd.DataFrame):
        df=df_backtest.copy(); df['meta_label']=0
        mask_win=(df['mfe']>0.08)&(df['mae']>-0.09)
        df.loc[mask_win,'meta_label']=1
        df.loc[df['hit_stop']==True,'meta_label']=0
        return df
    def train(self, df_labeled: pd.DataFrame):
        for f in self.features:
            if f not in df_labeled.columns:
                if f=='close_vs_sma200': df_labeled[f]=(df_labeled['close']<df_labeled['sma200']).astype(int) if 'close' in df_labeled.columns and 'sma200' in df_labeled.columns else 0
                else: df_labeled[f]=0
        X=df_labeled[self.features].fillna(0); y=df_labeled['meta_label']
        base=RandomForestClassifier(n_estimators=100, max_depth=6, min_samples_leaf=20, random_state=42, class_weight='balanced')
        self.calibrated=CalibratedClassifierCV(base, method='isotonic', cv=StratifiedKFold(5, shuffle=True, random_state=42))
        self.calibrated.fit(X,y)
        proba=self.calibrated.predict_proba(X)[:,1]
        from sklearn.metrics import precision_score, recall_score
        pred=(proba>0.55).astype(int)
        return {"precision":float(precision_score(y,pred, zero_division=0)),"recall":float(recall_score(y,pred, zero_division=0)),"mean_proba":float(proba.mean())}
    def predict_proba(self, df: pd.DataFrame):
        df=df.copy()
        for f in self.features:
            if f not in df.columns:
                if f=='close_vs_sma200': df[f]=(df['close']<df['sma200']).astype(int) if 'close' in df.columns and 'sma200' in df.columns else 0
                else: df[f]=0
        X=df[self.features].fillna(0)
        if self.calibrated is None: df['prob_meta']=0.5
        else: df['prob_meta']=self.calibrated.predict_proba(X)[:,1]
        return df
