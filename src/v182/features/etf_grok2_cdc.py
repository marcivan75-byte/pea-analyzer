from __future__ import annotations

from typing import Mapping
import math

import numpy as np
import pandas as pd

from v182.features.etf_grok_v2081 import _criterion_scores, score_snapshot


def _peer_group(row: pd.Series, fields: list[str]) -> str:
    parts: list[str] = []
    for field in fields:
        value = row.get(field)
        if value is not None and not pd.isna(value) and str(value).strip():
            parts.append(str(value).strip())
            break
    return parts[0] if parts else "UNCLASSIFIED"


def _adjusted_weights(base_config: Mapping, grok2_config: Mapping) -> dict[str, float]:
    multipliers = grok2_config["quantitative_core"]["group_multipliers"]
    weights: dict[str, float] = {}
    for name, spec in base_config["dynamic_criteria"].items():
        group = str(spec.get("group", ""))
        weights[name] = float(spec["backtested_weight"]) * float(multipliers.get(group, 1.0))
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("ETF_GROK2_WEIGHT_TOTAL_NOT_POSITIVE")
    return {name: weight / total for name, weight in weights.items()}


def score_grok2(
    histories: Mapping[str, pd.DataFrame],
    etf_reference: pd.DataFrame,
    base_config: Mapping,
    grok2_config: Mapping,
) -> tuple[pd.DataFrame, dict]:
    """Score GROK 2 with PIT price/volume features only.

    Static 2026 vehicle/look-through fields are deliberately not injected into the
    historical score. They are added later as an operational evidence overlay.
    """
    v1_snapshot, v1_summary = score_snapshot(histories, etf_reference, base_config)
    expected = list(base_config["dynamic_criteria"])
    core = grok2_config["quantitative_core"]
    weights = _adjusted_weights(base_config, grok2_config)

    snapshot = v1_snapshot.copy()
    complete = snapshot["criteria_complete"].astype(bool)
    snapshot["grok2_score_raw"] = np.nan
    snapshot["grok2_global_rank_pct"] = np.nan
    snapshot["grok2_peer_rank_pct"] = np.nan
    snapshot["grok2_score_final"] = np.nan
    snapshot["grok2_liquidity_pct"] = np.nan
    snapshot["grok2_peer_count"] = 0
    snapshot["grok2_peer_group"] = "UNCLASSIFIED"

    if complete.any():
        raw = snapshot.loc[complete].set_index("instrument_id")[expected].apply(pd.to_numeric, errors="coerce")
        criterion_scores = _criterion_scores(raw, base_config["dynamic_criteria"])
        raw_score = pd.Series(0.0, index=raw.index, dtype=float)
        for name, weight in weights.items():
            raw_score += pd.to_numeric(criterion_scores[name], errors="coerce") * float(weight)
        global_rank = raw_score.rank(method="average", pct=True) * 100.0

        meta = snapshot.loc[complete].set_index("instrument_id")
        fields = list(core.get("peer_group_fields", ["category", "geo_exposure"]))
        groups = meta.apply(lambda row: _peer_group(row, fields), axis=1)
        peer_count = groups.groupby(groups).transform("count").astype(int)
        peer_rank = raw_score.groupby(groups).rank(method="average", pct=True) * 100.0
        liquidity = pd.to_numeric(raw["notional_volume20"], errors="coerce").rank(method="average", pct=True) * 100.0

        final = (
            float(core["score_raw_weight"]) * raw_score
            + float(core["global_rank_weight"]) * global_rank
            + float(core["peer_rank_weight"]) * peer_rank
        )
        idx = snapshot.set_index("instrument_id").index
        for instrument_id in raw.index:
            mask = snapshot["instrument_id"] == instrument_id
            snapshot.loc[mask, "grok2_score_raw"] = float(raw_score.loc[instrument_id])
            snapshot.loc[mask, "grok2_global_rank_pct"] = float(global_rank.loc[instrument_id])
            snapshot.loc[mask, "grok2_peer_rank_pct"] = float(peer_rank.loc[instrument_id])
            snapshot.loc[mask, "grok2_score_final"] = float(final.loc[instrument_id])
            snapshot.loc[mask, "grok2_liquidity_pct"] = float(liquidity.loc[instrument_id])
            snapshot.loc[mask, "grok2_peer_count"] = int(peer_count.loc[instrument_id])
            snapshot.loc[mask, "grok2_peer_group"] = str(groups.loc[instrument_id])

    min_peer = int(core["minimum_peer_count"])
    min_liq = float(core["minimum_liquidity_percentile"])
    threshold = float(core["selection_threshold"])
    regime_allowed = snapshot["regime_allowed"].astype(bool)
    eligible = (
        complete
        & regime_allowed
        & (snapshot["grok2_peer_count"] >= min_peer)
        & (pd.to_numeric(snapshot["grok2_liquidity_pct"], errors="coerce") >= min_liq)
        & (pd.to_numeric(snapshot["grok2_score_final"], errors="coerce") >= threshold)
    )

    snapshot["grok2_decision"] = "BLOCK_DATA"
    snapshot.loc[complete, "grok2_decision"] = "REJECT_SCORE"
    snapshot.loc[complete & ~regime_allowed, "grok2_decision"] = "ABSTAIN_REGIME"
    snapshot.loc[complete & regime_allowed & (snapshot["grok2_peer_count"] < min_peer), "grok2_decision"] = "BLOCK_PEER_COUNT"
    snapshot.loc[complete & regime_allowed & (snapshot["grok2_peer_count"] >= min_peer) & (pd.to_numeric(snapshot["grok2_liquidity_pct"], errors="coerce") < min_liq), "grok2_decision"] = "BLOCK_LIQUIDITY"
    snapshot.loc[eligible, "grok2_decision"] = "ELIGIBLE"

    ranked = snapshot.loc[eligible].sort_values("grok2_score_final", ascending=False)
    selected_ids: list[str] = []
    group_counts: dict[str, int] = {}
    max_per_group = int(core.get("max_selected_per_peer_group", 1))
    for row in ranked.itertuples(index=False):
        group = str(row.grok2_peer_group)
        if group_counts.get(group, 0) >= max_per_group:
            continue
        selected_ids.append(str(row.instrument_id))
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected_ids) >= int(core["top_n"]):
            break
    snapshot["grok2_selected"] = snapshot["instrument_id"].astype(str).isin(selected_ids)
    snapshot.loc[snapshot["grok2_selected"], "grok2_decision"] = "BUY_CANDIDATE"
    snapshot = snapshot.sort_values(["grok2_selected", "grok2_score_final", "instrument_id"], ascending=[False, False, True], na_position="last").reset_index(drop=True)

    summary = {
        "version": grok2_config["version"],
        "base_model": base_config["version"],
        "as_of": v1_summary.get("as_of"),
        "universe_histories": int(len(histories)),
        "scorable_etfs": int(complete.sum()),
        "eligible_after_cdc_quant_gates": int(eligible.sum()),
        "selected": [
            {
                "isin": str(row.instrument_id),
                "score_final": float(row.grok2_score_final),
                "peer_group": str(row.grok2_peer_group),
                "peer_count": int(row.grok2_peer_count),
                "liquidity_pct": float(row.grok2_liquidity_pct),
            }
            for row in snapshot.loc[snapshot["grok2_selected"]].itertuples(index=False)
        ],
        "regime": v1_summary.get("regime", {}),
        "static_2026_fields_used_in_historical_score": False,
        "lookthrough_backfilled": False,
        "promotion_allowed": False,
        "live_orders_enabled": False,
    }
    return snapshot, summary
