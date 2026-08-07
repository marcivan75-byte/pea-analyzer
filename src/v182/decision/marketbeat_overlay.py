from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
from typing import Any

import pandas as pd

from v182.decision.analyst_momentum import _committee_selection
from v182.io.frames import apply_observations, load_master, save_master
from v182.reporting.exports import export_master_excel
from v182.sources.marketbeat_parse import collect_selective_forecasts

ROOT = Path(__file__).resolve().parents[3]

OVERLAY_FIELDS = [
    "analyst_momentum_score_pre_marketbeat",
    "target_revision_signal_pct",
    "consensus_change_signal",
    "marketbeat_confirmation_state",
    "marketbeat_risk_revision_pct",
]

MARKETBEAT_DISPLAY_FIELDS = [
    "marketbeat_ticker", "marketbeat_exchange", "marketbeat_match_type",
    "marketbeat_match_confidence", "marketbeat_as_of", "mb_data_status",
    "mb_consensus_rating", "mb_consensus_score_100", "mb_n_analysts",
    "mb_strong_buy_n", "mb_buy_n", "mb_hold_n", "mb_sell_n", "mb_strong_sell_n",
    "mb_consensus_score_1m_ago", "mb_consensus_delta_1m",
    "mb_consensus_score_3m_ago", "mb_consensus_delta_3m",
    "mb_consensus_score_12m_ago", "mb_consensus_delta_12m",
    "mb_target_price", "mb_target_currency", "mb_target_1m_ago",
    "mb_target_change_1m_abs", "mb_target_change_1m_pct",
    "mb_target_3m_ago", "mb_target_change_3m_abs", "mb_target_change_3m_pct",
    "mb_target_12m_ago", "mb_target_change_12m_abs", "mb_target_change_12m_pct",
]


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).strip().replace(",", ".").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _component(value: float | None, transform) -> float:
    return 50.0 if value is None else _clip(float(transform(value)))


def _first_number(row: pd.Series, fields: list[str]) -> float | None:
    for field in fields:
        if field in row.index:
            value = _num(row.get(field))
            if value is not None:
                return value
    return None


def _weighted_signal(local: float | None, marketbeat: float | None) -> float | None:
    if local is None:
        return marketbeat
    if marketbeat is None:
        return local
    return round(local * 0.70 + marketbeat * 0.30, 4)


def _confirmation_state(local: float | None, marketbeat: float | None) -> str:
    if marketbeat is None:
        return "NO_MARKETBEAT_DATA"
    if local is None:
        return "MARKETBEAT_ONLY"
    if abs(local) < 2.0 and abs(marketbeat) < 2.0:
        return "BOTH_STABLE"
    if local >= 2.0 and marketbeat >= 2.0:
        return "CONFIRM_POSITIVE"
    if local <= -2.0 and marketbeat <= -2.0:
        return "CONFIRM_NEGATIVE"
    if (local >= 2.0 and marketbeat <= -2.0) or (local <= -2.0 and marketbeat >= 2.0):
        return "DIVERGENCE"
    return "MIXED_OR_WEAK"


def _marketbeat_confidence(row: pd.Series, existing: float | None, state: str) -> float:
    analysts = _num(row.get("mb_n_analysts"))
    if existing is None:
        score = 45.0 + (min(20.0, max(0.0, analysts) / 20.0 * 20.0) if analysts is not None else 0.0)
        score += 10.0
    else:
        score = existing
        if state in {"CONFIRM_POSITIVE", "CONFIRM_NEGATIVE", "BOTH_STABLE", "MIXED_OR_WEAK"}:
            score += 7.0
        elif state == "DIVERGENCE":
            score -= 10.0
    return round(_clip(score), 2)


