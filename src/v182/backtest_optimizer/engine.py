from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .config import DEFAULT_FEATURES, OptimizerConfig
from .data import coerce_score, first_column
from .metrics import objective, portfolio_metrics


@dataclass
class OptimizationResult:
    status: str
    baseline_weights: dict[str, float]
    recommended_weights: dict[str, float]
    baseline_metrics: dict[str, float]
    recommended_metrics: dict[str, float]
    train_metrics: dict[str, float]
    sensitivity: pd.DataFrame
    leaderboard: pd.DataFrame
    audit: dict[str, object]


@dataclass(frozen=True)
class _FeatureSpec:
    name: str
    column: str
    baseline: float
    optional: bool


class BacktestOptimizer:
    def __init__(self, config: OptimizerConfig | None = None):
        self.config = config or OptimizerConfig()

    def _resolve_features(self, df: pd.DataFrame) -> list[_FeatureSpec]:
        specs: list[_FeatureSpec] = []
        handled: set[str] = set()
        if self.config.include_default_features:
            for name, (default_candidates, default_baseline) in DEFAULT_FEATURES.items():
                override = self.config.feature_overrides.get(name, {})
                candidates = tuple(override.get("candidates", default_candidates))
                baseline = float(override.get("baseline", default_baseline))
                col = first_column(df, candidates)
                handled.add(name)
                if col is None:
                    continue
                optional = bool(override.get("optional", default_baseline == 0.0))
                specs.append(_FeatureSpec(name, col, baseline, optional))
        for name, override in self.config.feature_overrides.items():
            if name in handled:
                continue
            candidates = tuple(override.get("candidates", (name,)))
            col = first_column(df, candidates)
            if col is None:
                continue
            baseline = float(override.get("baseline", 0.0))
            optional = bool(override.get("optional", baseline == 0.0))
            specs.append(_FeatureSpec(name, col, baseline, optional))
        if len(specs) < 2:
            raise ValueError("At least two point-in-time score features are required")
        return specs

    @staticmethod
    def _normalise_weights(weights: np.ndarray) -> np.ndarray:
        total = float(weights.sum())
        if total <= 0:
            return np.repeat(1.0 / len(weights), len(weights))
        return weights / total

    def _cap_and_normalise(self, weights: np.ndarray, specs: list[_FeatureSpec]) -> np.ndarray:
        w = np.maximum(np.asarray(weights, dtype=float), 0.0)
        caps = np.array([
            self.config.max_optional_weight if spec.optional else self.config.max_single_weight
            for spec in specs
        ], dtype=float)
        if float(caps.sum()) < 1.0 - 1e-9:
            raise ValueError("Configured weight caps cannot sum to 100%")
        w = self._normalise_weights(w)
        for _ in range(20):
            over = w > caps + 1e-12
            if not over.any():
                break
            fixed = np.minimum(w, caps)
            room = np.maximum(caps - fixed, 0.0)
            deficit = 1.0 - float(fixed.sum())
            if deficit <= 1e-12:
                w = fixed
                break
            eligible = room > 1e-12
            if not eligible.any():
                w = fixed
                break
            seed = w.copy()
            seed[~eligible] = 0.0
            if seed.sum() <= 0:
                seed = room.copy()
            addition = deficit * seed / seed.sum()
            w = fixed + np.minimum(addition, room)
            remaining = 1.0 - float(w.sum())
            if remaining > 1e-12:
                room = np.maximum(caps - w, 0.0)
                room_sum = float(room.sum())
                if room_sum > 0:
                    w += remaining * room / room_sum
        if abs(float(w.sum()) - 1.0) > 1e-8:
            w = self._normalise_weights(w)
        return w

    def _baseline(self, specs: list[_FeatureSpec]) -> np.ndarray:
        w = np.array([max(0.0, s.baseline) for s in specs], dtype=float)
        if w.sum() <= 0:
            w[:] = 1.0
        return self._cap_and_normalise(w, specs)

    def _candidate_weights(self, specs: list[_FeatureSpec]) -> np.ndarray:
        rng = np.random.default_rng(self.config.random_seed)
        base = self._baseline(specs)
        eps = 0.02
        alpha = np.maximum(base, eps) * 45.0 + 0.20
        rows = [base]
        for _ in range(max(1, self.config.candidate_count - 1)):
            sampled = rng.dirichlet(alpha)
            strength = rng.uniform(0.20, 1.0)
            w = self._cap_and_normalise((1.0 - strength) * base + strength * sampled, specs)
            drift = float(np.abs(w - base).sum())
            if drift > self.config.max_weight_drift_l1:
                scale = self.config.max_weight_drift_l1 / drift
                w = self._cap_and_normalise(base + scale * (w - base), specs)
            rows.append(w)
        return np.vstack(rows)

    def _prepare(self, raw: pd.DataFrame) -> tuple[pd.DataFrame, list[_FeatureSpec]]:
        if raw.empty:
            raise ValueError("No valid point-in-time snapshots found")
        specs = self._resolve_features(raw)
        df = raw.copy()
        for spec in specs:
            df[f"__f_{spec.name}"] = coerce_score(df[spec.column])
        feature_cols = [f"__f_{s.name}" for s in specs]
        df = df.dropna(subset=["__forward_return"])
        policy = self.config.missing_feature_policy.upper()
        if policy == "NEUTRAL_50":
            df[feature_cols] = df[feature_cols].fillna(50.0)
            df["__baseline_feature_coverage"] = 1.0
        elif policy == "RENORMALIZE_OBSERVED":
            base = self._baseline(specs)
            matrix = df[feature_cols].to_numpy(float)
            coverage = (~np.isnan(matrix)).astype(float) @ base
            df["__baseline_feature_coverage"] = coverage
            df = df[df["__baseline_feature_coverage"] >= self.config.min_feature_weight_coverage]
        else:
            raise ValueError(f"Unsupported missing_feature_policy: {self.config.missing_feature_policy}")
        counts = df.groupby("__snapshot_date")["__instrument_id"].size()
        valid_dates = counts[counts >= self.config.min_instruments_per_snapshot].index
        df = df[df["__snapshot_date"].isin(valid_dates)].copy()
        return df, specs

    def _simulate(self, df: pd.DataFrame, specs: list[_FeatureSpec], weights: np.ndarray) -> tuple[dict[str, float], pd.DataFrame]:
        fcols = [f"__f_{s.name}" for s in specs]
        work = df[["__snapshot_date", "__instrument_id", "__forward_return", *fcols]].copy()
        matrix = work[fcols].to_numpy(float)
        if self.config.missing_feature_policy.upper() == "RENORMALIZE_OBSERVED":
            observed = ~np.isnan(matrix)
            denominator = observed.astype(float) @ weights
            numerator = np.nansum(matrix * weights, axis=1)
            score = np.divide(numerator, denominator, out=np.full(len(work), np.nan), where=denominator > 0)
            score[denominator < self.config.min_feature_weight_coverage] = np.nan
            work["__model_score"] = score
        else:
            work["__model_score"] = matrix @ weights
        periods: list[dict[str, object]] = []
        previous: set[str] = set()
        for date, g in work.groupby("__snapshot_date", sort=True):
            eligible = g.dropna(subset=["__model_score"])
            required = min(self.config.top_k, len(g))
            if len(eligible) < max(3, required):
                continue
            chosen = eligible.nlargest(required, "__model_score")
            ids = set(chosen["__instrument_id"].astype(str))
            gross = float(chosen["__forward_return"].mean())
            if previous:
                overlap = len(ids & previous) / max(1, len(ids | previous))
                turnover = 1.0 - overlap
            else:
                turnover = 1.0
            cost = turnover * self.config.transaction_cost_bps / 10000.0
            periods.append({
                "snapshot_date": date,
                "gross_return": gross,
                "turnover": turnover,
                "net_return": gross - cost,
            })
            previous = ids
        period_df = pd.DataFrame(periods)
        return portfolio_metrics(period_df), period_df

    def _leaderboard(self, train: pd.DataFrame, specs: list[_FeatureSpec]) -> pd.DataFrame:
        rows: list[dict[str, float]] = []
        for idx, w in enumerate(self._candidate_weights(specs)):
            metrics, _ = self._simulate(train, specs, w)
            row: dict[str, float] = {"candidate": float(idx), "objective": objective(metrics, self.config), **metrics}
            row.update({f"w_{s.name}": float(w[i]) for i, s in enumerate(specs)})
            rows.append(row)
        return pd.DataFrame(rows).sort_values("objective", ascending=False).reset_index(drop=True)

    def _sensitivity(self, train: pd.DataFrame, specs: list[_FeatureSpec], baseline: np.ndarray) -> pd.DataFrame:
        base_metrics, _ = self._simulate(train, specs, baseline)
        base_obj = objective(base_metrics, self.config)
        rows: list[dict[str, float | str]] = []
        for i, spec in enumerate(specs):
            for delta in (-0.20, 0.20):
                w = baseline.copy()
                w[i] = max(0.0, w[i] * (1.0 + delta))
                w = self._cap_and_normalise(w, specs)
                metrics, _ = self._simulate(train, specs, w)
                rows.append({
                    "feature": spec.name,
                    "perturbation": delta,
                    "objective_delta": objective(metrics, self.config) - base_obj,
                    "annualized_return": metrics["annualized_return"],
                    "max_drawdown": metrics["max_drawdown"],
                    "hit_rate": metrics["hit_rate"],
                })
        return pd.DataFrame(rows)

    def optimize(self, raw: pd.DataFrame) -> OptimizationResult:
        df, specs = self._prepare(raw)
        dates = sorted(pd.to_datetime(df["__snapshot_date"].unique()))
        baseline = self._baseline(specs)
        baseline_map = {s.name: float(baseline[i]) for i, s in enumerate(specs)}
        audit: dict[str, object] = {
            "optimizer_version": "BACKTEST_OPTIMIZER_V1",
            "horizon_days": self.config.horizon_days,
            "snapshot_count": len(dates),
            "row_count": int(len(df)),
            "feature_columns": {s.name: s.column for s in specs},
            "missing_feature_policy": self.config.missing_feature_policy,
            "min_feature_weight_coverage": self.config.min_feature_weight_coverage,
            "lookahead_guard": "forward returns derived only from later archived snapshots",
            "production_weights_modified": False,
        }
        if len(dates) < self.config.min_snapshots:
            empty = pd.DataFrame()
            audit["reason"] = f"need >= {self.config.min_snapshots} eligible snapshots; found {len(dates)}"
            return OptimizationResult(
                "INSUFFICIENT_HISTORY", baseline_map, baseline_map, {}, {}, {}, empty, empty, audit
            )

        split = int(len(dates) * self.config.train_fraction)
        split = max(1, split)
        split = min(split, len(dates) - self.config.min_test_snapshots)
        if split <= 0 or len(dates) - split < self.config.min_test_snapshots:
            empty = pd.DataFrame()
            audit["reason"] = "chronological holdout cannot satisfy minimum test snapshots"
            return OptimizationResult(
                "INSUFFICIENT_HISTORY", baseline_map, baseline_map, {}, {}, {}, empty, empty, audit
            )
        train_dates = set(dates[:split])
        test_dates = set(dates[split:])
        train = df[df["__snapshot_date"].isin(train_dates)]
        test = df[df["__snapshot_date"].isin(test_dates)]

        leaderboard = self._leaderboard(train, specs)
        best = leaderboard.iloc[0]
        raw_best = np.array([float(best[f"w_{s.name}"]) for s in specs])
        robust = self._cap_and_normalise(
            self.config.baseline_blend * baseline + (1.0 - self.config.baseline_blend) * raw_best,
            specs,
        )

        base_train, _ = self._simulate(train, specs, baseline)
        robust_train, _ = self._simulate(train, specs, robust)
        base_test, _ = self._simulate(test, specs, baseline)
        robust_test, _ = self._simulate(test, specs, robust)
        improvement = robust_test["mean_return"] - base_test["mean_return"]
        dd_worsening = abs(robust_test["max_drawdown"]) - abs(base_test["max_drawdown"])
        drift = float(np.abs(robust - baseline).sum())

        accepted = (
            improvement >= self.config.min_oos_improvement
            and dd_worsening <= self.config.max_drawdown_worsening
            and drift <= self.config.max_weight_drift_l1
        )
        status = "ROBUST_RECOMMENDATION" if accepted else "NO_ROBUST_IMPROVEMENT"
        recommended = robust if accepted else baseline
        recommended_test = robust_test if accepted else base_test
        recommended_train = robust_train if accepted else base_train
        audit.update({
            "train_snapshot_count": len(train_dates),
            "test_snapshot_count": len(test_dates),
            "oos_mean_return_improvement": float(improvement),
            "oos_drawdown_worsening": float(dd_worsening),
            "weight_drift_l1": drift,
            "acceptance": bool(accepted),
            "acceptance_rules": {
                "min_oos_improvement": self.config.min_oos_improvement,
                "max_drawdown_worsening": self.config.max_drawdown_worsening,
                "max_weight_drift_l1": self.config.max_weight_drift_l1,
            },
        })
        return OptimizationResult(
            status=status,
            baseline_weights=baseline_map,
            recommended_weights={s.name: float(recommended[i]) for i, s in enumerate(specs)},
            baseline_metrics=base_test,
            recommended_metrics=recommended_test,
            train_metrics=recommended_train,
            sensitivity=self._sensitivity(train, specs, baseline),
            leaderboard=leaderboard,
            audit=audit,
        )
