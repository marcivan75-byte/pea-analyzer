"""
v182/scoring/ic_lasso_selector.py
HEBDO AT META - IC Spearman + Lasso gouverné avec validation temporelle sans fuite de scaling.
Les lignes de X/y doivent être ordonnées chronologiquement avant appel.
"""

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Lasso
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from scipy.stats import spearmanr


def compute_information_coefficient(df_features: pd.DataFrame, forward_ret: pd.Series) -> dict:
    ics = {}
    for col in df_features.columns:
        x = df_features[col]
        y = forward_ret
        mask = x.notna() & y.notna()
        if mask.sum() < 30:
            ics[col] = None
        else:
            ic, _ = spearmanr(x[mask], y[mask])
            ics[col] = None if pd.isna(ic) else float(ic)
    return ics


def lasso_select_features(X: pd.DataFrame, y: pd.Series, cv=5):
    """Sélection Lasso sans mélange futur/passé ni fuite du scaler.

    X/y doivent être triés dans l'ordre temporel croissant. Les labels futurs
    absents sont exclus. Le scaler est ajusté à l'intérieur de chaque fold via
    GridSearchCV(Pipeline(StandardScaler, Lasso)).
    """
    if len(X) != len(y):
        raise ValueError('BLOCK_DATA_LASSO: X/y length mismatch')
    if not X.index.equals(y.index):
        raise ValueError('BLOCK_DATA_LASSO: X/y index mismatch')
    if not X.index.is_monotonic_increasing:
        raise ValueError('BLOCK_DATA_LASSO: temporal order is not monotonic')

    valid_y = y.notna()
    Xf = X.loc[valid_y].copy()
    yf = y.loc[valid_y].astype(float)
    if len(Xf) < max(30, cv + 5):
        raise ValueError('BLOCK_DATA_LASSO: insufficient temporal sample after dropping missing labels')
    if Xf.shape[1] == 0:
        raise ValueError('BLOCK_DATA_LASSO: no features')

    # Features manquantes : médiane calculée à l'intérieur de chaque fold serait idéale,
    # mais cette fonction n'a pas de contrat d'imputation gouverné. Fail-closed.
    if Xf.isna().any().any():
        raise ValueError('BLOCK_DATA_LASSO: missing feature values require governed imputation before training')

    splitter = TimeSeriesSplit(n_splits=cv)
    pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('lasso', Lasso(max_iter=5000, random_state=42)),
    ])
    alpha_grid = np.logspace(-5, 0, 60)
    search = GridSearchCV(
        pipe,
        param_grid={'lasso__alpha': alpha_grid},
        cv=splitter,
        scoring='neg_mean_squared_error',
        refit=True,
        n_jobs=1,
    )
    search.fit(Xf, yf)

    lasso = search.best_estimator_.named_steps['lasso']
    coefs = pd.Series(lasso.coef_, index=X.columns)
    selected = coefs[coefs != 0].sort_values(key=lambda s: s.abs(), ascending=False)
    return {
        'coefs': coefs.to_dict(),
        'selected': selected.to_dict(),
        'alpha': float(search.best_params_['lasso__alpha']),
        'cv_scheme': 'TimeSeriesSplit_Pipeline_GridSearchCV',
        'n_labeled': int(len(Xf)),
        'n_dropped_missing_label': int((~valid_y).sum()),
    }


def build_governed_weights(df_selected: dict) -> dict:
    abs_sum = sum(abs(v) for v in df_selected.values()) or 1
    weights = {}
    for f, coef in df_selected.items():
        weights[f] = {
            'weight': abs(coef) / abs_sum,
            'direction': 'LONG' if coef > 0 else 'SHORT',
            'coef': float(coef),
        }
    return weights
