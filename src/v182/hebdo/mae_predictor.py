from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler


REQUIRED_FEATURES = ("vol_z", "drawdown_4w", "close_vs_sma200", "atr_14_pct")
RAW_REQUIRED_FEATURES = ("vol_z", "drawdown_4w", "close", "sma200", "atr_14_pct")
DEFAULT_THRESHOLD = 0.45
MIN_TRAIN_ROWS = 150


class MAEDataUnavailable(ValueError):
    pass


def _as_finite_float(row: Mapping[str, object] | pd.Series, key: str) -> float:
    value = row.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MAEDataUnavailable(f"BLOCK_DATA_MAE: missing/invalid {key}") from exc
    if not np.isfinite(number):
        raise MAEDataUnavailable(f"BLOCK_DATA_MAE: non-finite {key}")
    return number


def _feature_vector(row: Mapping[str, object] | pd.Series) -> dict[str, float]:
    raw = {key: _as_finite_float(row, key) for key in RAW_REQUIRED_FEATURES}
    if raw["atr_14_pct"] < 0:
        raise MAEDataUnavailable("BLOCK_DATA_MAE: negative atr_14_pct")
    if raw["sma200"] <= 0:
        raise MAEDataUnavailable("BLOCK_DATA_MAE: non-positive sma200")
    return {
        "vol_z": raw["vol_z"],
        "drawdown_4w": raw["drawdown_4w"],
        "close_vs_sma200": raw["close"] / raw["sma200"] - 1.0,
        "atr_14_pct": raw["atr_14_pct"],
    }


def predict_stop_prob(row: Mapping[str, object] | pd.Series) -> float:
    """Heuristique V22 de secours analytique, non promue comme modèle entraîné."""
    raw = {key: _as_finite_float(row, key) for key in RAW_REQUIRED_FEATURES}
    if raw["atr_14_pct"] < 0:
        raise MAEDataUnavailable("BLOCK_DATA_MAE: negative atr_14_pct")
    score = 0.0
    score += 2.0 if raw["vol_z"] > 4.0 else 0.0
    score += 1.5 if raw["drawdown_4w"] < -0.12 else 0.0
    score += 1.0 if raw["close"] < raw["sma200"] else 0.0
    score += raw["atr_14_pct"] * 10.0
    return 1.0 / (1.0 + math.exp(-(score - 3.0)))


def train_stop_model(
    history: pd.DataFrame,
    *,
    label_col: str = "hit_stop",
    date_col: str = "as_of_date",
) -> dict[str, object]:
    """Entraîne un modèle logistique sur observations PIT ordonnées dans le temps.

    Les features sont fixées avant l'entraînement. Aucun fill n'est appliqué. Les 20%
    d'observations les plus récentes servent de validation temporelle et ne participent
    pas au fit, afin d'éviter une validation in-sample trompeuse.
    """
    required = set(RAW_REQUIRED_FEATURES) | {label_col, date_col}
    missing = required.difference(history.columns)
    if missing:
        raise MAEDataUnavailable(f"BLOCK_DATA_MAE_TRAIN: missing {sorted(missing)}")

    work = history.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    vectors: list[dict[str, object]] = []
    for idx, row in work.iterrows():
        try:
            features = _feature_vector(row)
        except MAEDataUnavailable:
            continue
        label = row.get(label_col)
        if pd.isna(label) or pd.isna(row.get(date_col)):
            continue
        vectors.append({**features, "label": int(bool(label)), "date": row[date_col], "_idx": idx})

    clean = pd.DataFrame(vectors).sort_values("date", kind="stable") if vectors else pd.DataFrame()
    if len(clean) < MIN_TRAIN_ROWS:
        raise MAEDataUnavailable(f"BLOCK_DATA_MAE_TRAIN: only {len(clean)} complete rows")
    if clean["label"].nunique() < 2:
        raise MAEDataUnavailable("BLOCK_DATA_MAE_TRAIN: label has only one class")

    split = int(len(clean) * 0.80)
    train = clean.iloc[:split]
    valid = clean.iloc[split:]
    if len(valid) < 30 or train["label"].nunique() < 2:
        raise MAEDataUnavailable("BLOCK_DATA_MAE_TRAIN: temporal validation split insufficient")

    X_train = train[list(REQUIRED_FEATURES)].astype(float)
    y_train = train["label"].astype(int)
    X_valid = valid[list(REQUIRED_FEATURES)].astype(float)
    y_valid = valid["label"].astype(int)

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42)
    model.fit(scaler.transform(X_train), y_train)
    prob = model.predict_proba(scaler.transform(X_valid))[:, 1]

    auc = float(roc_auc_score(y_valid, prob)) if y_valid.nunique() == 2 else None
    brier = float(brier_score_loss(y_valid, prob))
    return {
        "version": "V22.1_LOGIT_1",
        "features": list(REQUIRED_FEATURES),
        "training_mean": {f: float(v) for f, v in zip(REQUIRED_FEATURES, scaler.mean_, strict=True)},
        "training_scale": {f: float(v) for f, v in zip(REQUIRED_FEATURES, scaler.scale_, strict=True)},
        "coef": {f: float(v) for f, v in zip(REQUIRED_FEATURES, model.coef_[0], strict=True)},
        "intercept": float(model.intercept_[0]),
        "threshold": DEFAULT_THRESHOLD,
        "n_train": int(len(train)),
        "n_validation": int(len(valid)),
        "validation_auc": auc,
        "validation_brier": brier,
        "train_end": str(train["date"].max()),
        "validation_start": str(valid["date"].min()),
        "validation_end": str(valid["date"].max()),
    }


