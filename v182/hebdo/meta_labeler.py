"""
v182/hebdo/meta_labeler.py
HEBDO AT META - calibrated meta-labeling with minimum-sample governance.
"""

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import precision_score, recall_score


class MetaLabeler:
    def __init__(self):
        self.calibrated = None
        self.features = [
            'vol_z', 'drawdown_4w', 'rsi_14_hebdo', 'atr_14_pct',
            'mom_26w_sector', 'prob_stop_9', 'close_vs_sma200'
        ]
        self.training_status = 'UNTRAINED'

    def build_meta_label(self, df_backtest: pd.DataFrame):
        required = {'mfe', 'mae', 'hit_stop'}
        missing = required - set(df_backtest.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_META_LABEL: missing {sorted(missing)}")
        df = df_backtest.copy()
        df['meta_label'] = 0
        mask_win = (df['mfe'] > 0.08) & (df['mae'] > -0.09)
        df.loc[mask_win, 'meta_label'] = 1
        df.loc[df['hit_stop'] == True, 'meta_label'] = 0
        return df

    def _prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for f in self.features:
            if f not in out.columns:
                if f == 'close_vs_sma200':
                    if 'close' in out.columns and 'sma200' in out.columns:
                        out[f] = (out['close'] < out['sma200']).astype(int)
                    else:
                        out[f] = 0
                else:
                    out[f] = 0
        return out

    def train(self, df_labeled: pd.DataFrame):
        if 'meta_label' not in df_labeled.columns:
            raise ValueError('BLOCK_DATA_META_TRAIN: meta_label missing')
        work = self._prepare_features(df_labeled)
        y = work['meta_label'].astype(int)
        counts = y.value_counts()
        if len(counts) < 2 or counts.min() < 5 or len(work) < 30:
            self.calibrated = None
            self.training_status = 'BLOCK_INSUFFICIENT_CLASSES_OR_SAMPLE'
            return {
                'status': self.training_status,
                'precision': None,
                'recall': None,
                'mean_proba': None,
                'n': int(len(work)),
                'class_counts': {str(k): int(v) for k, v in counts.items()},
            }
        X = work[self.features].fillna(0)
        base = RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            min_samples_leaf=20,
            random_state=42,
            class_weight='balanced',
        )
        cv = StratifiedKFold(5, shuffle=True, random_state=42)
        self.calibrated = CalibratedClassifierCV(base, method='isotonic', cv=cv)
        self.calibrated.fit(X, y)
        proba = self.calibrated.predict_proba(X)[:, 1]
        pred = (proba > 0.55).astype(int)
        self.training_status = 'TRAINED'
        return {
            'status': self.training_status,
            'precision': float(precision_score(y, pred, zero_division=0)),
            'recall': float(recall_score(y, pred, zero_division=0)),
            'mean_proba': float(proba.mean()),
            'n': int(len(work)),
        }

    def predict_proba(self, df: pd.DataFrame):
        out = self._prepare_features(df)
        X = out[self.features].fillna(0)
        if self.calibrated is None:
            out['prob_meta'] = 0.5
            out['meta_model_status'] = self.training_status
        else:
            out['prob_meta'] = self.calibrated.predict_proba(X)[:, 1]
            out['meta_model_status'] = 'TRAINED'
        return out