def _recompute_score(row: pd.Series, cfg: dict, revision_signal: float | None, consensus_signal: float | None, confidence: float | None) -> float:
    weights = cfg.get("weights", {})
    default_weights = {
        "target_revision": 0.35,
        "consensus_change": 0.20,
        "target_upside": 0.15,
        "revision_breadth": 0.15,
        "broker_quality": 0.10,
        "confidence": 0.05,
    }
    merged = {**default_weights, **weights}
    components = {
        "target_revision": _component(revision_signal, lambda x: 50.0 + x * 5.0),
        "consensus_change": _component(consensus_signal, lambda x: 50.0 + x * 2.0),
        "target_upside": _component(_num(row.get("target_upside_pct")), lambda x: 50.0 + x * 2.0),
        "revision_breadth": _component(_num(row.get("revision_breadth_30d")), lambda x: 50.0 + x / 2.0),
        "broker_quality": _component(_num(row.get("weighted_target_revision_30d_pct")), lambda x: 50.0 + x * 5.0),
        "confidence": 50.0 if confidence is None else _clip(confidence),
    }
    total = sum(max(0.0, float(merged.get(key, 0.0))) for key in components)
    if total <= 0:
        return 50.0
    return round(_clip(sum(components[k] * max(0.0, float(merged.get(k, 0.0))) for k in components) / total), 2)


def _final_gate(
    row: pd.Series,
    analyst_cfg: dict,
    score: float,
    revision_signal: float | None,
    consensus_signal: float | None,
) -> tuple[str, str, bool, float | None]:
    thresholds = analyst_cfg.get("thresholds", {})
    strong_pos = float(thresholds.get("target_revision_strong_positive_pct", 5.0))
    pos = float(thresholds.get("target_revision_positive_pct", 2.0))
    neg = float(thresholds.get("target_revision_negative_pct", -2.0))
    strong_neg = float(thresholds.get("target_revision_strong_negative_pct", -5.0))
    mandatory = float(thresholds.get("mandatory_review_target_cut_pct", -10.0))

    risk_candidates = [
        _num(row.get("target_change_run_pct")),
        _num(row.get("target_change_1m_pct")),
        _num(row.get("mb_target_change_1m_pct")),
    ]
    risk_candidates = [value for value in risk_candidates if value is not None]
    worst = min(risk_candidates) if risk_candidates else None
    upside = _num(row.get("target_upside_pct"))
    breadth = _num(row.get("revision_breadth_30d"))

    if worst is not None and (worst <= mandatory or (worst <= strong_neg and upside is not None and upside >= 15.0)):
        return "STRONG_NEGATIVE", "BLOCK_NEW_BUY_REVIEW", True, worst
    if worst is not None and worst <= strong_neg:
        return "STRONG_NEGATIVE", "PENALIZE_STRONG", False, worst
    if worst is not None and worst <= neg:
        return "NEGATIVE", "PENALIZE", False, worst

    corroborated = (consensus_signal is not None and consensus_signal > 0) or (breadth is not None and breadth > 0)
    no_material_negative = worst is None or worst > neg
    if revision_signal is not None and revision_signal >= strong_pos and corroborated and score >= 65.0 and no_material_negative:
        return "STRONG_POSITIVE", "BOOST", False, worst
    if no_material_negative and (score >= 60.0 or (revision_signal is not None and revision_signal >= pos)):
        return "POSITIVE", "SUPPORT", False, worst
    if score < 40.0:
        return "NEGATIVE", "PENALIZE", False, worst
    return "NEUTRAL", "NEUTRAL", False, worst


def _select_marketbeat_rows(actions: pd.DataFrame, max_issuers: int) -> list[dict]:
    shortlist, _ = _committee_selection(actions, limit=300)
    if shortlist.empty:
        return []
    ranked = shortlist.copy()
    if "score_brut" in ranked.columns:
        ranked["_mb_score"] = pd.to_numeric(ranked["score_brut"], errors="coerce")
        ranked = ranked.sort_values("_mb_score", ascending=False).drop(columns=["_mb_score"])
    required = [column for column in ["isin", "name", "yahoo_ticker"] if column in ranked.columns]
    if len(required) < 3:
        return []
    ranked = ranked.dropna(subset=["isin", "name", "yahoo_ticker"])
    return ranked[required].head(max_issuers).to_dict("records")


