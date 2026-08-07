from __future__ import annotations
from datetime import date, datetime
from math import log10
from typing import Iterable

EVIDENCE_WEIGHT = {"A": 1.00, "B": 0.88, "C": 0.68, "D": 0.0}


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def recency_factor(event_date: str, as_of: str, bands: list[dict]) -> float:
    d0 = datetime.fromisoformat(event_date[:10]).date()
    d1 = datetime.fromisoformat(as_of[:10]).date()
    days = max(0, (d1 - d0).days)
    for band in bands:
        if days <= int(band["max_days"]):
            return float(band["factor"])
    return 0.0


def insider_event_score(event: dict, as_of: str, cfg: dict,
                        market_cap: float | None = None, adv20_eur: float | None = None) -> float:
    if event.get("validation_status") not in {"VALIDATED", "ISIN_MATCHED", "AUTO_MATCH"}:
        return 0.0
    direction = int(event.get("direction", 0) or 0)
    if direction == 0:
        return 0.0
    role = (event.get("actor_role") or "OTHER_PDMR").upper()
    role_factor = float(cfg["insiders"]["role_factor"].get(role, cfg["insiders"]["role_factor"]["OTHER_PDMR"]))
    pub_date = event.get("publication_date") or event.get("transaction_date")
    recency = recency_factor(pub_date, as_of, cfg["insiders"]["recency_bands"])
    value = float(event.get("value_eur") or 0.0)
    size = _size_factor(value, market_cap, adv20_eur, cfg)
    evidence = EVIDENCE_WEIGHT.get(event.get("evidence_level", "D"), 0.0)
    # Buys are deliberately more informative than discretionary sales.
    asym = 1.0 if direction > 0 else float(cfg["insiders"].get("sell_asymmetry", 0.72))
    return direction * role_factor * recency * size * evidence * asym


def insider_score(events: Iterable[dict], as_of: str, cfg: dict,
                  market_cap: float | None = None, adv20_eur: float | None = None) -> tuple[float, dict]:
    valid = [e for e in events if e.get("event_type") == "INSIDER" and e.get("evidence_level") != "D"]
    raw = sum(insider_event_score(e, as_of, cfg, market_cap, adv20_eur) for e in valid)
    window = int(cfg["insiders"]["cluster_window_days"])
    cutoff = datetime.fromisoformat(as_of[:10]).date().toordinal() - window
    recent_buys = [e for e in valid if int(e.get("direction", 0) or 0) > 0 and
                   datetime.fromisoformat((e.get("publication_date") or e.get("transaction_date"))[:10]).date().toordinal() >= cutoff]
    buyers = {str(e.get("actor_name") or "").strip().upper() for e in recent_buys if e.get("actor_name")}
    cluster = len(buyers) >= int(cfg["insiders"]["cluster_min_distinct_buyers"])
    if cluster:
        raw += min(float(cfg["insiders"]["cluster_bonus_max"]),
                   float(cfg["insiders"]["cluster_bonus_per_extra_buyer"]) * (len(buyers) - 1))
    cap = float(cfg["caps"]["insider"])
    return round(clamp(raw, -cap, cap), 4), {"cluster_flag": cluster, "distinct_buyers": len(buyers)}


def significant_holder_score(events: Iterable[dict], cfg: dict) -> float:
    score = 0.0
    for e in events:
        if e.get("event_type") != "THRESHOLD" or e.get("evidence_level") == "D":
            continue
        direction = int(e.get("direction", 0) or 0)
        threshold = float(e.get("threshold_pct") or 0.0)
        weight = 0.0
        for band in cfg["thresholds"]["bands"]:
            if threshold >= float(band["threshold_pct"]):
                weight = float(band["weight"])
        score += direction * weight * EVIDENCE_WEIGHT.get(e.get("evidence_level", "D"), 0.0)
    cap = float(cfg["caps"]["significant_holder"])
    return round(clamp(score, -cap, cap), 4)


