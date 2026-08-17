from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from v182.risk.beta_metrics import jaccard

ACTIVE_DECISIONS = {"BUY", "BUY_CANDIDATE", "HOLD"}


def economic_overlap_scores(rows: pd.DataFrame, returns_by_isin: dict[str, pd.Series]) -> list[float | None]:
    """Compute the legacy overlap score once per unique ISIN/tag combination.

    Committee decisions contain the same ISIN on several horizons. The previous
    implementation recomputed the exact same return correlation for every
    horizon row, turning a ~1,931-instrument risk context into tens/hundreds of
    thousands of redundant pandas concat/corr operations. This implementation
    preserves the scoring formula and pairwise 126-session semantics while
    caching pair correlations and reusing identical ISIN/tag results.
    """
    active_rows = rows[
        rows["decision"].astype(str).str.upper().isin(ACTIVE_DECISIONS)
    ]
    active_tags_by_isin: dict[str, set[tuple[str, ...]]] = {}
    for _, row in active_rows.iterrows():
        other_isin = str(row.get("isin") or "")
        if not other_isin:
            continue
        tags = tuple(str(row.get("risk_engine_tags") or "").split("|"))
        active_tags_by_isin.setdefault(other_isin, set()).add(tags)

    pair_corr_cache: dict[tuple[str, str], float | None] = {}

    def pair_corr(left_isin: str, right_isin: str) -> float | None:
        key = tuple(sorted((left_isin, right_isin)))
        if key in pair_corr_cache:
            return pair_corr_cache[key]
        left = returns_by_isin.get(left_isin)
        right = returns_by_isin.get(right_isin)
        if left is None or right is None:
            pair_corr_cache[key] = None
            return None
        pair = pd.concat([left, right], axis=1).dropna().tail(126)
        if len(pair) < 40:
            pair_corr_cache[key] = None
            return None
        corr = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
        value = corr if math.isfinite(corr) else None
        pair_corr_cache[key] = value
        return value

    score_cache: dict[tuple[str, tuple[str, ...]], float | None] = {}

    def score_for(isin: str, current_tags: tuple[str, ...]) -> float | None:
        cache_key = (isin, current_tags)
        if cache_key in score_cache:
            return score_cache[cache_key]
        if returns_by_isin.get(isin) is None:
            score_cache[cache_key] = None
            return None

        candidates: list[float] = []
        for other_isin, other_tag_variants in active_tags_by_isin.items():
            if other_isin == isin:
                continue
            corr = pair_corr(isin, other_isin)
            if corr is None:
                continue
            corr_component = max(0.0, min(1.0, corr))
            for other_tags in other_tag_variants:
                tag_component = jaccard(list(current_tags), list(other_tags))
                candidates.append(
                    100.0 * (0.70 * corr_component + 0.30 * tag_component)
                )
        value = round(max(candidates), 4) if candidates else 0.0
        score_cache[cache_key] = value
        return value

    scores: list[float | None] = []
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "")
        tags = tuple(str(row.get("risk_engine_tags") or "").split("|"))
        scores.append(score_for(isin, tags))
    return scores


def _weight_column(frame: pd.DataFrame) -> str | None:
    for field in ("position_weight_pct", "target_weight_pct", "allocation_pct", "portfolio_weight_pct", "weight_pct"):
        if field in frame.columns and pd.to_numeric(frame[field], errors="coerce").notna().any():
            return field
    return None