def apply_marketbeat_overlay(root: Path | None = None) -> dict:
    root = root or ROOT
    outputs = root / "outputs"
    config_dir = root / "config"
    actions_path = outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    metrics_path = outputs / "audit" / "V18.2_ANALYST_MOMENTUM_METRICS.json"
    cfg_path = config_dir / "V18.2_CONSENSUS_PIPELINE.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    analyst_cfg = cfg.get("committee", {}).get("analyst_momentum", {})
    mb_cfg = cfg.get("marketbeat_parse", {})
    enabled = bool(mb_cfg.get("enabled", False))
    api_key = os.environ.get(str(mb_cfg.get("api_key_environment") or "MARKETBEAT_API_KEY"), "").strip()

    actions = load_master(actions_path).astype(object)
    existing_metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    mb_metrics: dict[str, Any] = {
        "enabled": enabled,
        "key_present": bool(api_key),
        "success": False,
        "skipped": None,
    }

    if enabled and api_key:
        max_issuers = max(0, int(mb_cfg.get("max_issuers_per_run", 3) or 0))
        securities = _select_marketbeat_rows(actions, max_issuers)
        records, failures, runtime = collect_selective_forecasts(
            securities,
            api_key,
            mapping_path=config_dir / "V18.2_MARKETBEAT_SYMBOL_MAP.csv",
            max_issuers=max_issuers,
            mapping_ttl_days=int(mb_cfg.get("mapping_ttl_days", 90) or 90),
            unresolved_ttl_days=int(mb_cfg.get("unresolved_ttl_days", 7) or 7),
            min_match_score=float(mb_cfg.get("min_match_score", 0.88) or 0.88),
            min_interval_seconds=float(mb_cfg.get("min_interval_seconds", 12.2) or 12.2),
        )
        now = datetime.now(timezone.utc)
        observations: list[dict] = []
        for record in records:
            for field, value in record.get("fields", {}).items():
                if value is None:
                    continue
                observations.append({
                    "universe": "ACTION",
                    "isin": record["isin"],
                    "field": field,
                    "value": value,
                    "source": "MarketBeat via Parse",
                    "collected_at": now.isoformat(),
                    "as_of": now.date().isoformat(),
                    "evidence_level": "C",
                    "validation_status": "AUTO_MATCH_ISSUER_PROXY",
                })
        actions, quarantine = apply_observations(actions, observations)
        mb_metrics = {
            **runtime,
            "enabled": True,
            "key_present": True,
            "observations": len(observations),
            "quarantined": len(quarantine),
            "failure_reasons": [item.get("reason") for item in failures[:10]],
        }
    elif not enabled:
        mb_metrics["skipped"] = "DISABLED"
    else:
        mb_metrics["skipped"] = "MISSING_KEY"

    for field in OVERLAY_FIELDS:
        if field not in actions.columns:
            actions[field] = None
        else:
            actions[field] = actions[field].astype(object)

    overlay_rows = divergence_rows = reviews = 0
    overall_weight = max(0.0, min(1.0, float(analyst_cfg.get("overall_weight", 0.15))))

    for idx, row in actions.iterrows():
        mb_revision = _first_number(row, ["mb_target_change_1m_pct"])
        if mb_revision is None:
            mb_3m = _first_number(row, ["mb_target_change_3m_pct"])
            mb_revision = mb_3m / 3.0 if mb_3m is not None else None
        mb_consensus = _first_number(row, ["mb_consensus_delta_1m"])
        if mb_consensus is None:
            mb_3m_consensus = _first_number(row, ["mb_consensus_delta_3m"])
            mb_consensus = mb_3m_consensus / 3.0 if mb_3m_consensus is not None else None
        if mb_revision is None and mb_consensus is None:
            continue

        local_revision = _first_number(row, ["target_change_1m_pct", "target_change_run_pct"])
        if local_revision is None:
            local_3m = _first_number(row, ["target_change_3m_pct"])
            local_revision = local_3m / 3.0 if local_3m is not None else None
        local_consensus = _first_number(row, ["consensus_delta_1m", "consensus_delta_run"])

        revision_signal = _weighted_signal(local_revision, mb_revision)
        consensus_signal = _weighted_signal(local_consensus, mb_consensus)
        state = _confirmation_state(local_revision, mb_revision)
        if state == "DIVERGENCE":
            divergence_rows += 1
        existing_confidence = _num(row.get("consensus_confidence"))
        confidence = _marketbeat_confidence(row, existing_confidence, state)
        score = _recompute_score(row, analyst_cfg, revision_signal, consensus_signal, confidence)
        signal, gate, review, worst = _final_gate(row, analyst_cfg, score, revision_signal, consensus_signal)

        previous_score = _num(row.get("analyst_momentum_score"))
        actions.at[idx, "analyst_momentum_score_pre_marketbeat"] = previous_score
        actions.at[idx, "target_revision_signal_pct"] = revision_signal
        actions.at[idx, "consensus_change_signal"] = consensus_signal
        actions.at[idx, "marketbeat_confirmation_state"] = state
        actions.at[idx, "marketbeat_risk_revision_pct"] = worst
        actions.at[idx, "consensus_confidence"] = confidence
        source_count = _num(row.get("consensus_source_count")) or 0.0
        actions.at[idx, "consensus_source_count"] = int(source_count) + 1
        actions.at[idx, "analyst_momentum_score"] = score
        actions.at[idx, "committee_analyst_signal"] = signal
        actions.at[idx, "committee_analyst_gate"] = gate
        actions.at[idx, "committee_review_required"] = review
        base_score = _num(row.get("score_brut"))
        if base_score is not None:
            actions.at[idx, "committee_score_with_analyst_momentum"] = round(base_score * (1.0 - overall_weight) + score * overall_weight, 2)
        overlay_rows += 1
        reviews += int(review)

    save_master(actions, actions_path)
    export_master_excel(actions, outputs / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx", "V18.2 Actions PEA actualisées")

    shortlist, selection_basis = _committee_selection(actions, limit=300)
    committee_fields = [
        field for field in [
            "isin", "name", "yahoo_ticker", "comite_status", "score_brut",
            "committee_score_with_analyst_momentum", "analyst_momentum_score",
            "analyst_momentum_score_pre_marketbeat", "committee_analyst_signal",
            "committee_analyst_gate", "committee_review_required",
            "target_price", "last_close", "target_upside_abs", "target_upside_pct",
            "target_change_run_abs", "target_change_run_pct", "target_change_1m_abs",
            "target_change_1m_pct", "target_change_3m_abs", "target_change_3m_pct",
            "target_revision_signal_pct", "consensus_rating", "consensus_score_100",
            "consensus_delta_run", "consensus_delta_1m", "consensus_delta_3m",
            "consensus_change_signal", "revision_breadth_30d", "weighted_target_revision_30d_pct",
            "n_analysts", "consensus_source_count", "consensus_confidence", "consensus_as_of",
            "marketbeat_confirmation_state", "marketbeat_risk_revision_pct",
            *MARKETBEAT_DISPLAY_FIELDS,
        ] if field in shortlist.columns
    ]
    shortlist[committee_fields].to_csv(
        outputs / "V18.2_COMMITTEE_ANALYST_MOMENTUM.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )

    overlay_metrics = {
        "rows": overlay_rows,
        "divergences": divergence_rows,
        "mandatory_reviews_after_overlay": reviews,
        "committee_rows": len(shortlist),
        "committee_selection_basis": selection_basis,
    }
    existing_metrics["marketbeat"] = mb_metrics
    existing_metrics["marketbeat_overlay"] = overlay_metrics
    metrics_path.write_text(json.dumps(existing_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"marketbeat": mb_metrics, "overlay": overlay_metrics}


def main() -> None:
    result = apply_marketbeat_overlay()
    mb = result["marketbeat"]
    overlay = result["overlay"]
    print(
        "WAVE_09B_MARKETBEAT — "
        f"selected={mb.get('selected', 0)} | successful={mb.get('successful', 0)} | "
        f"api_calls={mb.get('api_calls', 0)} | overlay_rows={overlay['rows']} | "
        f"divergences={overlay['divergences']} | reviews={overlay['mandatory_reviews_after_overlay']}"
    )


if __name__ == "__main__":
    main()
