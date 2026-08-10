"""
Adaptive Weights Engine – TCT V24.1.2
Ajuste progressivement les pondérations des critères
en fonction de la performance récente des signaux.

Principe :
1. On enregistre chaque signal avec un résultat réel. Les labels proxy sont désactivés par défaut.
2. On mesure, pour chaque critère, son association avec les succès.
3. On met à jour les poids : w ← (1-α)·w + α·w_target
4. Bornes min/max + renormalisation pour stabilité.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd

from src.utils.logger import setup_logger

logger = setup_logger("adaptive_weights")

# Poids de départ = V24.1.2
DEFAULT_WEIGHTS = {
    "squeeze": 0.18,
    "setup": 0.16,
    "earnings": 0.14,
    "t1_tech": 0.14,
    "bayes": 0.10,
    "cata": 0.08,
    "regime": 0.07,
    "rs": 0.05,
    "news": 0.04,
    "valo": 0.04,
}

# Bornes de sécurité (évite explosion / disparition d'un critère)
WEIGHT_MIN = 0.02
WEIGHT_MAX = 0.30

# Critères suivis
CRITERIA = list(DEFAULT_WEIGHTS.keys())


class AdaptiveWeightsEngine:
    def __init__(
        self,
        weights_path: str = "data/persistence/adaptive_weights.json",
        history_path: str = "data/persistence/signal_outcomes.csv",
        learning_rate: float = 0.08,
        lookback_days: int = 60,
        min_samples: int = 40,
        allow_proxy_learning: bool = False,
        allow_unvalidated_weights: bool = False,
    ):
        self.weights_path = Path(weights_path)
        self.history_path = Path(history_path)
        self.learning_rate = learning_rate
        self.lookback_days = lookback_days
        self.min_samples = min_samples
        self.allow_proxy_learning = bool(allow_proxy_learning)
        self.allow_unvalidated_weights = bool(allow_unvalidated_weights)

        self.weights_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

        self.weights = self._load_weights()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_weights(self) -> Dict[str, float]:
        if self.weights_path.exists():
            try:
                with open(self.weights_path) as f:
                    data = json.load(f)
                meta = data.get("meta", {}) if isinstance(data, dict) else {}
                training_source = str((meta or {}).get("training_source") or "").lower()
                if not self.allow_unvalidated_weights and training_source != "real_outcomes":
                    logger.warning(
                        "Poids adaptatifs persistés sans provenance outcomes réels → poids V24.1.2 restaurés"
                    )
                    return dict(DEFAULT_WEIGHTS)
                w = data.get("weights", DEFAULT_WEIGHTS)
                # Sécurité : clés manquantes
                for k, v in DEFAULT_WEIGHTS.items():
                    w.setdefault(k, v)
                return self._normalize(w)
            except Exception as e:
                logger.warning(f"Chargement poids échoué : {e}")
        return dict(DEFAULT_WEIGHTS)

    def save_weights(self, meta: Optional[Dict] = None) -> None:
        payload = {
            "weights": self.weights,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "learning_rate": self.learning_rate,
            "lookback_days": self.lookback_days,
            "meta": meta or {},
        }
        tmp = self.weights_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self.weights_path)
        logger.info(f"Poids adaptatifs sauvegardés : {self.weights}")

    def _normalize(self, w: Dict[str, float]) -> Dict[str, float]:
        """Project weights onto the simplex while respecting min/max bounds."""
        vals = np.array([float(w.get(k, DEFAULT_WEIGHTS[k])) for k in CRITERIA], dtype=float)
        vals = np.nan_to_num(vals, nan=0.0, posinf=WEIGHT_MAX, neginf=0.0)
        if len(vals) * WEIGHT_MIN > 1 or len(vals) * WEIGHT_MAX < 1:
            raise ValueError("Bornes de poids incompatibles avec une somme à 1")

        # Projection x_i = clip(v_i - lambda, lo, hi), sum(x)=1.
        lo_lambda = float(np.min(vals - WEIGHT_MAX)) - 1.0
        hi_lambda = float(np.max(vals - WEIGHT_MIN)) + 1.0
        for _ in range(100):
            lam = (lo_lambda + hi_lambda) / 2.0
            x = np.clip(vals - lam, WEIGHT_MIN, WEIGHT_MAX)
            if x.sum() > 1.0:
                lo_lambda = lam
            else:
                hi_lambda = lam
        x = np.clip(vals - (lo_lambda + hi_lambda) / 2.0, WEIGHT_MIN, WEIGHT_MAX)
        # Correction flottante résiduelle sur les poids non saturés.
        residual = 1.0 - float(x.sum())
        free = np.where((x > WEIGHT_MIN + 1e-12) & (x < WEIGHT_MAX - 1e-12))[0]
        if free.size:
            x[free] += residual / free.size
        else:
            x[int(np.argmax(WEIGHT_MAX - x))] += residual
        return {k: float(v) for k, v in zip(CRITERIA, x)}

    # ------------------------------------------------------------------
    # Enregistrement des outcomes
    # ------------------------------------------------------------------
    def record_outcomes(self, df: pd.DataFrame, label_col: str = "outcome") -> int:
        """
        Ajoute des lignes d'historique.
        df doit contenir les sous-scores (ou proxies) + une colonne label (0/1 ou PnL).
        """
        if df is None or df.empty or label_col not in df.columns:
            return 0

        wanted = list(dict.fromkeys(CRITERIA + ["isin", "setup", "score_final", "meta_proba", "score_earnings_proximity", "bandwidth", label_col]))
        cols_keep = [c for c in wanted if c in df.columns]
        out = df[cols_keep].copy()
        # Évite colonnes dupliquées
        out = out.loc[:, ~out.columns.duplicated()]
        out["recorded_at"] = datetime.now(timezone.utc).isoformat()

        csv_path = self.history_path if str(self.history_path).endswith(".csv") else self.history_path.with_suffix(".csv")
        if csv_path.exists():
            try:
                old = pd.read_csv(csv_path)
                out = pd.concat([old, out], ignore_index=True)
            except Exception as e:
                logger.warning(f"Lecture historique : {e}")

        # Garde 180 jours max
        try:
            out["recorded_at"] = pd.to_datetime(out["recorded_at"], utc=True)
            cutoff = datetime.now(timezone.utc) - timedelta(days=180)
            out = out[out["recorded_at"] >= cutoff]
        except Exception:
            pass

        csv_path = self.history_path if str(self.history_path).endswith(".csv") else self.history_path.with_suffix(".csv")
        out.to_csv(csv_path, index=False)
        self.history_path = csv_path
        n = len(df)
        logger.info(f"{n} outcomes enregistrés → {csv_path}")
        return n

    # ------------------------------------------------------------------
    # Apprentissage
    # ------------------------------------------------------------------
    def _proxy_label_from_row(self, row: pd.Series) -> float:
        """
        Label proxy quand on n'a pas encore le PnL réel.
        Heuristique alignée edge TCT : setups forts + earnings ultra + meta élevée.
        """
        score = 0.0
        setup = str(row.get("setup") or "")
        if setup == "T2_CONFIRMATION":
            score += 0.45
        elif setup == "T1":
            score += 0.25
        if float(row.get("score_earnings_proximity", 0) or 0) >= 80:
            score += 0.25
        if float(row.get("meta_proba", 0.5) or 0.5) >= 0.70:
            score += 0.20
        if float(row.get("score_final", 50) or 50) >= 70:
            score += 0.10
        return 1.0 if score >= 0.55 else 0.0

    def update_from_history(self, use_proxy_if_needed: bool = False) -> Dict[str, float]:
        """
        Recalcule les poids cibles à partir de l'historique récent
        et applique une mise à jour exponentielle.
        """
        if not self.history_path.exists():
            logger.info("Pas d'historique → poids inchangés")
            return self.weights

        try:
            csv_path = self.history_path if str(self.history_path).endswith(".csv") else self.history_path.with_suffix(".csv")
            if not csv_path.exists():
                logger.info("Pas d'historique CSV")
                return self.weights
            hist = pd.read_csv(csv_path)
        except Exception as e:
            logger.error(f"Lecture historique impossible : {e}")
            return self.weights

        if hist.empty:
            return self.weights

        # Fenêtre temporelle
        if "recorded_at" in hist.columns:
            hist["recorded_at"] = pd.to_datetime(hist["recorded_at"], errors="coerce", utc=True)
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
            hist = hist[hist["recorded_at"] >= cutoff]

        if len(hist) < self.min_samples:
            logger.info(f"Échantillons insuffisants ({len(hist)} < {self.min_samples})")
            return self.weights

        # Label
        training_source = None
        if "outcome" in hist.columns:
            y = pd.to_numeric(hist["outcome"], errors="coerce").fillna(0).clip(0, 1)
            training_source = "real_outcomes"
        elif use_proxy_if_needed and self.allow_proxy_learning:
            y = hist.apply(self._proxy_label_from_row, axis=1)
            training_source = "proxy_experimental"
            logger.warning("MODE EXPERIMENTAL : utilisation de labels proxy pour l'adaptation")
        elif use_proxy_if_needed and not self.allow_proxy_learning:
            logger.warning("Apprentissage proxy demandé mais interdit par configuration")
            return self.weights
        else:
            logger.warning("Pas de colonne outcome")
            return self.weights

        # Pour chaque critère : différence de moyenne (succès vs échec)
        lifts = {}
        for crit in CRITERIA:
            # setup est catégoriel même si la colonne existe : ne jamais le passer
            # directement à pd.to_numeric (qui le transformait entièrement en NaN).
            if crit == "setup" and "setup" in hist.columns:
                x = hist["setup"].map({"T2_CONFIRMATION": 90, "T1": 75}).fillna(30)
            elif crit in hist.columns:
                x = pd.to_numeric(hist[crit], errors="coerce")
            elif crit == "earnings" and "score_earnings_proximity" in hist.columns:
                x = pd.to_numeric(hist["score_earnings_proximity"], errors="coerce")
            elif crit == "bayes" and "meta_proba" in hist.columns:
                x = pd.to_numeric(hist["meta_proba"], errors="coerce") * 100
            else:
                lifts[crit] = 0.0
                continue

            x = x.fillna(x.median() if x.notna().any() else 50)
            pos = x[y >= 0.5]
            neg = x[y < 0.5]
            if len(pos) < 5 or len(neg) < 5:
                lifts[crit] = 0.0
                continue
            # Lift normalisé
            lift = (pos.mean() - neg.mean()) / (x.std() + 1e-6)
            lifts[crit] = float(lift)

        # Transforme les lifts en poids cibles (softmax soft)
        lifts_arr = np.array([lifts.get(c, 0.0) for c in CRITERIA])
        # Softmax température
        temp = 1.5
        exp = np.exp((lifts_arr - lifts_arr.max()) / temp)
        target = exp / exp.sum()
        target_w = {c: float(t) for c, t in zip(CRITERIA, target)}

        # Mise à jour exponentielle
        alpha = self.learning_rate
        new_w = {}
        for c in CRITERIA:
            old = self.weights.get(c, DEFAULT_WEIGHTS[c])
            new_w[c] = (1 - alpha) * old + alpha * target_w[c]

        self.weights = self._normalize(new_w)
        self.save_weights(meta={
            "n_samples": len(hist),
            "lifts": {k: round(v, 4) for k, v in lifts.items()},
            "target": {k: round(v, 4) for k, v in target_w.items()},
            "training_source": training_source,
        })
        logger.info(f"Poids mis à jour (α={alpha}) : { {k: round(v,3) for k,v in self.weights.items()} }")
        return self.weights

    def get_weights(self) -> Dict[str, float]:
        return dict(self.weights)

    def reset_to_default(self) -> None:
        self.weights = dict(DEFAULT_WEIGHTS)
        self.save_weights(meta={"reset": True})
