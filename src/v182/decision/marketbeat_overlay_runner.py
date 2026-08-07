from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
from typing import Any

import pandas as pd

from v182.decision.analyst_momentum import _committee_selection
from v182.decision import marketbeat_overlay as core
from v182.io.frames import apply_observations, load_master, save_master
from v182.reporting.exports import export_master_excel
from v182.sources.marketbeat_parse import collect_selective_forecasts

ROOT = Path(__file__).resolve().parents[3]


def _resolved_isins(mapping_path: Path) -> set[str]:
    if not mapping_path.exists() or mapping_path.stat().st_size == 0:
        return set()
    try:
        mapping = pd.read_csv(mapping_path, sep=";", encoding="utf-8-sig", dtype=str)
    except Exception:
        return set()
    required = {"isin", "status"}
    if not required.issubset(mapping.columns):
        return set()
    resolved = mapping[mapping["status"].astype(str).str.upper() == "RESOLVED"]
    return set(resolved["isin"].dropna().astype(str))


def _select_marketbeat_rows(
    actions: pd.DataFrame,
    max_issuers: int,
    *,
    mapping_path: Path,
    candidate_pool: int = 100,
) -> list[dict]:
    """Prioritize validated MarketBeat mappings inside the Committee pool.

    Scarce Parse credits are spent first on identities that have already passed
    the strict local-listing witness rule. Remaining slots are discovery names
    ranked by Committee priority, score and analyst depth. The candidate pool
    never promotes a cached issuer that is outside the Committee shortlist.
    """
    shortlist, _ = _committee_selection(actions, limit=300)
    if shortlist.empty or max_issuers <= 0:
        return []

    ranked = shortlist.copy()
    required = [column for column in ["isin", "name", "yahoo_ticker"] if column in ranked.columns]
    if len(required) < 3:
        return []
    ranked = ranked.dropna(subset=["isin", "name", "yahoo_ticker"])
    if ranked.empty:
        return []

    ranked["_review"] = (
        ranked["committee_review_required"].astype(str).str.upper().isin({"TRUE", "1", "YES"})
        if "committee_review_required" in ranked.columns
        else False
    )
    ranked["_score"] = (
        pd.to_numeric(ranked["score_brut"], errors="coerce").fillna(-1.0)
        if "score_brut" in ranked.columns
        else -1.0
    )
    analysts = pd.Series(float("nan"), index=ranked.index, dtype=float)
    if "n_analysts" in ranked.columns:
        analysts = pd.to_numeric(ranked["n_analysts"], errors="coerce")
    if "n_analysts_yf" in ranked.columns:
        analysts = analysts.fillna(pd.to_numeric(ranked["n_analysts_yf"], errors="coerce"))
    ranked["_analysts"] = analysts.fillna(-1.0)
    ranked = ranked.sort_values(
        ["_review", "_score", "_analysts"],
        ascending=[False, False, False],
        kind="stable",
    ).head(max(1, int(candidate_pool)))

    resolved_isins = _resolved_isins(mapping_path)
    cached = ranked[ranked["isin"].astype(str).isin(resolved_isins)]
    discovery = ranked[~ranked["isin"].astype(str).isin(resolved_isins)]
    ordered = pd.concat([cached, discovery], ignore_index=True).head(max_issuers)
    return ordered[required].to_dict("records")


