"""
Script d'entraînement des modèles Gap Risk et Meta-Labeling.
Usage DEMO uniquement :
    python -m src.ml.train_models --gap --allow-synthetic-demo-training
    python -m src.ml.train_models --meta --allow-synthetic-demo-training
    python -m src.ml.train_models --all --allow-synthetic-demo-training

Ces routines utilisent des labels synthétiques et ne doivent jamais produire
un modèle de décision considéré comme validé en production.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from sklearn.isotonic import IsotonicRegression
import joblib

from src.utils.logger import setup_logger
from src.data.demo import generate_demo_signals

logger = setup_logger("train_models")


def _write_training_meta(model_dir: str, model_type: str, metrics: dict) -> None:
    payload = {
        "model_type": model_type,
        "training_source": "synthetic_demo",
        "validated_for_production": False,
        "research_only": True,
        "metrics": metrics,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "warning": "Synthetic labels: model must not drive production sizing or probabilities.",
    }
    Path(model_dir, "model_meta.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _make_synthetic_gap_dataset(n: int = 2000) -> pd.DataFrame:
    """Dataset synthétique réaliste pour entraîner le Gap Risk (prototype)."""
    np.random.seed(42)
    days = np.random.choice([1, 2, 3, 4, 5, 8, 12, 20, 40], n, p=[0.08,0.10,0.12,0.12,0.12,0.15,0.12,0.10,0.09])
    eps_rev = np.random.uniform(-12, 18, n)
    short = np.random.uniform(0.5, 25, n)
    atr_pct = np.random.uniform(0.012, 0.08, n)
    avg_gap = np.random.uniform(0.02, 0.12, n)
    beat = np.random.uniform(25, 90, n)
    vol_ratio = np.random.uniform(0.7, 4.5, n)

    # Label adverse gap (règle simplifiée + bruit)
    score = np.zeros(n)
    score += np.where(days <= 1, 45, np.where(days <= 2, 30, np.where(days <= 3, 18, np.where(days <= 5, 8, 0))))
    score += np.where(eps_rev <= -5, 20, np.where(eps_rev <= 0, 10, 0))
    score += np.where(short >= 15, 12, np.where(short >= 8, 5, 0))
    score += np.where(atr_pct > 0.045, 10, 0)
    score += np.where(avg_gap > 0.06, 12, 0)
    score += np.where(beat < 45, 8, 0)
    score = np.clip(score, 0, 100)
    p = score / 100 * 0.65
    adverse = (np.random.rand(n) < p).astype(int)
    gap_pct = -0.02 - (score / 100) * 0.09 + np.random.normal(0, 0.02, n)

    return pd.DataFrame({
        "days_to_earnings": days,
        "eps_revision_3m": eps_rev,
        "short_interest": short,
        "atr_pct": atr_pct,
        "avg_abs_gap_last_4": avg_gap,
        "beat_rate": beat,
        "vol_ratio": vol_ratio,
        "score_earnings_proximity": np.clip(60 + eps_rev * 1.5 - days * 1.2, 20, 98),
        "adverse_gap": adverse,
        "gap_pct": gap_pct,
    })


def train_gap_risk(model_dir: str = "models/gap_risk"):
    logger.info("Entraînement Gap Risk…")
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    df = _make_synthetic_gap_dataset(2500)

    feature_cols = [
        "days_to_earnings", "eps_revision_3m", "short_interest", "atr_pct",
        "avg_abs_gap_last_4", "beat_rate", "vol_ratio", "score_earnings_proximity"
    ]
    X = df[feature_cols].fillna(0)
    y_clf = df["adverse_gap"]
    y_reg = df["gap_pct"]

    X_train, X_val, y_clf_train, y_clf_val, y_reg_train, y_reg_val = train_test_split(
        X, y_clf, y_reg, test_size=0.25, random_state=42, stratify=y_clf
    )

    clf = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.04, max_depth=5, num_leaves=22,
        subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1, verbose=-1
    )
    clf.fit(X_train, y_clf_train)

    raw = clf.predict_proba(X_val)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(raw, y_clf_val)
    auc = roc_auc_score(y_clf_val, calibrator.predict(raw))
    logger.info(f"Gap Risk Classifier AUC: {auc:.4f}")

    quant = lgb.LGBMRegressor(
        objective="quantile", alpha=0.15, n_estimators=300, learning_rate=0.04,
        max_depth=5, num_leaves=22, subsample=0.8, colsample_bytree=0.8,
        random_state=42, n_jobs=-1, verbose=-1
    )
    quant.fit(X_train, y_reg_train)

    clf.booster_.save_model(f"{model_dir}/gap_clf_final.txt")
    quant.booster_.save_model(f"{model_dir}/gap_quantile_final.txt")
    joblib.dump(calibrator, f"{model_dir}/gap_calibrator_final.pkl")
    with open(f"{model_dir}/gap_features.json", "w") as f:
        json.dump(feature_cols, f)
    _write_training_meta(model_dir, "gap_risk", {"auc": float(auc), "n": int(len(df))})
    logger.info("Modèles Gap Risk sauvegardés (DEMO non validé)")


def train_meta_labeling(model_dir: str = "models/meta_labeling"):
    logger.info("Entraînement Meta-Labeling…")
    Path(model_dir).mkdir(parents=True, exist_ok=True)

    # Utilise le générateur de démo enrichi
    df = generate_demo_signals(n=800, path="data/processed/_train_meta.parquet")
    # Label synthétique basé sur qualité du setup
    y = (
        (df["setup"].isin(["T1", "T2_CONFIRMATION"])).astype(int) * 0.4
        + (df["score_earnings_proximity"] > 75).astype(int) * 0.3
        + (df["meta_proba"] > 0.65).astype(int) * 0.3
        + np.random.rand(len(df)) * 0.2
    )
    y = (y > 0.55).astype(int)

    feature_cols = [
        "score_final", "score_earnings_proximity", "days_to_earnings",
        "eps_revision_3m", "short_interest", "vol_ratio", "atr_pct",
        "meta_proba", "market_cap_m", "bandwidth"
    ]
    for c in feature_cols:
        if c not in df.columns:
            df[c] = 0.0

    X = df[feature_cols].fillna(0)
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)

    model = lgb.LGBMClassifier(
        n_estimators=250, learning_rate=0.05, max_depth=4, num_leaves=18,
        subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1, verbose=-1
    )
    model.fit(X_train, y_train)

    raw = model.predict_proba(X_val)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(raw, y_val)
    auc = roc_auc_score(y_val, calibrator.predict(raw))
    logger.info(f"Meta-Labeling AUC: {auc:.4f}")

    model.booster_.save_model(f"{model_dir}/meta_lgbm.txt")
    joblib.dump(calibrator, f"{model_dir}/meta_calibrator.pkl")
    with open(f"{model_dir}/meta_features.json", "w") as f:
        json.dump(feature_cols, f)
    _write_training_meta(model_dir, "meta_labeling", {"auc": float(auc), "n": int(len(df))})
    logger.info("Modèle Meta-Labeling sauvegardé (DEMO non validé)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap", action="store_true")
    parser.add_argument("--meta", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--allow-synthetic-demo-training",
        action="store_true",
        help="Autorise explicitement l'entraînement sur labels synthétiques (DEMO uniquement).",
    )
    args = parser.parse_args()

    requested = any([args.gap, args.meta, args.all])
    if requested and not args.allow_synthetic_demo_training:
        parser.error(
            "Entraînement refusé : ce script utilise des labels synthétiques. "
            "Ajoutez --allow-synthetic-demo-training uniquement pour un test DEMO."
        )

    if args.all or args.gap:
        train_gap_risk()
    if args.all or args.meta:
        train_meta_labeling()
    if not requested:
        print("Usage DEMO: python -m src.ml.train_models --all --allow-synthetic-demo-training")
