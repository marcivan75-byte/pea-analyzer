from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from src.utils.logger import setup_logger

logger = setup_logger("meta_labeling")


class MetaLabelingModel:
    def __init__(self, model_dir: str = "models/meta_labeling", fallback_proba: float = 0.50, allow_unvalidated: bool = False):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.fallback_proba = float(np.clip(fallback_proba, 0.01, 0.99))
        self.allow_unvalidated = bool(allow_unvalidated)
        self.model_meta = {}
        self.model = None
        self.calibrator = None
        self.features = None
        self.is_ready = False
        self._load()

    def _load(self) -> None:
        try:
            model_path = self.model_dir / "meta_lgbm.txt"
            cal_path = self.model_dir / "meta_calibrator.pkl"
            feat_path = self.model_dir / "meta_features.json"

            if model_path.exists() and cal_path.exists() and feat_path.exists():
                meta_path = self.model_dir / "model_meta.json"
                if meta_path.exists():
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                    self.model_meta = meta if isinstance(meta, dict) else {}
                validated = bool(self.model_meta.get("validated_for_production", False))
                source = str(self.model_meta.get("training_source") or "").lower()
                real_source = source in {"real_outcomes", "point_in_time_real", "walk_forward_real"}
                if not self.allow_unvalidated and not (validated and real_source):
                    logger.warning(
                        "Meta model présent mais non validé sur données réelles → ignoré (shadow/fallback)"
                    )
                    return
                self.model = lgb.Booster(model_file=str(model_path))
                self.calibrator = joblib.load(cal_path)
                with open(feat_path, encoding="utf-8") as f:
                    self.features = json.load(f)
                if not isinstance(self.features, list) or not self.features:
                    raise ValueError("meta_features.json invalide")
                self.is_ready = True
                logger.info("Meta-labeling model loaded")
            else:
                logger.warning(
                    "Meta-labeling model absent → conservation des probabilités amont si disponibles"
                )
        except Exception as e:
            logger.error(f"Failed to load meta model: {e}")
            self.is_ready = False

    def predict_proba(self, df: pd.DataFrame) -> pd.Series:
        if df is None:
            return pd.Series(dtype=float)
        if df.empty:
            return pd.Series(dtype=float, index=df.index)
        if not self.is_ready:
            return pd.Series(self.fallback_proba, index=df.index, dtype=float)

        try:
            X = df.reindex(columns=self.features, fill_value=0)
            X = X.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
            raw = self.model.predict(X)
            proba = self.calibrator.predict(raw)
            return pd.Series(np.clip(proba, 0.01, 0.99), index=df.index, dtype=float)
        except Exception as e:
            logger.error(f"Meta predict error: {e}")
            return pd.Series(self.fallback_proba, index=df.index, dtype=float)


def apply_meta_labeling(
    df: pd.DataFrame,
    model: Optional[MetaLabelingModel] = None,
    fallback_proba: float = 0.50,
    preserve_upstream: bool = True,
) -> pd.DataFrame:
    """Apply the model without destroying valid upstream probabilities when no model exists."""
    if df is None or df.empty:
        return df

    out = df.copy()
    model = model or MetaLabelingModel(fallback_proba=fallback_proba)

    if model.is_ready:
        out["meta_proba"] = model.predict_proba(out)
        out["meta_model_source"] = "ml"
        return out

    existing = (
        pd.to_numeric(out["meta_proba"], errors="coerce")
        if "meta_proba" in out.columns
        else pd.Series(np.nan, index=out.index, dtype=float)
    )
    existing = existing.where(existing.between(0.0, 1.0))
    if preserve_upstream and existing.notna().any():
        out["meta_proba"] = existing.fillna(float(fallback_proba)).clip(0.01, 0.99)
        out["meta_model_source"] = np.where(existing.notna(), "upstream", "fallback")
    else:
        out["meta_proba"] = float(np.clip(fallback_proba, 0.01, 0.99))
        out["meta_model_source"] = "fallback"
    return out
