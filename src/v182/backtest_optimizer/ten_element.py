from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v182.decision.etf102_committee_v207 import _metric_score

from .config import OptimizerConfig
from .engine import BacktestOptimizer, OptimizationResult

ROOT = Path(__file__).resolve().parents[3]
ETF_CONFIG = ROOT / "data" / "reference" / "V20.7_ETF102_CONFIG.json"


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _feature_overrides_from_element(element: dict[str, Any]) -> dict[str, dict[str, object]]:
    overrides: dict[str, dict[str, object]] = {}
    for name, spec in element.get("features", {}).items():
        overrides[name] = {
            "candidates": [spec["column"]],
            "baseline": float(spec["baseline"]),
            "optional": bool(spec.get("optional", False)),
        }
    return overrides


def _prepare_etf_direct(df: pd.DataFrame, element: dict[str, Any], etf_cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, dict[str, object]]]:
    horizon = str(element["source_horizon"]).upper()
    weights = etf_cfg["direct_weights"][horizon]
    out = df.copy()
    overrides: dict[str, dict[str, object]] = {}
    groups = out.groupby("__snapshot_date", sort=False).groups
    for field, baseline in weights.items():
        feature_name = _safe(field)
        column = f"__bt_{horizon.lower()}_{feature_name}"
        values = pd.Series(np.nan, index=out.index, dtype=float)
        for indices in groups.values():
            idx = list(indices)
            scored = _metric_score(out.loc[idx], field, horizon, etf_cfg)
            values.loc[idx] = pd.to_numeric(scored, errors="coerce").to_numpy()
        out[column] = values
        overrides[feature_name] = {
            "candidates": [column],
            "baseline": float(baseline),
            "optional": False,
            "source_metric": field,
        }
    return out, overrides


