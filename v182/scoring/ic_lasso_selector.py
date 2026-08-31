"""
v182/scoring/ic_lasso_selector.py
HEBDO AT META - IC Spearman + Lasso gouverné avec validation temporelle calendaire purgée.
"""
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Lasso
from sklearn.model_selection import GridSearchCV
from scipy.stats import spearmanr


def _validate_time_index(X: pd.DataFrame, y: pd.Series):
    if len(X) != len(y):
        raise ValueError('BLOCK_DATA_LASSO: X/y length mismatch')
    if not X.index.equals(y.index):
        raise ValueError('BLOCK_DATA_LASSO: X/y index mismatch')
    if not isinstance(X.index, pd.DatetimeIndex):
        raise ValueError('BLOCK_DATA_LASSO: DatetimeIndex required as temporal evidence')
    idx = pd.to_datetime(X.index, errors='coerce', utc=True)
    if pd.isna(idx).any():
        raise ValueError('BLOCK_DATA_LASSO: invalid temporal index')
    if not idx.is_monotonic_increasing:
        raise ValueError('BLOCK_DATA_LASSO: temporal order is not monotonic')
    return idx


def compute_information_coefficient(df_features: pd.DataFrame, forward_ret: pd.Series) -> dict:
    _validate_time_index(df_features, forward_ret)
    ics = {}
    for col in df_features.columns:
        x = pd.to_numeric(df_features[col], errors='coerce')
        y = pd.to_numeric(forward_ret, errors='coerce')
        mask = x.notna() & y.notna() & np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 30:
            ics[col] = None
        else:
            ic, _ = spearmanr(x[mask], y[mask])
            ics[col] = None if pd.isna(ic) else float(ic)
    return ics


def _calendar_purged_splits(index: pd.DatetimeIndex, n_splits: int, embargo_days: int):
    if n_splits < 2:
        raise ValueError('BLOCK_DATA_LASSO: cv must be >= 2')
    unique_dates = pd.DatetimeIndex(index.normalize().unique()).sort_values()
    if len(unique_dates) < n_splits + 2:
        raise ValueError('BLOCK_DATA_LASSO: insufficient distinct dates')
    # Réserve environ 40% des dates pour les folds de validation successifs.
    first_test_pos = max(1, int(len(unique_dates) * 0.60))
    test_dates = unique_dates[first_test_pos:]
    chunks = [c for c in np.array_split(test_dates, n_splits) if len(c)]
    if len(chunks) != n_splits:
        raise ValueError('BLOCK_DATA_LASSO: insufficient dates for requested cv')
    embargo = pd.Timedelta(days=int(embargo_days))
    splits = []
    for chunk in chunks:
        test_start = pd.Timestamp(chunk[0])
        test_end = pd.Timestamp(chunk[-1])
        train_cutoff = test_start - embargo
        train_mask = index < train_cutoff
        test_mask = (index.normalize() >= test_start) & (index.normalize() <= test_end)
        train_idx = np.flatnonzero(train_mask)
        test_idx = np.flatnonzero(test_mask)
        if len(train_idx) < 20 or len(test_idx) < 2:
            raise ValueError('BLOCK_DATA_LASSO: calendar-purged fold too small')
        if index[train_idx].max() + embargo > index[test_idx].min():
            raise RuntimeError('INTERNAL_LASSO_SPLIT_ERROR: embargo violated')
        splits.append((train_idx, test_idx))
    return splits


def lasso_select_features(X: pd.DataFrame, y: pd.Series, cv=5, label_horizon_days=None):
    """Sélection Lasso avec scaling fold-local et embargo calendaire du forward label."""
    if label_horizon_days is None:
        raise ValueError('BLOCK_DATA_LASSO: label_horizon_days required for forward-label CV')
    label_horizon_days = int(label_horizon_days)
    if label_horizon_days < 1:
        raise ValueError('BLOCK_DATA_LASSO: label_horizon_days must be >= 1')
    idx = _validate_time_index(X, y)
    valid_y = pd.to_numeric(y, errors='coerce').notna() & np.isfinite(pd.to_numeric(y, errors='coerce'))
    Xf = X.loc[valid_y].copy()
    yf = pd.to_numeric(y.loc[valid_y], errors='coerce').astype(float)
    idxf = pd.DatetimeIndex(idx[valid_y.to_numpy()])
    if len(Xf) < max(30, cv + 5):
        raise ValueError('BLOCK_DATA_LASSO: insufficient temporal sample after dropping missing labels')
    if Xf.shape[1] == 0:
        raise ValueError('BLOCK_DATA_LASSO: no features')
    for c in Xf.columns:
        Xf[c] = pd.to_numeric(Xf[c], errors='coerce')
    if Xf.isna().any().any() or not np.isfinite(Xf.to_numpy(dtype=float)).all():
        raise ValueError('BLOCK_DATA_LASSO: non-finite features require governed imputation before training')

    splits = _calendar_purged_splits(idxf, int(cv), label_horizon_days)
    pipe = Pipeline([('scaler', StandardScaler()), ('lasso', Lasso(max_iter=5000, random_state=42))])
    search = GridSearchCV(
        pipe,
        {'lasso__alpha': np.logspace(-5, 0, 60)},
        cv=splits,
        scoring='neg_mean_squared_error',
        refit=True,
        n_jobs=1,
        error_score='raise',
    )
    try:
        search.fit(Xf, yf)
    except ValueError as exc:
        raise ValueError(f'BLOCK_DATA_LASSO: calendar temporal CV infeasible: {exc}') from exc
    lasso = search.best_estimator_.named_steps['lasso']
    coefs = pd.Series(lasso.coef_, index=X.columns)
    selected = coefs[coefs != 0].sort_values(key=lambda s: s.abs(), ascending=False)
    return {
        'coefs': coefs.to_dict(),
        'selected': selected.to_dict(),
        'alpha': float(search.best_params_['lasso__alpha']),
        'cv_scheme': 'CalendarPurged_Pipeline_GridSearchCV',
        'embargo_days': label_horizon_days,
        'n_labeled': int(len(Xf)),
        'n_dropped_missing_label': int((~valid_y).sum()),
        'n_distinct_dates': int(pd.DatetimeIndex(idxf.normalize()).nunique()),
    }


def build_governed_weights(df_selected: dict) -> dict:
    abs_sum = sum(abs(v) for v in df_selected.values()) or 1
    return {f: {'weight': abs(coef)/abs_sum, 'direction': 'LONG' if coef > 0 else 'SHORT', 'coef': float(coef)} for f, coef in df_selected.items()}
