import lightgbm as lgb
import joblib
import json
import pandas as pd
import numpy as np
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("gap_risk")

def simple_gap_risk(days_to_earnings, eps_revision, short_interest,
                    atr_pct, avg_abs_gap_last_4, beat_rate, vol_ratio):
    """Version rules-based de secours."""
    score = 0.0
    days = 99 if pd.isna(days_to_earnings) else float(days_to_earnings)

    if days <= 1: score += 45
    elif days <= 2: score += 30
    elif days <= 3: score += 18
    elif days <= 5: score += 8

    if not pd.isna(eps_revision):
        if eps_revision <= -5: score += 20
        elif eps_revision <= 0: score += 10
        elif eps_revision >= 10: score -= 8

    if not pd.isna(short_interest):
        if short_interest >= 15: score += 12
        elif short_interest >= 8: score += 5

    if not pd.isna(atr_pct) and atr_pct > 0.045: score += 10
    if not pd.isna(avg_abs_gap_last_4) and avg_abs_gap_last_4 > 0.06: score += 12
    if not pd.isna(beat_rate) and beat_rate < 45: score += 8

    score = max(0.0, min(100.0, score))
    p_adverse = score / 100.0 * 0.65
    expected_gap = -0.02 - (score / 100.0) * 0.09

    if p_adverse >= 0.40 or days <= 1:
        gap_mult = 0.0
    elif p_adverse >= 0.28:
        gap_mult = 0.35
    elif p_adverse >= 0.20:
        gap_mult = 0.60
    else:
        gap_mult = 1.0

    if days <= 2:
        gap_mult *= 0.50
    elif days <= 3:
        gap_mult *= 0.75

    return round(p_adverse, 3), round(expected_gap, 4), round(gap_mult, 3)


class GapRiskModel:
    def __init__(self, model_dir: str = "models/gap_risk", allow_unvalidated: bool = False):
        self.model_dir = Path(model_dir)
        self.clf = None
        self.quantile = None
        self.calibrator = None
        self.features = []
        self.is_ready = False
        self.allow_unvalidated = bool(allow_unvalidated)
        self.model_meta = {}
        self._load_models()

    def _load_models(self):
        try:
            clf_path = self.model_dir / "gap_clf_final.txt"
            quant_path = self.model_dir / "gap_quantile_final.txt"
            cal_path = self.model_dir / "gap_calibrator_final.pkl"
            feat_path = self.model_dir / "gap_features.json"

            if not all(p.exists() for p in [clf_path, quant_path, cal_path, feat_path]):
                logger.warning("Modèles Gap Risk absents → mode rules-based")
                return

            meta_path = self.model_dir / "model_meta.json"
            if meta_path.exists():
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                self.model_meta = meta if isinstance(meta, dict) else {}
            validated = bool(self.model_meta.get("validated_for_production", False))
            source = str(self.model_meta.get("training_source") or "").lower()
            real_source = source in {"real_outcomes", "point_in_time_real", "walk_forward_real"}
            if not self.allow_unvalidated and not (validated and real_source):
                logger.warning("Gap Risk model présent mais non validé sur données réelles → mode rules-based")
                return

            self.clf = lgb.Booster(model_file=str(clf_path))
            self.quantile = lgb.Booster(model_file=str(quant_path))
            self.calibrator = joblib.load(cal_path)
            with open(feat_path) as f:
                self.features = json.load(f)
            self.is_ready = True
            logger.info("Modèles Gap Risk chargés")
        except Exception as e:
            logger.error(f"Échec chargement Gap Risk : {e}")
            self.is_ready = False

    def predict(self, df_features: pd.DataFrame) -> pd.DataFrame:
        if df_features is None or df_features.empty:
            return pd.DataFrame(columns=["p_adverse", "expected_adverse_gap", "gap_model_source"])

        if not self.is_ready:
            return self._rules_based(df_features)

        try:
            X = df_features.reindex(columns=self.features, fill_value=np.nan)
            medians = X.median(numeric_only=True)
            X = X.fillna(medians).fillna(0)

            raw = self.clf.predict(X)
            p_adverse = self.calibrator.predict(raw)
            expected_gap = self.quantile.predict(X)

            p_adverse = np.clip(p_adverse, 0.01, 0.99)
            expected_gap = np.clip(expected_gap, -0.35, 0.15)

            return pd.DataFrame({
                "p_adverse": p_adverse,
                "expected_adverse_gap": expected_gap,
                "gap_model_source": "ml"
            }, index=df_features.index)
        except Exception as e:
            logger.exception(f"Erreur prédiction Gap Risk : {e}")
            return self._rules_based(df_features)

    def _rules_based(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for idx, row in df.iterrows():
            try:
                p, gap, _ = simple_gap_risk(
                    row.get("days_to_earnings"),
                    row.get("eps_revision_3m"),
                    row.get("short_interest"),
                    row.get("atr_pct"),
                    row.get("avg_abs_gap_last_4"),
                    row.get("beat_rate"),
                    row.get("vol_ratio")
                )

                # Fail-closed sur les données de risque réellement critiques.
                # L'ancien fallback assimilait des champs absents à un faible risque.
                missing_days = pd.isna(row.get("days_to_earnings"))
                critical = (
                    "days_to_earnings", "atr_pct", "avg_abs_gap_last_4",
                    "beat_rate", "vol_ratio"
                )
                n_missing = sum(pd.isna(row.get(c)) for c in critical)
                if missing_days:
                    p = max(float(p), 0.38)
                    gap = min(float(gap), -0.09)
                elif n_missing >= 3:
                    p = max(float(p), 0.38)
                    gap = min(float(gap), -0.09)
                elif n_missing >= 2:
                    p = max(float(p), 0.28)
                    gap = min(float(gap), -0.065)
            except Exception:
                p, gap = 0.45, -0.09
            rows.append({
                "p_adverse": p,
                "expected_adverse_gap": gap,
                "gap_model_source": "rules"
            })
        return pd.DataFrame(rows, index=df.index)