def portfolio_summary(
    rows: pd.DataFrame,
    returns_by_isin: dict[str, pd.Series],
    benchmark: pd.Series,
    scenarios_pct: list[float],
) -> dict:
    active = rows[rows["decision"].astype(str).str.upper().isin(ACTIVE_DECISIONS)].copy()
    active = active[active["isin"].astype(str).isin(returns_by_isin)]
    if active.empty:
        return {"status": "NO_ACTIVE_DECISIONS_WITH_HISTORY", "active_rows": 0, "weight_method": "NONE"}
    weight_col = _weight_column(active)
    weights = pd.to_numeric(active[weight_col], errors="coerce").clip(lower=0) if weight_col else pd.Series(1.0, index=active.index)
    if weights.fillna(0).sum() <= 0:
        weights = pd.Series(1.0, index=active.index)
        weight_method = "EQUAL_WEIGHT_FALLBACK"
    else:
        weights = weights.fillna(0)
        weight_method = weight_col or "EQUAL_WEIGHT_FALLBACK"
    weights = weights / weights.sum()
    active = active.assign(_risk_weight=weights)

    beta = pd.to_numeric(active.get("risk_beta_252d"), errors="coerce")
    downside = pd.to_numeric(active.get("risk_downside_beta_252d"), errors="coerce")
    valid_beta, valid_down = beta.notna(), downside.notna()
    portfolio_beta = float(np.average(beta[valid_beta], weights=weights[valid_beta])) if valid_beta.any() else None
    portfolio_down = float(np.average(downside[valid_down], weights=weights[valid_down])) if valid_down.any() else None

    engine_weights: dict[str, float] = {}
    for idx, row in active.iterrows():
        tags = [tag for tag in str(row.get("risk_engine_tags") or "OTHER").split("|") if tag and tag != "OTHER"] or ["OTHER"]
        allocation = float(active.at[idx, "_risk_weight"]) / len(tags)
        for tag in tags:
            engine_weights[tag] = engine_weights.get(tag, 0.0) + allocation
    top_engine = max(engine_weights, key=engine_weights.get) if engine_weights else "OTHER"
    top_share = float(engine_weights.get(top_engine, 0.0))

    pair_corrs: list[float] = []
    stress_corrs: list[float] = []
    active_isins = active["isin"].astype(str).tolist()
    stress_dates = benchmark[benchmark <= benchmark.quantile(0.10)].index
    for pos, left in enumerate(active_isins):
        for right in active_isins[pos + 1 :]:
            pair = pd.concat([returns_by_isin[left], returns_by_isin[right]], axis=1).dropna().tail(252)
            if len(pair) >= 40:
                value = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
                if math.isfinite(value):
                    pair_corrs.append(value)
            stress = pd.concat([returns_by_isin[left], returns_by_isin[right]], axis=1).dropna()
            stress = stress.loc[stress.index.intersection(stress_dates)]
            if len(stress) >= 12:
                value = float(stress.iloc[:, 0].corr(stress.iloc[:, 1]))
                if math.isfinite(value):
                    stress_corrs.append(value)
    mean_corr = float(np.mean(pair_corrs)) if pair_corrs else None
    mean_stress = float(np.mean(stress_corrs)) if stress_corrs else None
    if top_share >= 0.70 or (mean_stress is not None and mean_stress >= 0.85):
        warning = "RED"
    elif top_share >= 0.55 or (mean_stress is not None and mean_stress >= 0.75):
        warning = "ORANGE"
    elif top_share >= 0.40 or (mean_stress is not None and mean_stress >= 0.60):
        warning = "AMBER"
    else:
        warning = "GREEN"
    stress_beta = portfolio_down if portfolio_down is not None else portfolio_beta
    scenarios = {str(float(s)): round(float(s) * stress_beta, 4) if stress_beta is not None else None for s in scenarios_pct}
    return {
        "status": "OK",
        "active_rows": int(len(active)),
        "weight_method": weight_method,
        "portfolio_beta_252d": round(portfolio_beta, 6) if portfolio_beta is not None else None,
        "portfolio_downside_beta_252d": round(portfolio_down, 6) if portfolio_down is not None else None,
        "mean_pair_correlation_252d": round(mean_corr, 6) if mean_corr is not None else None,
        "mean_stress_pair_correlation": round(mean_stress, 6) if mean_stress is not None else None,
        "top_engine": top_engine,
        "top_engine_share_pct": round(top_share * 100.0, 4),
        "engine_weight_pct": {key: round(value * 100.0, 4) for key, value in sorted(engine_weights.items(), key=lambda item: -item[1])},
        "diversification_warning": warning,
        "systematic_stress_scenarios_pct": scenarios,
        "stress_scenario_semantic": "SCENARIO_X_DOWNSIDE_BETA_SYSTEMATIC_COMPONENT_NOT_TOTAL_LOSS_FORECAST",
    }


def sector_overlay(rows: pd.DataFrame, root: Path) -> pd.DataFrame:
    work = rows[rows["asset_class"].astype(str).str.upper().eq("ACTION")].copy()
    if "risk_sector" not in work.columns:
        return pd.DataFrame()
    work = work[work["risk_sector"].astype(str).str.strip().ne("")]
    if work.empty:
        return pd.DataFrame()
    grouped = work.groupby("risk_sector", dropna=False).agg(
        n_actions=("isin", "nunique"),
        sector_beta_63d=("risk_beta_63d", "median"),
        sector_beta_126d=("risk_beta_126d", "median"),
        sector_beta_252d=("risk_beta_252d", "median"),
        sector_downside_beta_252d=("risk_downside_beta_252d", "median"),
        sector_stress_correlation=("risk_stress_correlation_252d", "median"),
    ).reset_index().rename(columns={"risk_sector": "sector"})
    grouped["sector_beta_acceleration"] = pd.to_numeric(grouped["sector_beta_63d"], errors="coerce") - pd.to_numeric(
        grouped["sector_beta_252d"], errors="coerce"
    )
    rotation_path = root / "outputs" / "V21_3_SECTOR_ROTATION.csv"
    if rotation_path.exists():
        rotation = pd.read_csv(rotation_path, sep=";", encoding="utf-8-sig", low_memory=False)
        rotation_fields = ("sector", "sector_rotation_score", "recovery_gate", "momentum_acceleration")
        keep = [field for field in rotation_fields if field in rotation.columns]
        if "sector" in keep:
            grouped = grouped.merge(rotation[keep].drop_duplicates("sector"), on="sector", how="left", sort=False)
    acceleration = pd.to_numeric(grouped["sector_beta_acceleration"], errors="coerce")
    downside = pd.to_numeric(grouped["sector_downside_beta_252d"], errors="coerce")
    grouped["sector_correction_risk"] = "GREEN"
    grouped.loc[(acceleration > 0.15) | (downside > 1.20), "sector_correction_risk"] = "AMBER"
    grouped.loc[(acceleration > 0.25) & (downside > 1.30), "sector_correction_risk"] = "ORANGE"
    grouped.loc[(acceleration > 0.35) & (downside > 1.50), "sector_correction_risk"] = "RED"
    return grouped