def _feature_coverage(df: pd.DataFrame, overrides: dict[str, dict[str, object]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for name, spec in overrides.items():
        candidates = list(spec.get("candidates", []))
        col = candidates[0] if candidates else name
        if col not in df.columns or len(df) == 0:
            result[name] = 0.0
        else:
            result[name] = round(float(pd.to_numeric(df[col], errors="coerce").notna().mean()), 4)
    return result


def _optimizer_config(
    master: dict[str, Any],
    element: dict[str, Any],
    overrides: dict[str, dict[str, object]],
    index: int,
    candidate_count: int | None,
) -> OptimizerConfig:
    baselines = [float(v.get("baseline", 0.0)) for v in overrides.values()]
    max_baseline = max(baselines, default=0.0)
    return OptimizerConfig(
        horizon_days=int(element["horizon_days"]),
        horizon_tolerance_days=int(element["horizon_tolerance_days"]),
        top_k=int(element["top_k"]),
        candidate_count=int(candidate_count or master.get("default_candidate_count", 1000)),
        random_seed=20421 + index * 997,
        min_snapshots=int(element.get("min_snapshots", master.get("default_min_snapshots", 12))),
        min_test_snapshots=int(element.get("min_test_snapshots", master.get("default_min_test_snapshots", 4))),
        train_fraction=float(element.get("train_fraction", master.get("default_train_fraction", 0.67))),
        min_instruments_per_snapshot=int(element["min_instruments_per_snapshot"]),
        transaction_cost_bps=float(element["transaction_cost_bps"]),
        baseline_blend=float(element.get("baseline_blend", 0.45)),
        max_single_weight=max(0.45, min(0.65, max_baseline + 0.12)),
        max_optional_weight=float(element.get("max_optional_weight", 0.12)),
        max_weight_drift_l1=float(element.get("max_weight_drift_l1", 0.50)),
        min_oos_improvement=float(element.get("min_oos_improvement", 0.0025)),
        max_drawdown_worsening=float(element.get("max_drawdown_worsening", 0.03)),
        include_default_features=False,
        missing_feature_policy="RENORMALIZE_OBSERVED",
        min_feature_weight_coverage=float(element.get("min_feature_weight_coverage", 0.60)),
        feature_overrides=overrides,
    )


def _weights_frame(result: OptimizationResult, element_id: str) -> pd.DataFrame:
    rows = []
    for feature, current in result.baseline_weights.items():
        recommended = result.recommended_weights.get(feature, current)
        delta = recommended - current
        if result.status == "ROBUST_RECOMMENDATION":
            action = "KEEP" if abs(delta) < 0.01 else ("INCREASE" if delta > 0 else "DECREASE")
        elif result.status == "INSUFFICIENT_HISTORY":
            action = "WAIT_HISTORY"
        else:
            action = "KEEP_CURRENT"
        rows.append({
            "element": element_id,
            "feature": feature,
            "current_weight": current,
            "recommended_weight": recommended,
            "delta": delta,
            "action": action,
        })
    return pd.DataFrame(rows)


def _element_summary(element: dict[str, Any], result: OptimizationResult, coverage: dict[str, float]) -> str:
    direction = "BAISSE" if int(element.get("target_direction", 1)) < 0 else "HAUSSE"
    lines = [
        f"# {element['id']}",
        "",
        f"- Classe : **{element['asset_class']}**",
        f"- Horizon cible : **{element['horizon_days']} jours**",
        f"- Direction évaluée : **{direction}**",
        f"- Statut : **{result.status}**",
        f"- Snapshots exploitables : **{result.audit.get('snapshot_count', 0)}**",
        f"- Politique données manquantes : **{result.audit.get('missing_feature_policy', 'NA')}**",
        "- Pondérations de production modifiées : **NON**",
        "",
        "## Couverture des critères",
        "| Critère | Couverture |",
        "|---|---:|",
    ]
    for feature, value in coverage.items():
        lines.append(f"| {feature} | {value:.1%} |")
    lines += ["", "## Recommandations", "| Critère | Actuel | Recommandé | Décision |", "|---|---:|---:|---|"]
    weights = _weights_frame(result, str(element["id"]))
    for _, row in weights.iterrows():
        lines.append(
            f"| {row['feature']} | {float(row['current_weight']):.2%} | {float(row['recommended_weight']):.2%} | {row['action']} |"
        )
    if result.baseline_metrics:
        lines += [
            "",
            "## Validation hors échantillon",
            "| Mesure | Actuel | Recommandé |",
            "|---|---:|---:|",
        ]
        for metric in ("mean_return", "annualized_return", "annualized_vol", "max_drawdown", "hit_rate", "turnover"):
            a = result.baseline_metrics.get(metric, math.nan)
            b = result.recommended_metrics.get(metric, math.nan)
            lines.append(f"| {metric} | {a:.4f} | {b:.4f} |")
    if result.audit.get("reason"):
        lines += ["", f"**Motif :** {result.audit['reason']}"]
    return "\n".join(lines) + "\n"


def _empty_result(status: str, reason: str, element: dict[str, Any], overrides: dict[str, dict[str, object]]) -> OptimizationResult:
    baseline = {k: float(v.get("baseline", 0.0)) for k, v in overrides.items()}
    total = sum(baseline.values())
    if total > 0:
        baseline = {k: v / total for k, v in baseline.items()}
    audit = {
        "optimizer_version": "BACKTEST_OPTIMIZER_V1",
        "element": element["id"],
        "snapshot_count": 0,
        "reason": reason,
        "production_weights_modified": False,
    }
    return OptimizationResult(status, baseline, baseline, {}, {}, {}, pd.DataFrame(), pd.DataFrame(), audit)


def _cross_horizon_conflicts(recommendations: pd.DataFrame) -> list[dict[str, object]]:
    if recommendations.empty:
        return []
    conflicts: list[dict[str, object]] = []
    active = recommendations[recommendations["action"].isin(["INCREASE", "DECREASE"])].copy()
    for (asset_class, feature), g in active.groupby(["asset_class", "feature"]):
        actions = set(g["action"].astype(str))
        if {"INCREASE", "DECREASE"}.issubset(actions):
            conflicts.append({
                "asset_class": asset_class,
                "feature": feature,
                "elements": g["element"].astype(str).tolist(),
                "message": "opposite robust directions across horizons; keep recommendation horizon-specific",
            })
    return conflicts


def run_ten_elements(history_root: Path, config_path: Path, output_root: Path, candidate_count: int | None = None) -> dict[str, Any]:
    master = _load_json(config_path)
    etf_cfg = _load_json(ETF_CONFIG)
    output_root.mkdir(parents=True, exist_ok=True)
    histories: dict[str, pd.DataFrame] = {}
    for asset_class in ("ETF", "ACTION"):
        path = history_root / f"{asset_class}_HISTORY.parquet"
        histories[asset_class] = pd.read_parquet(path) if path.exists() else pd.DataFrame()

    master_rows: list[dict[str, Any]] = []
    recommendation_frames: list[pd.DataFrame] = []
    fatal_errors: list[str] = []

    for index, element in enumerate(master["elements"], start=1):
        element_id = str(element["id"])
        element_dir = output_root / f"{index:02d}_{element_id}"
        element_dir.mkdir(parents=True, exist_ok=True)
        asset_class = str(element["asset_class"])
        df = histories.get(asset_class, pd.DataFrame()).copy()
        overrides: dict[str, dict[str, object]] = {}
        result: OptimizationResult
        coverage: dict[str, float] = {}
        try:
            if df.empty:
                overrides = _feature_overrides_from_element(element)
                result = _empty_result("INSUFFICIENT_HISTORY", f"no {asset_class} rolling history available", element, overrides)
            else:
                outcome_col = f"__forward_return_{int(element['horizon_days'])}d"
                if outcome_col not in df.columns:
                    overrides = _feature_overrides_from_element(element)
                    result = _empty_result("INSUFFICIENT_HISTORY", f"missing outcome column {outcome_col}", element, overrides)
                else:
                    df["__forward_return"] = pd.to_numeric(df[outcome_col], errors="coerce") * int(element.get("target_direction", 1))
                    if element["mode"] == "ETF_DIRECT":
                        df, overrides = _prepare_etf_direct(df, element, etf_cfg)
                    else:
                        overrides = _feature_overrides_from_element(element)
                    coverage = _feature_coverage(df, overrides)
                    cfg = _optimizer_config(master, element, overrides, index, candidate_count)
                    result = BacktestOptimizer(cfg).optimize(df)
                    result.audit.update({
                        "element": element_id,
                        "asset_class": asset_class,
                        "target_direction": int(element.get("target_direction", 1)),
                        "source_mode": element["mode"],
                        "feature_coverage": coverage,
                        "production_weights_modified": False,
                    })
        except ValueError as exc:
            result = _empty_result("INSUFFICIENT_FEATURES", str(exc), element, overrides)
        except Exception as exc:
            result = _empty_result("ERROR", f"{type(exc).__name__}: {exc}", element, overrides)
            fatal_errors.append(f"{element_id}: {type(exc).__name__}: {exc}")

        weights = _weights_frame(result, element_id)
        weights["asset_class"] = asset_class
        weights["horizon_days"] = int(element["horizon_days"])
        recommendation_frames.append(weights)
        weights.to_csv(element_dir / "WEIGHTS.csv", index=False)
        result.sensitivity.to_csv(element_dir / "SENSITIVITY.csv", index=False)
        result.leaderboard.head(250).to_csv(element_dir / "LEADERBOARD_TOP250.csv", index=False)
        (element_dir / "AUDIT.json").write_text(json.dumps(result.audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (element_dir / "SUMMARY.md").write_text(_element_summary(element, result, coverage), encoding="utf-8")

        master_rows.append({
            "sequence": index,
            "element": element_id,
            "asset_class": asset_class,
            "horizon_days": int(element["horizon_days"]),
            "direction": "DOWN" if int(element.get("target_direction", 1)) < 0 else "UP",
            "status": result.status,
            "eligible_snapshots": int(result.audit.get("snapshot_count", 0)),
            "train_snapshots": int(result.audit.get("train_snapshot_count", 0)),
            "test_snapshots": int(result.audit.get("test_snapshot_count", 0)),
            "oos_mean_return_improvement": result.audit.get("oos_mean_return_improvement"),
            "oos_drawdown_worsening": result.audit.get("oos_drawdown_worsening"),
            "weight_drift_l1": result.audit.get("weight_drift_l1"),
            "recommendation": (
                "OPTIMIZE_ELEMENT" if result.status == "ROBUST_RECOMMENDATION"
                else "WAIT_HISTORY" if result.status == "INSUFFICIENT_HISTORY"
                else "WAIT_FEATURES" if result.status == "INSUFFICIENT_FEATURES"
                else "KEEP_CURRENT" if result.status == "NO_ROBUST_IMPROVEMENT"
                else "TECHNICAL_REVIEW"
            ),
            "reason": result.audit.get("reason", ""),
        })

    master_df = pd.DataFrame(master_rows)
    recommendations = pd.concat(recommendation_frames, ignore_index=True) if recommendation_frames else pd.DataFrame()
    conflicts = _cross_horizon_conflicts(recommendations)
    master_df.to_csv(output_root / "MASTER_RESULTS.csv", index=False)
    recommendations.to_csv(output_root / "OPTIMIZATION_RECOMMENDATIONS.csv", index=False)

    audit = {
        "version": master["version"],
        "execution": "RESEARCH_ONLY",
        "sequential_independent_elements": True,
        "element_count": len(master_rows),
        "expected_element_count": 10,
        "statuses": master_df["status"].value_counts().to_dict() if not master_df.empty else {},
        "cross_horizon_conflicts": conflicts,
        "fatal_errors": fatal_errors,
        "production_weights_modified": False,
        "methodology": {
            "point_in_time_features": True,
            "later_snapshot_outcomes_only": True,
            "chronological_holdout": True,
            "observed_weight_renormalization": True,
            "short_direction_inverted": True,
            "no_weight_carryover_between_elements": True,
        },
    }
    (output_root / "MASTER_AUDIT.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Audit final — BACKTEST 10 ELEMENTS V1",
        "",
        "Les 10 backtests sont exécutés **consécutivement et indépendamment**. Aucune pondération optimisée n'est transmise au test suivant et aucune pondération de production n'est modifiée.",
        "",
        "## Résultats par élément",
        "| # | Élément | Classe | Horizon | Direction | Statut | Snapshots | Recommandation |",
        "|---:|---|---|---:|---|---|---:|---|",
    ]
    for _, row in master_df.iterrows():
        lines.append(
            f"| {int(row['sequence'])} | {row['element']} | {row['asset_class']} | {int(row['horizon_days'])} j | {row['direction']} | {row['status']} | {int(row['eligible_snapshots'])} | {row['recommendation']} |"
        )
    lines += ["", "## Recommandations d'optimisation élément par élément"]
    for _, row in master_df.iterrows():
        lines.append(f"- **{row['element']}** : `{row['recommendation']}`" + (f" — {row['reason']}" if row['reason'] else ""))
    if conflicts:
        lines += ["", "## Conflits entre horizons"]
        for item in conflicts:
            lines.append(
                f"- **{item['asset_class']} / {item['feature']}** : directions opposées selon les horizons ({', '.join(item['elements'])}). Ne pas appliquer de changement global ; conserver une pondération spécifique à l'horizon."
            )
    lines += [
        "",
        "## Verdict de contrôle",
        f"- Nombre d'éléments : **{len(master_rows)}/10**",
        f"- Erreurs techniques : **{len(fatal_errors)}**",
        "- Look-ahead sur les critères : **interdit**",
        "- Données manquantes : **renormalisation des poids observés, sans 50 neutre**",
        "- Mise en production automatique des nouveaux poids : **désactivée**",
    ]
    (output_root / "MASTER_AUDIT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ten sequential independent ETF/Action backtests")
    parser.add_argument("--history", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-count", type=int, default=None)
    args = parser.parse_args()
    audit = run_ten_elements(Path(args.history), Path(args.config), Path(args.output), args.candidate_count)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 2 if audit["fatal_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
