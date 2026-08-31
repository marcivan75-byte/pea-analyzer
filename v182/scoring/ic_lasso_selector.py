"""
v182/scoring/ic_lasso_selector.py
HEBDO AT META - IC Spearman + LassoCV gouverné avec validation temporelle.
Les lignes de X/y doivent être ordonnées chronologiquement avant appel.
"""

import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LassoCV
from sklearn.model_selection import TimeSeriesSplit
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
    """Sélection Lasso sans mélange futur/passé.

    X/y doivent être triés dans l'ordre temporel croissant. Les labels futurs
    absents sont exclus : ils ne sont jamais imputés à zéro.
    """
    if len(X) != len(y):
        raise ValueError('BLOCK_DATA_LASSO: X/y length mismatch')
    valid_y = y.notna()
    Xf = X.loc[valid_y].copy()
    yf = y.loc[valid_y].astype(float)
    if len(Xf) < max(30, cv + 5):
        raise ValueError('BLOCK_DATA_LASSO: insufficient temporal sample after dropping missing labels')
    Xf = Xf.fillna(0)
    splitter = TimeSeriesSplit(n_splits=cv)
    model = Pipeline([
        ('scaler', StandardScaler()),
        ('lasso', LassoCV(cv=splitter, random_state=42, max_iter=5000)),
    ])
    model.fit(Xf, yf)
    lasso = model.named_steps['lasso']
    coefs = pd.Series(lasso.coef_, index=X.columns)
    selected = coefs[coefs != 0].sort_values(key=lambda s: s.abs(), ascending=False)
    return {
        'coefs': coefs.to_dict(),
        'selected': selected.to_dict(),
        'alpha': float(lasso.alpha_),
        'cv_scheme': 'TimeSeriesSplit',
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
