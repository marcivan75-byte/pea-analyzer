"""
v182/scoring/ic_lasso_selector.py
V22.5 AUDIT 5/5 - IC Spearman + LassoCV + poids gouvernés, purge, pas de 0.86 manuel
"""

import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LassoCV
from scipy.stats import spearmanr


def compute_information_coefficient(df_features: pd.DataFrame, forward_ret: pd.Series) -> dict:
    ics={}
    for col in df_features.columns:
        x=df_features[col]; y=forward_ret
        mask=x.notna() & y.notna()
        if mask.sum()<30: ics[col]=None
        else:
            ic,_=spearmanr(x[mask], y[mask])
            ics[col]=float(ic)
    return ics


def lasso_select_features(X: pd.DataFrame, y: pd.Series, cv=5):
    Xf=X.fillna(0); scaler=StandardScaler(); Xs=scaler.fit_transform(Xf)
    lasso=LassoCV(cv=cv, random_state=42, max_iter=5000).fit(Xs,y.fillna(0))
    coefs=pd.Series(lasso.coef_, index=X.columns)
    selected=coefs[coefs!=0].sort_values(key=lambda s: s.abs(), ascending=False)
    return {"coefs": coefs.to_dict(), "selected": selected.to_dict(), "alpha": float(lasso.alpha_)}


def build_governed_weights(df_selected: dict) -> dict:
    # df_selected = dict feature->coef
    abs_sum=sum(abs(v) for v in df_selected.values()) or 1
    weights={}
    for f, coef in df_selected.items():
        weights[f]={"weight": abs(coef)/abs_sum, "direction": "LONG" if coef>0 else "SHORT", "coef": float(coef)}
    return weights
