from __future__ import annotations
from datetime import datetime
from typing import Mapping
import pandas as pd

from v183.smart_money.scoring import (
    insider_score, significant_holder_score, short_score,
    confidence_factor, wis, ifs
)
from v183.smart_money.features.tape import calculate as calc_tape, score as calc_tape_score
from v183.smart_money.features.etf_flows import score as calc_flow_score


def score_action(isin: str, base_score: float | None, events: list[dict], ohlcv: pd.DataFrame | None,
                 as_of: str, cfg: dict, source_availability: Mapping[str, bool],
                 market_cap: float | None = None, adv20_eur: float | None = None) -> dict:
    available_events = [e for e in events if e.get("isin") == isin and
                        str(e.get("publication_date") or "")[:10] <= as_of[:10] and
                        e.get("validation_status") in {"VALIDATED", "ISIN_MATCHED", "AUTO_MATCH"}]
    ins, ins_meta = insider_score(available_events, as_of, cfg, market_cap, adv20_eur)
    holder = significant_holder_score(available_events, cfg)
    shorts = [
        {"holder": e.get("actor_name"), "position_date": e.get("position_date") or e.get("transaction_date") or e.get("publication_date"),
         "publication_date": e.get("publication_date"), "short_position_pct": e.get("short_position_pct")}
        for e in available_events if e.get("event_type") == "SHORT"
    ]
    srs, short_meta = short_score(shorts, cfg)
    tape_features = calc_tape(ohlcv) if ohlcv is not None else {}
    tape = calc_tape_score(tape_features, cfg)
    completeness = _availability(source_availability, ["insiders", "thresholds", "shorts", "tape"])
    conf = confidence_factor(available_events + ([{"evidence_level": "C"}] if ohlcv is not None else []), completeness, cfg)
    raw, effective = wis(ins, holder, srs, tape, conf, cfg)
    score_shadow = None if base_score is None else max(0.0, min(100.0, float(base_score) + effective))
    return {
        "isin": isin,
        "insider_score": ins,
        "significant_holder_score": holder,
        "short_seller_score": srs,
        "whale_tape_score": tape,
        "wis_raw": raw,
        "wis_effective": effective,
        "smart_money_confidence": conf,
        "smart_money_label": _wis_label(effective),
        "insider_cluster_flag": ins_meta["cluster_flag"],
        "insider_distinct_buyers": ins_meta["distinct_buyers"],
        "public_short_censored": short_meta.get("censored", False),
        "public_short_pct": short_meta.get("current_public_pct"),
        "short_delta_public": short_meta.get("delta"),
        "volume_z20": tape_features.get("volume_z20"),
        "dollar_volume_z20": tape_features.get("dollar_volume_z20"),
        "cmf20": tape_features.get("cmf20"),
        "obv_slope10": tape_features.get("obv_slope10"),
        "ad_slope10": tape_features.get("ad_slope10"),
        "score_base": base_score,
        "score_shadow": score_shadow,
        "score_final": base_score if cfg.get("shadow_mode", True) else score_shadow,
        "smart_money_data_status": "OK" if completeness >= 0.75 else "PARTIAL",
    }


def score_etf(isin: str, base_score: float | None, flow_history: pd.DataFrame | None,
              ohlcv: pd.DataFrame | None, cfg: dict, source_availability: Mapping[str, bool]) -> dict:
    if flow_history is not None and not flow_history.empty:
        flow_core, persistence, flow_meta = calc_flow_score(flow_history, cfg)
    else:
        flow_core, persistence, flow_meta = 0.0, 0.0, {}
    tape_features = calc_tape(ohlcv) if ohlcv is not None else {}
    # ETF tape is capped separately even though the common feature scorer can reach 1.5.
    tape_common = calc_tape_score(tape_features, cfg)
    tape = max(-float(cfg["caps"]["etf_tape"]), min(float(cfg["caps"]["etf_tape"]), tape_common))
    completeness = _availability(source_availability, ["flows", "tape"])
    evidence_events = []
    if flow_history is not None and not flow_history.empty:
        evidence_events.append({"evidence_level": "A"})
    if ohlcv is not None:
        evidence_events.append({"evidence_level": "C"})
    conf = confidence_factor(evidence_events, completeness, cfg)
    raw, effective = ifs(flow_core, persistence, tape, conf, cfg)
    score_shadow = None if base_score is None else max(0.0, min(100.0, float(base_score) + effective))
    return {
        "isin": isin,
        "flow_core_score": flow_core,
        "flow_persistence_score": persistence,
        "etf_tape_score": round(tape, 4),
        "ifs_raw": raw,
        "ifs_effective": effective,
        "smart_money_confidence": conf,
        "institutional_flow_label": _ifs_label(effective),
        **flow_meta,
        "score_base": base_score,
        "score_shadow": score_shadow,
        "score_final": base_score if cfg.get("shadow_mode", True) else score_shadow,
        "smart_money_data_status": "OK" if completeness >= 0.75 else "PARTIAL",
    }


def as_observations(universe: str, score_row: dict, as_of: str) -> list[dict]:
    skip = {"isin", "score_base", "score_final", "score_shadow"}
    evidence = "C"  # Aggregates are derived values; provenance sidecar preserves underlying A/B/C sources.
    result = []
    for field, value in score_row.items():
        if field in skip or value is None:
            continue
        result.append({
            "universe": universe, "isin": score_row["isin"], "field": field, "value": value,
            "source": "SMART_MONEY_V1_AGGREGATOR", "collected_at": datetime.utcnow().isoformat() + "Z",
            "as_of": as_of[:10], "evidence_level": evidence, "validation_status": "AUTO_MATCH",
        })
    return result


def _availability(flags: Mapping[str, bool], required: list[str]) -> float:
    return sum(1 for k in required if bool(flags.get(k, False))) / len(required)


def _wis_label(x: float) -> str:
    if x >= 5: return "SMART_MONEY_EXCEPTIONNEL"
    if x >= 3: return "FORTE_ACCUMULATION"
    if x >= 1: return "POSITIF"
    if x <= -5: return "ALERTE_SMART_MONEY"
    if x <= -3: return "FORTE_DISTRIBUTION"
    if x <= -1: return "NEGATIF"
    return "NEUTRE"


def _ifs_label(x: float) -> str:
    if x >= 4: return "ACCUMULATION_INSTITUTIONNELLE_EXCEPTIONNELLE"
    if x >= 2: return "FORTE_ENTREE"
    if x >= 0.75: return "ENTREE_MODEREE"
    if x <= -4: return "REDEPLOIEMENT_INSTITUTIONNEL_MAJEUR"
    if x <= -2: return "FORTE_SORTIE"
    if x <= -0.75: return "SORTIE_MODEREE"
    return "NEUTRE"
