"""
v182/hebdo/meta_labeler.py
HEBDO AT META - meta-labeling isotonic avec splits temporels groupés par date et embargo calendaire.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import precision_score, recall_score, brier_score_loss


class MetaLabeler:
    def __init__(self, label_horizon_days: int = 182):
        self.base = None
        self.isotonic = None
        self.features = [
            'vol_z', 'drawdown_4w', 'rsi_14_hebdo', 'atr_14_pct',
            'mom_26w_sector', 'prob_stop_9', 'close_vs_sma200'
        ]
        self.training_status = 'UNTRAINED'
        self.label_horizon_days = int(label_horizon_days)
        if self.label_horizon_days < 1:
            raise ValueError('BLOCK_DATA_META_TRAIN: label_horizon_days must be >= 1')

    def build_meta_label(self, df_backtest: pd.DataFrame):
        required = {'mfe', 'mae', 'hit_stop'}
        missing = required - set(df_backtest.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_META_LABEL: missing {sorted(missing)}")
        df = df_backtest.copy()
        valid = df[['mfe','mae','hit_stop']].notna().all(axis=1)
        if 'block_reason' in df.columns:
            valid &= df['block_reason'].isna()
        dropped=int((~valid).sum())
        df=df.loc[valid].copy()
        if df.empty:
            raise ValueError('BLOCK_DATA_META_LABEL: no complete outcomes')
        if not df['hit_stop'].isin([True,False]).all():
            raise ValueError('BLOCK_DATA_META_LABEL: hit_stop must be boolean for complete outcomes')
        df['meta_label'] = 0
        mask_win = (pd.to_numeric(df['mfe'],errors='coerce') > 0.08) & (pd.to_numeric(df['mae'],errors='coerce') > -0.09)
        df.loc[mask_win, 'meta_label'] = 1
        df.loc[df['hit_stop'] == True, 'meta_label'] = 0
        df['meta_label_dropped_incomplete']=dropped
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
    def _with_time(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if 'date' in out.columns:
            out['_meta_time'] = pd.to_datetime(out['date'], errors='coerce', utc=True)
        elif isinstance(out.index, pd.DatetimeIndex):
            out['_meta_time'] = pd.to_datetime(out.index, utc=True)
        else:
            raise ValueError('BLOCK_DATA_META_TRAIN: temporal order evidence missing (date column or DatetimeIndex required)')
        if out['_meta_time'].isna().any():
            raise ValueError('BLOCK_DATA_META_TRAIN: invalid date values')
        return out.sort_values(['_meta_time']).reset_index(drop=True)

    def _purged_split(self, work: pd.DataFrame):
        times=work['_meta_time']; start=times.min(); end=times.max()
        embargo=pd.Timedelta(days=self.label_horizon_days)
        effective=(end-start)-2*embargo
        if effective <= pd.Timedelta(0):
            return None
        train_end=start + effective*0.60
        cal_start=train_end + embargo
        cal_end=cal_start + effective*0.20
        test_start=cal_end + embargo
        train=work[times <= train_end].copy()
        cal=work[(times >= cal_start) & (times <= cal_end)].copy()
        test=work[times >= test_start].copy()
        if min(len(train),len(cal),len(test)) < 10:
            return None
        if train['_meta_time'].max()+embargo > cal['_meta_time'].min():
            raise RuntimeError('INTERNAL_META_SPLIT_ERROR: train/cal embargo violated')
        if cal['_meta_time'].max()+embargo > test['_meta_time'].min():
            raise RuntimeError('INTERNAL_META_SPLIT_ERROR: cal/test embargo violated')
        return train,cal,test,int(times.nunique())

    def train(self, df_labeled: pd.DataFrame):
        if 'meta_label' not in df_labeled.columns:
            raise ValueError('BLOCK_DATA_META_TRAIN: meta_label missing')
        work=self._with_time(df_labeled)
        work=self._prepare_features(work,strict=True)
        if work[self.features].isna().any().any():
            raise ValueError('BLOCK_DATA_META_FEATURES: missing feature values in training sample')
        split=self._purged_split(work)
        if split is None:
            self.base=None; self.isotonic=None; self.training_status='BLOCK_INSUFFICIENT_PURGED_TEMPORAL_SAMPLE'
            return {'status':self.training_status,'n':int(len(work)),'label_horizon_days':self.label_horizon_days}
        train,cal,test,n_dates=split
        y_train=train['meta_label'].astype(int); y_cal=cal['meta_label'].astype(int); y_test=test['meta_label'].astype(int)
        if not (self._class_ok(y_train) and self._class_ok(y_cal) and self._class_ok(y_test)):
            self.base=None; self.isotonic=None; self.training_status='BLOCK_INSUFFICIENT_CLASSES_PER_PURGED_SPLIT'
            return {'status':self.training_status,'n':int(len(work)),
                    'train_counts':{str(k):int(v) for k,v in y_train.value_counts().items()},
                    'cal_counts':{str(k):int(v) for k,v in y_cal.value_counts().items()},
                    'test_counts':{str(k):int(v) for k,v in y_test.value_counts().items()}}
        self.base=RandomForestClassifier(n_estimators=200,max_depth=6,min_samples_leaf=20,random_state=42,class_weight='balanced')
        self.base.fit(train[self.features],y_train)
        raw_cal=self.base.predict_proba(cal[self.features])[:,1]
        if np.unique(raw_cal).size<2:
            self.base=None; self.isotonic=None; self.training_status='BLOCK_DEGENERATE_CALIBRATION_SCORES'
            return {'status':self.training_status,'n':int(len(work))}
        self.isotonic=IsotonicRegression(out_of_bounds='clip'); self.isotonic.fit(raw_cal,y_cal)
        raw_test=self.base.predict_proba(test[self.features])[:,1]
        proba_test=np.asarray(self.isotonic.transform(raw_test),dtype=float); pred=(proba_test>0.55).astype(int)
        self.training_status='TRAINED_PURGED_TEMPORAL_OOS'
        return {'status':self.training_status,'precision':float(precision_score(y_test,pred,zero_division=0)),
                'recall':float(recall_score(y_test,pred,zero_division=0)),
                'brier':float(brier_score_loss(y_test,proba_test)),'mean_proba':float(proba_test.mean()),
                'n':int(len(work)),'n_dates':n_dates,'n_train':int(len(train)),'n_calibration':int(len(cal)),'n_test':int(len(test)),
                'split_scheme':'purged_calendar_time_train_cal_test','embargo_days':self.label_horizon_days}

    def predict_proba(self, df: pd.DataFrame):
        if self.base is None or self.isotonic is None:
            out=df.copy(); out['prob_meta']=0.5; out['meta_model_status']=self.training_status
            return out
        out=self._prepare_features(df,strict=True)
        if out[self.features].isna().any().any():
            raise ValueError('BLOCK_DATA_META_FEATURES: missing feature values in prediction sample')
        raw=self.base.predict_proba(out[self.features])[:,1]
        out['prob_meta']=np.asarray(self.isotonic.transform(raw),dtype=float)
        out['meta_model_status']=self.training_status
        return out