def predict_stop_prob_trained(
    row: Mapping[str, object] | pd.Series,
    artifact: Mapping[str, object],
) -> float:
    features = _feature_vector(row)
    names = list(artifact.get("features", []))
    if names != list(REQUIRED_FEATURES):
        raise MAEDataUnavailable("BLOCK_DATA_MAE_MODEL: incompatible feature contract")
    means = artifact.get("training_mean")
    scales = artifact.get("training_scale")
    coefs = artifact.get("coef")
    try:
        linear = float(artifact["intercept"])
        for name in names:
            mean_ = float(means[name])
            scale_ = float(scales[name])
            coef_ = float(coefs[name])
            if not np.isfinite(mean_) or not np.isfinite(scale_) or scale_ <= 0 or not np.isfinite(coef_):
                raise ValueError
            linear += coef_ * ((features[name] - mean_) / scale_)
    except (KeyError, TypeError, ValueError) as exc:
        raise MAEDataUnavailable("BLOCK_DATA_MAE_MODEL: invalid trained artifact") from exc
    return 1.0 / (1.0 + math.exp(-linear))


def apply_mae_filter(
    frame: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    trained_artifact: Mapping[str, object] | None = None,
    require_trained: bool = False,
) -> pd.DataFrame:
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be in (0, 1)")
    if require_trained and trained_artifact is None:
        raise MAEDataUnavailable("BLOCK_DATA_MAE_MODEL: trained artifact required")

    out = frame.copy()
    probabilities: list[float] = []
    statuses: list[str] = []
    for _, row in out.iterrows():
        try:
            prob = (
                predict_stop_prob_trained(row, trained_artifact)
                if trained_artifact is not None
                else predict_stop_prob(row)
            )
        except MAEDataUnavailable:
            probabilities.append(np.nan)
            statuses.append("BLOCK_DATA_MAE")
            continue
        probabilities.append(prob)
        statuses.append("EXCLU_MAE" if prob > threshold else "OK")
    out["stop_prob"] = probabilities
    out["mae_status"] = statuses
    out["EXCLU_MAE"] = out["mae_status"].eq("EXCLU_MAE")
    out["mae_model_type"] = "TRAINED" if trained_artifact is not None else "HEURISTIC"
    return out


class MAEPredictor:
    def __init__(self, threshold: float = DEFAULT_THRESHOLD, artifact: Mapping[str, object] | None = None):
        if not 0.0 < threshold < 1.0:
            raise ValueError("threshold must be in (0, 1)")
        self.threshold = threshold
        self.artifact = artifact

    def predict_stop_prob(self, row: Mapping[str, object] | pd.Series) -> float:
        return predict_stop_prob_trained(row, self.artifact) if self.artifact is not None else predict_stop_prob(row)

    def predict_batch(self, frame: pd.DataFrame, *, require_trained: bool = False) -> pd.DataFrame:
        return apply_mae_filter(frame, self.threshold, trained_artifact=self.artifact, require_trained=require_trained)

    def audit_stop_reduction(self, backtest: pd.DataFrame) -> dict[str, float | int | None]:
        required = {"hit_stop", "EXCLU_MAE"}
        missing = required.difference(backtest.columns)
        if missing:
            raise MAEDataUnavailable(f"BLOCK_DATA_MAE_AUDIT: missing {sorted(missing)}")
        valid = backtest.dropna(subset=["hit_stop", "EXCLU_MAE"]).copy()
        total = len(valid)
        if total == 0:
            return {"total_trades": 0, "stops_before": 0, "stops_rate_before": None, "trades_after_filter": 0, "stops_after": 0, "stops_rate_after": None, "stops_avoided": 0, "pct_stops_avoided": None}
        hit = valid["hit_stop"].astype(bool)
        kept = ~valid["EXCLU_MAE"].astype(bool)
        stops_before = int(hit.sum())
        after = valid.loc[kept]
        stops_after = int(after["hit_stop"].astype(bool).sum()) if not after.empty else 0
        return {
            "total_trades": total,
            "stops_before": stops_before,
            "stops_rate_before": float(stops_before / total),
            "trades_after_filter": int(len(after)),
            "stops_after": stops_after,
            "stops_rate_after": float(stops_after / len(after)) if len(after) else None,
            "stops_avoided": int(stops_before - stops_after),
            "pct_stops_avoided": float((stops_before - stops_after) / stops_before) if stops_before else None,
        }
