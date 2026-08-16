from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ACTIVE_EDGE_STATUSES = {"RESEARCH_BASELINE"}


def load_transmission_graph(path: str | Path) -> pd.DataFrame:
    graph = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    required = {"from_theme", "to_theme", "initial_strength", "lag_min_days", "lag_max_days", "status"}
    missing = required - set(graph.columns)
    if missing:
        raise ValueError(f"MISSING_TRANSMISSION_COLUMNS:{sorted(missing)}")
    graph["initial_strength"] = pd.to_numeric(graph["initial_strength"], errors="coerce")
    graph["lag_min_days"] = pd.to_numeric(graph["lag_min_days"], errors="coerce")
    graph["lag_max_days"] = pd.to_numeric(graph["lag_max_days"], errors="coerce")
    if graph["initial_strength"].dropna().lt(0).any() or graph["initial_strength"].dropna().gt(1).any():
        raise ValueError("INVALID_TRANSMISSION_STRENGTH")
    return graph


def propagate_theme_scores(
    theme_scores: pd.DataFrame,
    graph: pd.DataFrame,
    *,
    score_column: str = "RLS",
    risk_column: str = "AVCR",
    minimum_origin_score: float = 70.0,
    maximum_destination_risk: float = 65.0,
    max_depth: int = 3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Generate explainable second/third-order research candidates.

    Edges marked HYPOTHESIS_ONLY never create candidates. Propagation remains a
    shadow research output and does not modify instrument or sector scores.
    """
    required = {"theme_id", score_column, risk_column}
    missing = required - set(theme_scores.columns)
    if missing:
        raise ValueError(f"MISSING_THEME_SCORE_COLUMNS:{sorted(missing)}")
    scores = theme_scores.copy()
    scores[score_column] = pd.to_numeric(scores[score_column], errors="coerce")
    scores[risk_column] = pd.to_numeric(scores[risk_column], errors="coerce")
    score_map = scores.set_index("theme_id")[score_column].to_dict()
    risk_map = scores.set_index("theme_id")[risk_column].to_dict()
    active = graph.loc[graph["status"].isin(ACTIVE_EDGE_STATUSES)].copy()

    queue: list[tuple[str, float, int, list[str], float]] = []
    for theme, score in score_map.items():
        if pd.notna(score) and float(score) >= minimum_origin_score:
            queue.append((str(theme), float(score), 0, [str(theme)], 1.0))

    candidates: list[dict[str, Any]] = []
    visited_strength: dict[tuple[str, str], float] = {}
    while queue:
        origin, origin_score, depth, path, cumulative_strength = queue.pop(0)
        if depth >= max_depth:
            continue
        edges = active.loc[active["from_theme"].astype(str) == origin]
        for _, edge in edges.iterrows():
            destination = str(edge["to_theme"])
            if destination in path:
                continue
            edge_strength = float(edge["initial_strength"])
            propagated_strength = cumulative_strength * edge_strength
            propagated_score = 50.0 + (origin_score - 50.0) * propagated_strength
            destination_risk = risk_map.get(destination)
            risk_ok = pd.isna(destination_risk) or float(destination_risk) <= maximum_destination_risk
            key = (path[0], destination)
            if propagated_strength <= visited_strength.get(key, -1.0):
                continue
            visited_strength[key] = propagated_strength
            row = {
                "origin_theme": path[0],
                "destination_theme": destination,
                "depth": depth + 1,
                "path": " -> ".join(path + [destination]),
                "origin_RLS": origin_score,
                "destination_RLS": score_map.get(destination),
                "destination_AVCR": destination_risk,
                "edge_strength": edge_strength,
                "cumulative_strength": round(propagated_strength, 6),
                "propagated_score": round(float(np.clip(propagated_score, 0.0, 100.0)), 4),
                "lag_min_days": int(edge["lag_min_days"]) if pd.notna(edge["lag_min_days"]) else None,
                "lag_max_days": int(edge["lag_max_days"]) if pd.notna(edge["lag_max_days"]) else None,
                "risk_gate_pass": bool(risk_ok),
                "status": "SECOND_ORDER_CANDIDATE" if depth == 0 else "THIRD_ORDER_CANDIDATE",
                "decision_influence": 0.0,
            }
            candidates.append(row)
            if propagated_strength >= 0.25:
                queue.append((destination, float(propagated_score), depth + 1, path + [destination], propagated_strength))

    result = pd.DataFrame(candidates)
    if not result.empty:
        result = result.sort_values(["risk_gate_pass", "propagated_score", "cumulative_strength"], ascending=[False, False, False]).reset_index(drop=True)
    summary = {
        "status": "OK" if not result.empty else "NO_PROPAGATION_CANDIDATES",
        "origins": int(sum(pd.notna(value) and float(value) >= minimum_origin_score for value in score_map.values())),
        "candidate_paths": int(len(result)),
        "risk_gate_pass": int(result["risk_gate_pass"].sum()) if not result.empty else 0,
        "max_depth": int(max_depth),
        "hypothesis_only_edges_ignored": int((graph["status"] == "HYPOTHESIS_ONLY").sum()),
        "decision_influence": 0.0,
    }
    return result, summary