def short_score(records: Iterable[dict], cfg: dict) -> tuple[float, dict]:
    """Use only public observations. A last observation below 0.5% is censored, not zero."""
    rows = sorted(records, key=lambda r: r.get("position_date") or r.get("publication_date") or "")
    if not rows:
        return 0.0, {"censored": False, "current_public_pct": None, "delta": None}
    by_holder: dict[str, list[dict]] = {}
    for r in rows:
        by_holder.setdefault(str(r.get("holder") or r.get("actor_name") or "UNKNOWN"), []).append(r)
    current = 0.0
    previous = 0.0
    censored = False
    for holder_rows in by_holder.values():
        last = holder_rows[-1]
        last_pct = float(last.get("short_position_pct") or 0.0)
        current += last_pct
        if last_pct < float(cfg["shorts"]["public_threshold_pct"]):
            censored = True
        if len(holder_rows) >= 2:
            previous += float(holder_rows[-2].get("short_position_pct") or 0.0)
        else:
            previous += last_pct
    delta = current - previous
    # Increasing short exposure is negative; covering is positive.
    sensitivity = float(cfg["shorts"]["delta_sensitivity"])
    raw = -delta * sensitivity
    if censored:
        raw *= float(cfg["shorts"]["censored_confidence_multiplier"])
    cap = float(cfg["caps"]["short"])
    return round(clamp(raw, -cap, cap), 4), {
        "censored": censored,
        "current_public_pct": round(current, 4),
        "previous_public_pct": round(previous, 4),
        "delta": round(delta, 4),
    }


def confidence_factor(events: Iterable[dict], completeness: float, cfg: dict) -> float:
    ev = [e for e in events if e.get("evidence_level") in EVIDENCE_WEIGHT]
    if ev:
        evidence = sum(EVIDENCE_WEIGHT[e.get("evidence_level", "D")] for e in ev) / len(ev)
    else:
        evidence = 0.0
    factor = evidence * clamp(completeness, 0.0, 1.0)
    return round(clamp(factor, float(cfg["confidence"]["floor"]), 1.0), 4)


def wis(insider: float, holder: float, short: float, tape: float,
        confidence: float, cfg: dict) -> tuple[float, float]:
    raw_cap = float(cfg["caps"]["wis"])
    raw = clamp(insider + holder + short + tape, -raw_cap, raw_cap)
    effective = raw * confidence
    if confidence < float(cfg["confidence"]["low_threshold"]):
        low_cap = float(cfg["confidence"]["low_confidence_effective_cap"])
        effective = clamp(effective, -low_cap, low_cap)
    return round(raw, 4), round(effective, 4)


def ifs(flow_core: float, persistence: float, tape: float,
        confidence: float, cfg: dict) -> tuple[float, float]:
    raw_cap = float(cfg["caps"]["ifs"])
    raw = clamp(flow_core + persistence + tape, -raw_cap, raw_cap)
    effective = raw * confidence
    if confidence < float(cfg["confidence"]["low_threshold"]):
        low_cap = min(raw_cap, float(cfg["confidence"]["low_confidence_effective_cap"]))
        effective = clamp(effective, -low_cap, low_cap)
    return round(raw, 4), round(effective, 4)


def _size_factor(value_eur: float, market_cap: float | None, adv20_eur: float | None, cfg: dict) -> float:
    if value_eur <= 0:
        return float(cfg["insiders"]["size_floor"])
    # Base factor grows slowly with absolute value, then receives relative-size confirmation.
    base = clamp(0.45 + 0.20 * max(0.0, log10(max(value_eur, 1.0)) - 4.0), 0.45, 1.20)
    rel = 0.0
    if market_cap and market_cap > 0:
        rel = max(rel, value_eur / market_cap)
    if adv20_eur and adv20_eur > 0:
        rel = max(rel, value_eur / adv20_eur)
    if rel >= 0.05:
        base += 0.20
    elif rel >= 0.01:
        base += 0.10
    return clamp(base, float(cfg["insiders"]["size_floor"]), float(cfg["insiders"]["size_cap"]))