def apply_marketbeat_overlay(root: Path | None = None) -> dict:
    root = root or ROOT
    outputs = root / "outputs"
    config_dir = root / "config"
    actions_path = outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    metrics_path = outputs / "audit" / "V18.2_ANALYST_MOMENTUM_METRICS.json"
    cfg_path = config_dir / "V18.2_CONSENSUS_PIPELINE.json"
    mapping_path = config_dir / "V18.2_MARKETBEAT_SYMBOL_MAP.csv"

    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    analyst_cfg = cfg.get("committee", {}).get("analyst_momentum", {})
    mb_cfg = cfg.get("marketbeat_parse", {})
    enabled = bool(mb_cfg.get("enabled", False))
    api_key = os.environ.get(
        str(mb_cfg.get("api_key_environment") or "MARKETBEAT_API_KEY"), ""
    ).strip()

    actions = load_master(actions_path).astype(object)
    existing_metrics = (
        json.loads(metrics_path.read_text(encoding="utf-8"))
        if metrics_path.exists()
        else {}
    )
    mb_metrics: dict[str, Any] = {
        "enabled": enabled,
        "key_present": bool(api_key),
        "success": False,
        "skipped": None,
    }

    if enabled and api_key:
        max_issuers = max(0, int(mb_cfg.get("max_issuers_per_run", 3) or 0))
        candidate_pool = max(
            max_issuers,
            int(mb_cfg.get("candidate_pool", 100) or 100),
        )
        securities = _select_marketbeat_rows(
            actions,
            max_issuers,
            mapping_path=mapping_path,
            candidate_pool=candidate_pool,
        )
        records, failures, runtime = collect_selective_forecasts(
            securities,
            api_key,
            mapping_path=mapping_path,
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
            "candidate_pool": candidate_pool,
            "selected_isins": [str(item.get("isin") or "") for item in securities],
            "failure_reasons": [item.get("reason") for item in failures[:10]],
        }
    elif not enabled:
        mb_metrics["skipped"] = "DISABLED"
    else:
        mb_metrics["skipped"] = "MISSING_KEY"

    missing_overlay = [field for field in core.OVERLAY_FIELDS if field not in actions.columns]
    if missing_overlay:
        additions = pd.DataFrame(
            {field: pd.Series([None] * len(actions), dtype=object) for field in missing_overlay},
            index=actions.index,
        )
        actions = pd.concat([actions, additions], axis=1)
    for field in core.OVERLAY_FIELDS:
        actions[field] = actions[field].astype(object)

    overlay_rows = divergence_rows = reviews = 0
    overall_weight = max(
        0.0,
        min(1.0, float(analyst_cfg.get("overall_weight", 0.15))),
    )

    for idx, row in actions.iterrows():
        mb_revision = core._first_number(row, ["mb_target_change_1m_pct"])
        if mb_revision is None:
            mb_3m = core._first_number(row, ["mb_target_change_3m_pct"])
            mb_revision = mb_3m / 3.0 if mb_3m is not None else None

        mb_consensus = core._first_number(row, ["mb_consensus_delta_1m"])
        if mb_consensus is None:
            mb_3m_consensus = core._first_number(row, ["mb_consensus_delta_3m"])
            mb_consensus = mb_3m_consensus / 3.0 if mb_3m_consensus is not None else None
        if mb_revision is None and mb_consensus is None:
            continue

        local_revision = core._first_number(
            row, ["target_change_1m_pct", "target_change_run_pct"]
        )
        if local_revision is None:
            local_3m = core._first_number(row, ["target_change_3m_pct"])
            local_revision = local_3m / 3.0 if local_3m is not None else None
        local_consensus = core._first_number(
            row, ["consensus_delta_1m", "consensus_delta_run"]
        )

        revision_signal = core._weighted_signal(local_revision, mb_revision)
        consensus_signal = core._weighted_signal(local_consensus, mb_consensus)
        state = core._confirmation_state(local_revision, mb_revision)
        divergence_rows += int(state == "DIVERGENCE")

        existing_confidence = core._num(row.get("consensus_confidence"))
        confidence = core._marketbeat_confidence(row, existing_confidence, state)
        score = core._recompute_score(
            row,
            analyst_cfg,
            revision_signal,
            consensus_signal,
            confidence,
        )
        signal, gate, review, worst = core._final_gate(
            row,
            analyst_cfg,
            score,
            revision_signal,
            consensus_signal,
        )

        previous_score = core._num(row.get("analyst_momentum_score"))
        if core._num(row.get("analyst_momentum_score_pre_marketbeat")) is None:
            actions.at[idx, "analyst_momentum_score_pre_marketbeat"] = previous_score
        actions.at[idx, "target_revision_signal_pct"] = revision_signal
        actions.at[idx, "consensus_change_signal"] = consensus_signal
        actions.at[idx, "marketbeat_confirmation_state"] = state
        actions.at[idx, "marketbeat_risk_revision_pct"] = worst
        actions.at[idx, "consensus_confidence"] = confidence

        source_count = int(core._num(row.get("consensus_source_count")) or 0.0)
        has_local_source = any(
            value is not None
            for value in (
                local_revision,
                local_consensus,
                core._num(row.get("target_price")),
                core._num(row.get("consensus_score_100")),
            )
        )
        expected_count = 2 if has_local_source else 1
        actions.at[idx, "consensus_source_count"] = max(source_count, expected_count)

        actions.at[idx, "analyst_momentum_score"] = score
        actions.at[idx, "committee_analyst_signal"] = signal
        actions.at[idx, "committee_analyst_gate"] = gate
        actions.at[idx, "committee_review_required"] = review
        base_score = core._num(row.get("score_brut"))
        if base_score is not None:
            actions.at[idx, "committee_score_with_analyst_momentum"] = round(
                base_score * (1.0 - overall_weight) + score * overall_weight,
                2,
            )
        overlay_rows += 1
        reviews += int(review)

    save_master(actions, actions_path)
    export_master_excel(
        actions,
        outputs / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
        "V18.2 Actions PEA actualisées",
    )

    shortlist, selection_basis = _committee_selection(actions, limit=300)
    committee_fields = [
        field
        for field in [
            "isin", "name", "yahoo_ticker", "comite_status", "score_brut",
            "committee_score_with_analyst_momentum", "analyst_momentum_score",
            "analyst_momentum_score_pre_marketbeat", "committee_analyst_signal",
            "committee_analyst_gate", "committee_review_required",
            "target_price", "last_close", "target_upside_abs", "target_upside_pct",
            "target_change_run_abs", "target_change_run_pct", "target_change_1m_abs",
            "target_change_1m_pct", "target_change_3m_abs", "target_change_3m_pct",
            "target_change_12m_abs", "target_change_12m_pct",
            "target_revision_signal_pct", "consensus_rating", "consensus_score_100",
            "consensus_delta_run", "consensus_delta_1m", "consensus_delta_3m",
            "consensus_change_signal", "revision_breadth_30d",
            "weighted_target_revision_30d_pct", "n_analysts",
            "consensus_source_count", "consensus_confidence", "consensus_as_of",
            "marketbeat_confirmation_state", "marketbeat_risk_revision_pct",
            *core.MARKETBEAT_DISPLAY_FIELDS,
        ]
        if field in shortlist.columns
    ]
    shortlist[committee_fields].to_csv(
        outputs / "V18.2_COMMITTEE_ANALYST_MOMENTUM.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
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
    metrics_path.write_text(
        json.dumps(existing_metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
