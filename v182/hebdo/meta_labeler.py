"""
v182/hebdo/meta_labeler.py
HEBDO AT META - meta-labeling isotonic avec séparation chronologique train/calibration/test.
L'entraînement exige une preuve explicite d'ordre temporel.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import precision_score, recall_score, brier_score_loss


class MetaLabeler:
    def __init__(self):
        self.base = None
        self.isotonic = None
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

    def _prepare_features(self, df: pd.DataFrame, strict: bool = True) -> pd.DataFrame:
        out = df.copy()
        if 'close_vs_sma200' not in out.columns and {'close','sma200'}.issubset(out.columns):
            out['close_vs_sma200'] = (out['close'] < out['sma200']).astype(int)
        missing = [f for f in self.features if f not in out.columns]
        if missing and strict:
            raise ValueError(f"BLOCK_DATA_META_FEATURES: missing {missing}")
        for f in missing:
            out[f] = 0
        return out

    @staticmethod
    def _class_ok(y: pd.Series, minimum_each: int = 5) -> bool:
        counts = y.astype(int).value_counts()
        return len(counts) == 2 and counts.min() >= minimum_each

    @staticmethod
    def _sort_temporal(df: pd.DataFrame) -> pd.DataFrame:
        if 'date' in df.columns:
            out = df.copy()
            out['_meta_time'] = pd.to_datetime(out['date'], errors='coerce', utc=True)
            if out['_meta_time'].isna().any():
                raise ValueError('BLOCK_DATA_META_TRAIN: invalid date values')
            return out.sort_values('_meta_time').drop(columns=['_meta_time']).reset_index(drop=True)
        if isinstance(df.index, pd.DatetimeIndex):
            return df.sort_index().reset_index(drop=True)
        raise ValueError('BLOCK_DATA_META_TRAIN: temporal order evidence missing (date column or DatetimeIndex required)')

    def train(self, df_labeled: pd.DataFrame):
        """Entraîne sans mélange temporel: 60% train, 20% calibration, 20% test final."""
        if 'meta_label' not in df_labeled.columns:
            raise ValueError('BLOCK_DATA_META_TRAIN: meta_label missing')
        work = self._sort_temporal(df_labeled)
        work = self._prepare_features(work, strict=True).reset_index(drop=True)
        n = len(work)
        if n < 60:
            self.base = None
            self.isotonic = None
            self.training_status = 'BLOCK_INSUFFICIENT_TEMPORAL_SAMPLE'
            return {'status': self.training_status, 'n': int(n)}

        i_train = int(n * 0.60)
        i_cal = int(n * 0.80)
        train = work.iloc[:i_train]
        cal = work.iloc[i_train:i_cal]
        test = work.iloc[i_cal:]

        y_train = train['meta_label'].astype(int)
        y_cal = cal['meta_label'].astype(int)
        y_test = test['meta_label'].astype(int)
        if not (self._class_ok(y_train) and self._class_ok(y_cal) and self._class_ok(y_test)):
            self.base = None
            self.isotonic = None
            self.training_status = 'BLOCK_INSUFFICIENT_CLASSES_PER_TEMPORAL_SPLIT'
            return {
                'status': self.training_status,
                'n': int(n),
                'train_counts': {str(k): int(v) for k, v in y_train.value_counts().items()},
                'cal_counts': {str(k): int(v) for k, v in y_cal.value_counts().items()},
                'test_counts': {str(k): int(v) for k, v in y_test.value_counts().items()},
            }

        self.base = RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=20,
            random_state=42,
            class_weight='balanced',
        )
        self.base.fit(train[self.features].fillna(0), y_train)

        raw_cal = self.base.predict_proba(cal[self.features].fillna(0))[:, 1]
        self.isotonic = IsotonicRegression(out_of_bounds='clip')
        self.isotonic.fit(raw_cal, y_cal)

        raw_test = self.base.predict_proba(test[self.features].fillna(0))[:, 1]
        proba_test = np.asarray(self.isotonic.transform(raw_test), dtype=float)
        pred = (proba_test > 0.55).astype(int)
        self.training_status = 'TRAINED_TEMPORAL_OOS'
        return {
            'status': self.training_status,
            'precision': float(precision_score(y_test, pred, zero_division=0)),
            'recall': float(recall_score(y_test, pred, zero_division=0)),
            'brier': float(brier_score_loss(y_test, proba_test)),
            'mean_proba': float(proba_test.mean()),
            'n': int(n),
            'n_train': int(len(train)),
            'n_calibration': int(len(cal)),
            'n_test': int(len(test)),
            'split_scheme': 'chronological_60_20_20',
        }

    def predict_proba(self, df: pd.DataFrame):
        # Un modèle non entraîné reste neutre, sans imposer les features ML optionnelles.
        if self.base is None or self.isotonic is None:
            out = df.copy()
            out['prob_meta'] = 0.5
            out['meta_model_status'] = self.training_status
            return out
        out = self._prepare_features(df, strict=True)
        X = out[self.features].fillna(0)
        raw = self.base.predict_proba(X)[:, 1]
        out['prob_meta'] = np.asarray(self.isotonic.transform(raw), dtype=float)
        out['meta_model_status'] = self.training_status
        return out
