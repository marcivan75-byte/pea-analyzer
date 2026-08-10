"""
Reinforcement Learning – TCT V24.1.2
Approche pragmatique sans dépendances lourdes (numpy only) :

- États : vecteur de features du signal (meta, earnings, gap, setup, vol…)
- Actions discrètes :
    0 = IGNORE
    1 = TAKE_REDUCED  (×0.5)
    2 = TAKE_FULL     (×1.0)
    3 = TAKE_OVERSIZE (×1.25)
- Reward : PnL proxy ou label outcome (+1 / -1 / 0)
- Algo : Linear Q-Learning (Q(s,a) = w_a · φ(s)) + ε-greedy
- Buffer d'expérience persisté + mises à jour incrémentales

Intégration :
- predict_action(state) → (action_id, multiplier, q_values)
- record(state, action, reward)
- update() périodique
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

from src.utils.logger import setup_logger

logger = setup_logger("reinforcement")

ACTIONS = {
    0: {"name": "IGNORE",       "mult": 0.0},
    1: {"name": "TAKE_REDUCED", "mult": 0.50},
    2: {"name": "TAKE_FULL",    "mult": 1.00},
    3: {"name": "TAKE_OVERSIZE","mult": 1.25},
}
N_ACTIONS = len(ACTIONS)

# Features d'état (ordre fixe)
STATE_FEATURES = [
    "meta_proba",
    "score_earnings_proximity",
    "p_adverse",
    "days_to_earnings",
    "vol_ratio",
    "score_final",
    "note_opportunite",
    "setup_t1",
    "setup_t2",
    "short_interest",
]


class LinearQLearning:
    """
    Q(s, a) = w_a · φ(s)
    Mise à jour TD : w_a ← w_a + α · δ · φ(s)
    δ = r + γ max_a' Q(s',a') - Q(s,a)   (ici s'=s pour bandit contextuel one-step)
    Pour du one-step bandit : δ = r - Q(s,a)
    """

    def __init__(
        self,
        n_features: int,
        n_actions: int = N_ACTIONS,
        alpha: float = 0.05,
        gamma: float = 0.0,      # 0 = contextual bandit pur
        epsilon: float = 0.12,
        epsilon_decay: float = 0.995,
        epsilon_min: float = 0.03,
        model_dir: str = "models/rl",
    ):
        self.n_features = n_features
        self.n_actions = n_actions
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min = epsilon_min
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        # Poids : shape (n_actions, n_features + 1)  (+ bias)
        self.W = np.zeros((n_actions, n_features + 1))
        self.experience: List[Dict] = []
        self._load()

    def _phi(self, state: np.ndarray) -> np.ndarray:
        """Feature vector avec bias."""
        s = np.asarray(state, dtype=float).ravel()
        s = np.nan_to_num(s, nan=0.0, posinf=0.0, neginf=0.0)
        return np.concatenate([s, [1.0]])

    def q_values(self, state: np.ndarray) -> np.ndarray:
        phi = self._phi(state)
        return self.W @ phi

    def select_action(self, state: np.ndarray, explore: bool = True) -> Tuple[int, float, np.ndarray]:
        q = self.q_values(state)
        if explore and np.random.rand() < self.epsilon:
            a = int(np.random.randint(0, self.n_actions))
        else:
            a = int(np.argmax(q))
        mult = ACTIONS[a]["mult"]
        return a, mult, q

    def update_one(self, state: np.ndarray, action: int, reward: float) -> float:
        """Mise à jour one-step (bandit contextuel)."""
        phi = self._phi(state)
        q = float(self.W[action] @ phi)
        # TD target pour bandit : reward
        delta = reward - q
        self.W[action] += self.alpha * delta * phi
        return delta

    def batch_update(self, max_samples: int = 500) -> int:
        if not self.experience:
            return 0
        samples = self.experience[-max_samples:]
        n = 0
        for exp in samples:
            self.update_one(exp["state"], exp["action"], exp["reward"])
            n += 1
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        self._save()
        logger.info(f"RL batch update : {n} samples | ε={self.epsilon:.3f}")
        return n

    def record(self, state: np.ndarray, action: int, reward: float, meta: Optional[Dict] = None):
        self.experience.append({
            "state": np.asarray(state, dtype=float).tolist(),
            "action": int(action),
            "reward": float(reward),
            "meta": meta or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        # Limite mémoire
        if len(self.experience) > 5000:
            self.experience = self.experience[-3000:]

    def _save(self):
        path = self.model_dir / "linear_q.npz"
        np.savez(path, W=self.W, epsilon=np.array([self.epsilon]))
        exp_path = self.model_dir / "experience.json"
        with open(exp_path, "w") as f:
            json.dump(self.experience[-2000:], f)
        logger.info(f"RL model saved → {path}")

    def _load(self):
        path = self.model_dir / "linear_q.npz"
        exp_path = self.model_dir / "experience.json"
        try:
            if path.exists():
                data = np.load(path)
                self.W = data["W"]
                self.epsilon = float(data["epsilon"][0])
                logger.info(f"RL weights loaded | ε={self.epsilon:.3f}")
            if exp_path.exists():
                with open(exp_path) as f:
                    self.experience = json.load(f)
                logger.info(f"RL experience loaded : {len(self.experience)}")
        except Exception as e:
            logger.warning(f"RL load failed : {e}")


def build_state_vector(row: pd.Series) -> np.ndarray:
    """Construit le vecteur d'état à partir d'une ligne signal."""
    def f(key, default=0.0, scale=1.0):
        try:
            v = row.get(key, default)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return default
            return float(v) / scale
        except Exception:
            return default

    setup = str(row.get("setup") or "")
    vec = [
        f("meta_proba", 0.55),
        f("score_earnings_proximity", 40.0, 100.0),
        f("p_adverse", 0.25),
        f("days_to_earnings", 15.0, 30.0),   # normalisé
        f("vol_ratio", 1.0, 3.0),
        f("score_final", 50.0, 100.0),
        f("note_opportunite", 5.0, 10.0),
        1.0 if setup == "T1" else 0.0,
        1.0 if setup == "T2_CONFIRMATION" else 0.0,
        f("short_interest", 5.0, 20.0),
    ]
    return np.array(vec, dtype=float)


def reward_from_outcome(outcome: float, action: int) -> float:
    """
    Reward shaping :
    - Si IGNORE (0) et outcome négatif → petit reward positif (avoir évité la perte)
    - Si IGNORE et outcome positif → petit reward négatif (opportunité manquée)
    - Si TAKE et outcome positif → +reward * mult
    - Si TAKE et outcome négatif → -reward * mult
    """
    mult = ACTIONS[action]["mult"]
    o = 1.0 if outcome >= 0.5 else -1.0

    if action == 0:  # IGNORE
        return 0.15 if o < 0 else -0.25

    # TAKE
    base = 1.0 * o
    return base * (0.5 + 0.5 * mult)


class RLAgent:
    """Facade pour le pipeline TCT.

    Un modèle RL n'est considéré comme validé pour le sizing que si un fichier
    ``model_meta.json`` atteste d'un apprentissage sur outcomes réels.
    """

    def __init__(self, model_dir: str = "models/rl", **kwargs):
        self.model_dir = Path(model_dir)
        self.agent = LinearQLearning(
            n_features=len(STATE_FEATURES),
            model_dir=model_dir,
            **kwargs,
        )

    def validation_meta(self) -> Dict[str, Any]:
        path = self.model_dir / "model_meta.json"
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def is_validated(self, min_real_outcomes: int = 100) -> bool:
        meta = self.validation_meta()
        return (
            str(meta.get("training_source") or "").lower() == "real_outcomes"
            and int(meta.get("n_real_outcomes_total") or 0) >= int(min_real_outcomes)
        )

    def _mark_real_training(self, n: int) -> None:
        path = self.model_dir / "model_meta.json"
        old = self.validation_meta()
        total = int(old.get("n_real_outcomes_total") or 0) + int(n)
        payload = {
            "training_source": "real_outcomes",
            "n_real_outcomes_total": total,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def decide(self, row: pd.Series, explore: bool = False) -> Dict[str, Any]:
        state = build_state_vector(row)
        action, mult, q = self.agent.select_action(state, explore=explore)
        return {
            "rl_action": action,
            "rl_action_name": ACTIONS[action]["name"],
            "rl_mult": mult,
            "rl_q_values": q.tolist(),
            "rl_state": state.tolist(),
        }

    def learn_from_dataframe(self, df: pd.DataFrame, outcome_col: str = "outcome") -> int:
        """Enregistre et met à jour à partir d'un batch de signaux labellisés."""
        if df is None or df.empty:
            return 0
        n = 0
        for _, row in df.iterrows():
            state = build_state_vector(row)
            # Action : si déjà prise via rl_action, sinon inférée du decision/position
            if "rl_action" in row and pd.notna(row["rl_action"]):
                action = int(row["rl_action"])
            elif str(row.get("decision", "")).upper() == "IGNORE":
                action = 0
            else:
                pct = float(row.get("position_pct", 0) or 0)
                if pct <= 0:
                    action = 0
                elif pct < 0.006:
                    action = 1
                elif pct < 0.012:
                    action = 2
                else:
                    action = 3

            outcome = float(row.get(outcome_col, 0) or 0)
            reward = reward_from_outcome(outcome, action)
            self.agent.record(state, action, reward, meta={"isin": row.get("isin")})
            n += 1

        self.agent.batch_update()
        if n > 0:
            self._mark_real_training(n)
        return n

    def get_multiplier(self, row: pd.Series, explore: bool = False) -> float:
        return self.decide(row, explore=explore)["rl_mult"]
