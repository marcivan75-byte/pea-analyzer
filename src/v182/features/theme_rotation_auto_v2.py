from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from v182.features.sector_rotation_v2_final import build_sector_rotation_v2


ACTIVE_RULE_STATUS = "DIRECT_INDUSTRY"
MIN_DIRECT_CONFIDENCE = 80.0


def load_auto_theme_rules(path: str | Path) -> pd.DataFrame:
    rules = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)
    required = {"theme_id", "field", "pattern", "match_type", "confidence_pct", "status"}
    missing = required - set(rules.columns)
    if missing:
        raise ValueError(f"MISSING_AUTO_THEME_RULE_COLUMNS:{sorted(missing)}")
    rules["confidence_pct"] = pd.to_numeric(rules["confidence_pct"], errors="coerce")
    return rules


def _match(series: pd.Series, pattern: str, match_type: str) -> pd.Series:
    text = series.fillna("").astype(str)
    if match_type == "exact":
        return text.str.casefold().eq(str(pattern).casefold())
    if match_type == "contains":
        return text.str.contains(str(pattern), case=False, regex=False, na=False)
    if match_type == "regex":
        return text.str.contains(str(pattern), case=False, regex=True, na=False)
    raise ValueError(f"UNSUPPORTED_MATCH_TYPE:{match_type}")


def build_direct_theme_tags(
    actions: pd.DataFrame,
    rules: pd.DataFrame,
    *,
    minimum_confidence: float = MIN_DIRECT_CONFIDENCE,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Create direct theme tags only when a governed source field supports them.

    This is classification, not a revenue-exposure estimate. It deliberately does
    not infer AI/data-center/grid/cyber exposure from broad technology labels.
    """
    if actions.empty:
        return pd.DataFrame(), {"status": "EMPTY_ACTIONS", "tag_count": 0, "decision_influence": 0.0}
    active = rules.loc[
        rules["status"].eq(ACTIVE_RULE_STATUS)
        & rules["confidence_pct"].ge(float(minimum_confidence))
    ].copy()
    tags: list[pd.DataFrame] = []
    for _, rule in active.iterrows():
        field = str(rule["field"])
        if field not in actions.columns:
            continue
        mask = _match(actions[field], str(rule["pattern"]), str(rule["match_type"]))
        if not mask.any():
            continue
        matched = actions.loc[mask].copy()
        matched["theme_id"] = str(rule["theme_id"])
        matched["theme_mapping_confidence_pct"] = float(rule["confidence_pct"])
        matched["theme_mapping_field"] = field
        matched["theme_mapping_pattern"] = str(rule["pattern"])
        matched["theme_mapping_status"] = ACTIVE_RULE_STATUS
        tags.append(matched)
    if not tags:
        return pd.DataFrame(), {
            "status": "NO_DIRECT_THEME_MATCHES",
            "active_rule_count": int(len(active)),
            "tag_count": 0,
            "decision_influence": 0.0,
        }
    expanded = pd.concat(tags, ignore_index=True, sort=False)
    identity = "isin" if "isin" in expanded.columns else "ticker"
    if identity in expanded.columns:
        expanded = expanded.drop_duplicates([identity, "theme_id"], keep="first")
    summary = {
        "status": "OK",
        "active_rule_count": int(len(active)),
        "disabled_low_confidence_rules": int((rules["status"] != ACTIVE_RULE_STATUS).sum()),
        "tag_count": int(len(expanded)),
        "unique_instruments": int(expanded[identity].nunique()) if identity in expanded.columns else None,
        "theme_count": int(expanded["theme_id"].nunique()),
        "decision_influence": 0.0,
    }
    return expanded, summary


def build_theme_rotation_shadow(
    actions: pd.DataFrame,
    rules: pd.DataFrame,
    config: dict[str, Any],
    *,
    history: pd.DataFrame | None = None,
    as_of: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    """Aggregate direct-industry tags through the final V2 sector scoring engine."""
    tagged, tag_summary = build_direct_theme_tags(actions, rules)
    if tagged.empty:
        return pd.DataFrame(), {**tag_summary, "theme_scoring_status": "NOT_RUN"}, tagged

    scored_input = tagged.copy()
    # Reuse the governed V2 scoring engine, but make theme_id the grouping key.
    # Original Yahoo sector/industry fields remain available for audit in the tag output.
    scored_input["sector_yf"] = scored_input["theme_id"].astype(str)
    result = build_sector_rotation_v2(scored_input, config, history=history, as_of=as_of)
    themes = result.sectors.rename(columns={"sector": "theme_id"}).copy()
    themes["mapping_mode"] = "DIRECT_INDUSTRY_ONLY"
    themes["decision_influence"] = 0.0
    summary = {
        **tag_summary,
        "theme_scoring_status": result.diagnostic.get("status"),
        "scored_theme_count": int(len(themes)),
        "promising_but_overvalued_themes": themes.loc[
            themes["warnings"].apply(lambda value: "PROMISING_BUT_OVERVALUED" in value), "theme_id"
        ].tolist() if not themes.empty else [],
        "theme_priority_candidates": themes.loc[
            themes["new_position_action"].eq("PRIORITY_BUY_ZONE"), "theme_id"
        ].tolist() if not themes.empty else [],
        "theme_correction_alerts": themes.loc[
            themes["correction_alert"], "theme_id"
        ].tolist() if not themes.empty else [],
        "decision_influence": 0.0,
    }
    return themes, summary, tagged
