"""
v182/scoring/ic_lasso_selector.py
HEBDO AT META - IC Spearman + Lasso gouverné avec validation temporelle, scaling fold-local et embargo explicite.
"""

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Lasso
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from scipy.stats import spearmanr


def compute_information_coefficient(df_features: pd.DataFrame, forward_ret: pd.Series) -> dict:
    ics={}
    for col in df_features.columns:
        x=df_features[col]; y=forward_ret
        mask=x.notna() & y.notna()
        if mask.sum()<30:
            ics[col]=None
        else:
            ic,_=spearmanr(x[mask],y[mask])
            ics[col]=None if pd.isna(ic) else float(ic)
    return ics


def lasso_select_features(X: pd.DataFrame, y: pd.Series, cv=5, purge_gap=None):
    """Sélection Lasso avec embargo explicite exprimé en observations.

    `purge_gap` doit correspondre à l'horizon du forward label dans la fréquence de X/y
    (ex. 126 pour un label 26 semaines construit sur séances quotidiennes). Aucun défaut
    implicite n'est accepté car il créerait une fausse garantie anti-look-ahead.
    """
    if purge_gap is None:
        raise ValueError('BLOCK_DATA_LASSO: purge_gap required for forward-label CV')
    purge_gap=int(purge_gap)
    if purge_gap<1:
        raise ValueError('BLOCK_DATA_LASSO: purge_gap must be >= 1')
    if len(X)!=len(y):
        raise ValueError('BLOCK_DATA_LASSO: X/y length mismatch')
    if not X.index.equals(y.index):
        raise ValueError('BLOCK_DATA_LASSO: X/y index mismatch')
    if not X.index.is_monotonic_increasing:
        raise ValueError('BLOCK_DATA_LASSO: temporal order is not monotonic')

    valid_y=y.notna()
    Xf=X.loc[valid_y].copy(); yf=y.loc[valid_y].astype(float)
    if len(Xf)<max(30,cv+5):
        raise ValueError('BLOCK_DATA_LASSO: insufficient temporal sample after dropping missing labels')
    if Xf.shape[1]==0:
        raise ValueError('BLOCK_DATA_LASSO: no features')
    if Xf.isna().any().any():
        raise ValueError('BLOCK_DATA_LASSO: missing feature values require governed imputation before training')
    # TimeSeriesSplit exige assez de données pour folds + gap; pré-validation lisible.
    if len(Xf) <= (cv+1) + purge_gap:
        raise ValueError('BLOCK_DATA_LASSO: insufficient sample for requested purge_gap')

    splitter=TimeSeriesSplit(n_splits=cv,gap=purge_gap)
    pipe=Pipeline([('scaler',StandardScaler()),('lasso',Lasso(max_iter=5000,random_state=42))])
    alpha_grid=np.logspace(-5,0,60)
    search=GridSearchCV(pipe,{'lasso__alpha':alpha_grid},cv=splitter,scoring='neg_mean_squared_error',refit=True,n_jobs=1,error_score='raise')
    try:
        search.fit(Xf,yf)
    except ValueError as exc:
        raise ValueError(f'BLOCK_DATA_LASSO: temporal CV infeasible with purge_gap={purge_gap}: {exc}') from exc

    lasso=search.best_estimator_.named_steps['lasso']
    coefs=pd.Series(lasso.coef_,index=X.columns)
    selected=coefs[coefs!=0].sort_values(key=lambda s:s.abs(),ascending=False)
    return {
        'coefs':coefs.to_dict(),'selected':selected.to_dict(),
        'alpha':float(search.best_params_['lasso__alpha']),
        'cv_scheme':'TimeSeriesSplit_Pipeline_GridSearchCV_Purged',
        'purge_gap':purge_gap,
        'n_labeled':int(len(Xf)),
        'n_dropped_missing_label':int((~valid_y).sum()),
    }


def build_governed_weights(df_selected: dict) -> dict:
    abs_sum=sum(abs(v) for v in df_selected.values()) or 1
    return {f:{'weight':abs(coef)/abs_sum,'direction':'LONG' if coef>0 else 'SHORT','coef':float(coef)} for f,coef in df_selected.items()}
