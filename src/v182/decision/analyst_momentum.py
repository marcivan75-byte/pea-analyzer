from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]

MOMENTUM_FIELDS = [
    "target_price",
    "target_prev_run",
    "target_change_run_abs",
    "target_change_run_pct",
    "target_1m_ago",
    "target_change_1m_abs",
    "target_change_1m_pct",
    "target_3m_ago",
    "target_change_3m_abs",
    "target_change_3m_pct",
    "target_12m_ago",
    "target_change_12m_pct",
    "target_upside_abs",
    "target_upside_pct",
    "target_low",
    "target_high",
    "target_dispersion_pct",
    "consensus_score_100",
    "consensus_prev_run",
    "consensus_delta_run",
    "consensus_score_1m_ago",
    "consensus_delta_1m",
    "consensus_score_3m_ago",
    "consensus_delta_3m",
    "upgrades_30d",
    "downgrades_30d",
    "net_upgrades_30d",
    "target_raises_30d",
    "target_cuts_30d",
    "net_target_revisions_30d",
    "revision_breadth_30d",
    "n_analysts",
    "consensus_source_count",
    "consensus_confidence",
    "consensus_as_of",
    "weighted_target_revision_30d_pct",
    "weighted_consensus_delta_30d",
    "target_revision_acceleration",
    "analyst_momentum_score",
    "committee_analyst_signal",
    "committee_analyst_gate",
    "committee_review_required",
    "committee_score_with_analyst_momentum",
]

SNAPSHOT_COLUMNS = [
    "date", "isin", "source", "consensus_rating", "consensus_score_100",
    "n_analysts", "strong_buy", "buy", "hold", "sell", "strong_sell",
    "target_low", "target_mean", "target_high", "last_close",
]

REVISION_COLUMNS = [
    "date", "isin", "broker", "analyst", "old_rating", "new_rating",
    "old_target", "new_target", "change_abs", "change_pct", "currency", "source",
]

RATING_SCORE_100 = {
    "STRONG_BUY": 100.0,
    "BUY": 75.0,
    "OUTPERFORM": 75.0,
    "OVERWEIGHT": 75.0,
    "HOLD": 50.0,
    "NEUTRAL": 50.0,
    "UNDERPERFORM": 25.0,
    "UNDERWEIGHT": 25.0,
    "SELL": 25.0,
    "STRONG_SELL": 0.0,
}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text or text.upper() in {"NAN", "NONE", "NON_OBSERVE", "NA", "N/A"}:
        return None
    text = text.replace("%", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NAN", "NONE", "NON_OBSERVE", "NA", "N/A"}:
        return None
    return text


def _first_num(row: pd.Series, fields: list[str]) -> float | None:
    for field in fields:
        if field in row.index:
            value = _num(row.get(field))
            if value is not None:
                return value
    return None


def _first_text(row: pd.Series, fields: list[str]) -> str | None:
    for field in fields:
        if field in row.index:
            value = _text(row.get(field))
            if value is not None:
                return value
    return None


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100.0, 4)


def _abs_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 6)


def consensus_score_100(row: pd.Series) -> float | None:
    counts = {
        "strong_buy": _first_num(row, ["analyst_strong_buy", "strong_buy"]),
        "buy": _first_num(row, ["analyst_buy", "buy"]),
        "hold": _first_num(row, ["analyst_hold", "hold"]),
        "sell": _first_num(row, ["analyst_sell", "sell"]),
        "strong_sell": _first_num(row, ["analyst_strong_sell", "strong_sell"]),
    }
    if any(value is not None for value in counts.values()):
        clean = {key: max(0.0, value or 0.0) for key, value in counts.items()}
        total = sum(clean.values())
        if total > 0:
            weighted = (
                clean["strong_buy"] * 100.0
                + clean["buy"] * 75.0
                + clean["hold"] * 50.0
                + clean["sell"] * 25.0
            )
            return round(weighted / total, 4)

    legacy = _first_num(row, ["consensus_score"])
    if legacy is not None and 1.0 <= legacy <= 5.0:
        return round((legacy - 1.0) / 4.0 * 100.0, 4)

    recommendation_mean = _first_num(row, ["recommendation_mean_yf"])
    if recommendation_mean is not None and 1.0 <= recommendation_mean <= 5.0:
        legacy = 6.0 - recommendation_mean
        return round((legacy - 1.0) / 4.0 * 100.0, 4)

    rating = _first_text(row, ["consensus_rating", "consensus", "recommendation_key_yf"])
    if rating:
        normalized = rating.upper().replace("-", "_").replace(" ", "_")
        return RATING_SCORE_100.get(normalized)
    return None


def _canonical_source(row: pd.Series) -> str:
    explicit = _first_text(row, ["consensus_source"])
    if explicit:
        return explicit
    if _first_num(row, ["target_mean_yf"]) is not None or _first_text(row, ["recommendation_key_yf"]):
        return "yfinance"
    if any(_first_num(row, [field]) is not None for field in ["analyst_buy", "analyst_hold", "analyst_sell"]):
        return "Finnhub"
    return "UNKNOWN"


def _source_count(row: pd.Series) -> int:
    sources: set[str] = set()
    for field in [
        "consensus_source", "src_consensus", "src_consensus_score", "src_consensus_rating",
        "src_target_price", "src_target_mean_yf", "src_recommendation_mean_yf",
        "src_recommendation_key_yf",
    ]:
        value = _first_text(row, [field])
        if value:
            for token in value.replace("|", ";").replace(",", ";").split(";"):
                token = token.strip()
                if token:
                    sources.add(token.lower())
    if not sources:
        source = _canonical_source(row)
        if source != "UNKNOWN":
            sources.add(source.lower())
    return len(sources)


def _latest_observed_at(row: pd.Series) -> str | None:
    candidates: list[pd.Timestamp] = []
    raw_values: list[str] = []
    for field in [
        "observed_at_target_price", "observed_at_target_mean_yf",
        "observed_at_consensus_score", "observed_at_consensus_rating",
        "yf_consensus_as_of", "fundamentals_as_of",
    ]:
        text = _first_text(row, [field])
        if not text:
            continue
        stamp = pd.to_datetime(text, utc=True, errors="coerce")
        if pd.notna(stamp):
            candidates.append(stamp)
            raw_values.append(text)
    if not candidates:
        return None
    idx = max(range(len(candidates)), key=lambda i: candidates[i])
    return raw_values[idx]


def _target_dispersion(low: float | None, high: float | None, mean: float | None) -> float | None:
    if low is None or high is None or mean is None or mean == 0:
        return None
    return round((high - low) / abs(mean) * 100.0, 4)


def _history_asof(history: pd.DataFrame, isin: str, cutoff: pd.Timestamp, *, strict: bool = False) -> pd.Series | None:
    if history.empty or "isin" not in history.columns or "date" not in history.columns:
        return None
    subset = history[history["isin"].astype(str) == str(isin)].copy()
    if subset.empty:
        return None
    subset["_date"] = pd.to_datetime(subset["date"], utc=True, errors="coerce")
    subset = subset[subset["_date"].notna()]
    subset = subset[subset["_date"] < cutoff] if strict else subset[subset["_date"] <= cutoff]
    if subset.empty:
        return None
    return subset.sort_values("_date").iloc[-1]


def _load_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    except Exception:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    return frame


def _broker_weight_map(path: Path | None) -> dict[str, float]:
    if path is None or not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    except Exception:
        return {}
    result: dict[str, float] = {}
    if "broker" not in frame.columns or "weight" not in frame.columns:
        return result
    for _, row in frame.iterrows():
        broker = _text(row.get("broker"))
        weight = _num(row.get("weight"))
        if broker and weight is not None and weight > 0:
            result[broker.casefold()] = weight
    return result


def _revision_metrics(
    revisions: pd.DataFrame,
    isin: str,
    run_ts: pd.Timestamp,
    broker_weights: dict[str, float],
) -> dict[str, float | int | None]:
    empty = {
        "upgrades_30d": None,
        "downgrades_30d": None,
        "net_upgrades_30d": None,
        "target_raises_30d": None,
        "target_cuts_30d": None,
        "net_target_revisions_30d": None,
        "revision_breadth_30d": None,
        "weighted_target_revision_30d_pct": None,
        "weighted_consensus_delta_30d": None,
    }
    if revisions.empty or "isin" not in revisions.columns or "date" not in revisions.columns:
        return empty
    subset = revisions[revisions["isin"].astype(str) == str(isin)].copy()
    if subset.empty:
        return empty
    subset["_date"] = pd.to_datetime(subset["date"], utc=True, errors="coerce")
    cutoff = run_ts - pd.Timedelta(days=30)
    subset = subset[(subset["_date"] > cutoff) & (subset["_date"] <= run_ts)]
    if subset.empty:
        return empty

    rank = {
        "STRONG_SELL": 0, "SELL": 1, "UNDERPERFORM": 1, "UNDERWEIGHT": 1,
        "HOLD": 2, "NEUTRAL": 2,
        "BUY": 3, "OUTPERFORM": 3, "OVERWEIGHT": 3,
        "STRONG_BUY": 4,
    }
    upgrades = downgrades = raises = cuts = 0
    target_weighted_sum = target_weight_sum = 0.0
    consensus_weighted_sum = consensus_weight_sum = 0.0

    for _, row in subset.iterrows():
        broker = (_text(row.get("broker")) or "").casefold()
        weight = broker_weights.get(broker, 1.0)

        old_rating = (_text(row.get("old_rating")) or "").upper().replace("-", "_").replace(" ", "_")
        new_rating = (_text(row.get("new_rating")) or "").upper().replace("-", "_").replace(" ", "_")
        if old_rating in rank and new_rating in rank:
            delta = rank[new_rating] - rank[old_rating]
            if delta > 0:
                upgrades += 1
            elif delta < 0:
                downgrades += 1
            consensus_weighted_sum += delta * 25.0 * weight
            consensus_weight_sum += weight

        change_pct = _num(row.get("change_pct"))
        if change_pct is None:
            change_pct = _pct_change(_num(row.get("new_target")), _num(row.get("old_target")))
        if change_pct is not None:
            if change_pct > 0:
                raises += 1
            elif change_pct < 0:
                cuts += 1
            target_weighted_sum += change_pct * weight
            target_weight_sum += weight

    rating_events = upgrades + downgrades
    target_events = raises + cuts
    breadth = None
    if target_events:
        breadth = round((raises - cuts) / target_events * 100.0, 4)
    elif rating_events:
        breadth = round((upgrades - downgrades) / rating_events * 100.0, 4)

    return {
        "upgrades_30d": upgrades if rating_events else None,
        "downgrades_30d": downgrades if rating_events else None,
        "net_upgrades_30d": upgrades - downgrades if rating_events else None,
        "target_raises_30d": raises if target_events else None,
        "target_cuts_30d": cuts if target_events else None,
        "net_target_revisions_30d": raises - cuts if target_events else None,
        "revision_breadth_30d": breadth,
        "weighted_target_revision_30d_pct": (
            round(target_weighted_sum / target_weight_sum, 4) if target_weight_sum else None
        ),
        "weighted_consensus_delta_30d": (
            round(consensus_weighted_sum / consensus_weight_sum, 4) if consensus_weight_sum else None
        ),
    }


def _confidence(
    source_count: int,
    n_analysts: float | None,
    dispersion_pct: float | None,
    observed_at: str | None,
    run_ts: pd.Timestamp,
) -> float:
    score = 35.0 + min(30.0, source_count * 15.0)
    if n_analysts is not None:
        score += min(20.0, max(0.0, n_analysts) / 20.0 * 20.0)
    if observed_at:
        stamp = pd.to_datetime(observed_at, utc=True, errors="coerce")
        if pd.notna(stamp):
            age = max(0.0, (run_ts - stamp).total_seconds() / 86400.0)
            if age <= 7:
                score += 10.0
            elif age <= 30:
                score += 5.0
    if dispersion_pct is not None:
        score -= min(20.0, max(0.0, dispersion_pct - 20.0) * 0.5)
    return round(_clip(score), 2)


def _component_or_neutral(value: float | None, transform) -> float:
    if value is None:
        return 50.0
    return _clip(float(transform(value)))


def _momentum_score(values: dict[str, Any], cfg: dict) -> float:
    weights = cfg.get("weights", {})
    target_revision = values.get("target_change_1m_pct")
    if target_revision is None and values.get("target_change_3m_pct") is not None:
        target_revision = values["target_change_3m_pct"] / 3.0

    target_revision_component = _component_or_neutral(target_revision, lambda x: 50.0 + x * 5.0)
    consensus_delta = values.get("consensus_delta_1m")
    if consensus_delta is None:
        consensus_delta = values.get("consensus_delta_run")
    consensus_component = _component_or_neutral(consensus_delta, lambda x: 50.0 + x * 2.0)
    upside_component = _component_or_neutral(values.get("target_upside_pct"), lambda x: 50.0 + x * 2.0)
    breadth_component = _component_or_neutral(values.get("revision_breadth_30d"), lambda x: 50.0 + x / 2.0)
    broker_component = _component_or_neutral(
        values.get("weighted_target_revision_30d_pct"), lambda x: 50.0 + x * 5.0
    )
    confidence_component = values.get("consensus_confidence")
    confidence_component = 50.0 if confidence_component is None else _clip(float(confidence_component))

    components = {
        "target_revision": target_revision_component,
        "consensus_change": consensus_component,
        "target_upside": upside_component,
        "revision_breadth": breadth_component,
        "broker_quality": broker_component,
        "confidence": confidence_component,
    }
    default_weights = {
        "target_revision": 0.35,
        "consensus_change": 0.20,
        "target_upside": 0.15,
        "revision_breadth": 0.15,
        "broker_quality": 0.10,
        "confidence": 0.05,
    }
    merged_weights = {**default_weights, **weights}
    total_weight = sum(max(0.0, float(merged_weights.get(key, 0.0))) for key in components)
    if total_weight <= 0:
        return 50.0
    score = sum(
        components[key] * max(0.0, float(merged_weights.get(key, 0.0)))
        for key in components
    ) / total_weight
    return round(_clip(score), 2)


def _signal_and_gate(values: dict[str, Any], cfg: dict) -> tuple[str, str, bool]:
    thresholds = cfg.get("thresholds", {})
    strong_pos = float(thresholds.get("target_revision_strong_positive_pct", 5.0))
    pos = float(thresholds.get("target_revision_positive_pct", 2.0))
    neg = float(thresholds.get("target_revision_negative_pct", -2.0))
    strong_neg = float(thresholds.get("target_revision_strong_negative_pct", -5.0))
    mandatory = float(thresholds.get("mandatory_review_target_cut_pct", -10.0))

    revision = values.get("target_change_1m_pct")
    score = float(values.get("analyst_momentum_score") or 50.0)
    consensus_delta = values.get("consensus_delta_1m")
    breadth = values.get("revision_breadth_30d")
    upside = values.get("target_upside_pct")

    review = bool(
        revision is not None
        and (
            revision <= mandatory
            or (revision <= strong_neg and upside is not None and upside >= 15.0)
        )
    )
    if review:
        return "STRONG_NEGATIVE", "BLOCK_NEW_BUY_REVIEW", True

    corroborated = (
        (consensus_delta is not None and consensus_delta > 0)
        or (breadth is not None and breadth > 0)
    )
    if revision is not None and revision >= strong_pos and corroborated and score >= 65.0:
        return "STRONG_POSITIVE", "BOOST", False
    if score >= 60.0 or (revision is not None and revision >= pos):
        return "POSITIVE", "SUPPORT", False
    if revision is not None and revision <= strong_neg:
        return "STRONG_NEGATIVE", "PENALIZE_STRONG", False
    if score < 40.0 or (revision is not None and revision <= neg):
        return "NEGATIVE", "PENALIZE", False
    return "NEUTRAL", "NEUTRAL", False


def enrich_analyst_momentum(
    actions_df: pd.DataFrame,
    *,
    history: pd.DataFrame | None = None,
    revisions: pd.DataFrame | None = None,
    broker_weights: dict[str, float] | None = None,
    cfg: dict | None = None,
    run_date: str | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    cfg = cfg or {}
    analyst_cfg = cfg.get("committee", {}).get("analyst_momentum", cfg.get("analyst_momentum", cfg))
    history = history.copy() if history is not None else pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    revisions = revisions.copy() if revisions is not None else pd.DataFrame(columns=REVISION_COLUMNS)
    broker_weights = broker_weights or {}

    run_ts = pd.Timestamp(run_date) if run_date is not None else pd.Timestamp.now(tz="UTC")
    if run_ts.tzinfo is None:
        run_ts = run_ts.tz_localize("UTC")
    else:
        run_ts = run_ts.tz_convert("UTC")
    run_day = run_ts.strftime("%Y-%m-%d")

    out = actions_df.copy()
    for field in MOMENTUM_FIELDS:
        if field not in out.columns:
            out[field] = None

    snapshots: list[dict] = []
    scored = 0
    reviews = 0
    strong_positive = 0

    for idx, row in out.iterrows():
        isin = _first_text(row, ["isin"])
        if not isin:
            continue

        target = _first_num(row, ["target_price", "target_mean_yf"])
        target_low = _first_num(row, ["target_low", "target_low_yf"])
        target_high = _first_num(row, ["target_high", "target_high_yf"])
        last_close = _first_num(row, ["last_close"])
        score100 = consensus_score_100(row)
        rating = _first_text(row, ["consensus_rating", "consensus", "recommendation_key_yf"])
        if rating:
            rating = rating.upper().replace("-", "_").replace(" ", "_")
        n_analysts = _first_num(row, ["n_analysts", "n_analysts_yf"])
        source = _canonical_source(row)
        source_count = _source_count(row)
        observed_at = _latest_observed_at(row)
        dispersion = _target_dispersion(target_low, target_high, target)

        prev = _history_asof(history, isin, run_ts.normalize(), strict=True)
        month = _history_asof(history, isin, run_ts - pd.Timedelta(days=28))
        quarter = _history_asof(history, isin, run_ts - pd.Timedelta(days=84))
        year = _history_asof(history, isin, run_ts - pd.Timedelta(days=350))

        prev_target = _num(prev.get("target_mean")) if prev is not None else None
        m1_target = _num(month.get("target_mean")) if month is not None else None
        m3_target = _num(quarter.get("target_mean")) if quarter is not None else None
        y1_target = _num(year.get("target_mean")) if year is not None else None
        prev_score = _num(prev.get("consensus_score_100")) if prev is not None else None
        m1_score = _num(month.get("consensus_score_100")) if month is not None else None
        m3_score = _num(quarter.get("consensus_score_100")) if quarter is not None else None

        revision_metrics = _revision_metrics(revisions, isin, run_ts, broker_weights)

        values: dict[str, Any] = {
            "target_price": target,
            "target_prev_run": prev_target,
            "target_change_run_abs": _abs_change(target, prev_target),
            "target_change_run_pct": _pct_change(target, prev_target),
            "target_1m_ago": m1_target,
            "target_change_1m_abs": _abs_change(target, m1_target),
            "target_change_1m_pct": _pct_change(target, m1_target),
            "target_3m_ago": m3_target,
            "target_change_3m_abs": _abs_change(target, m3_target),
            "target_change_3m_pct": _pct_change(target, m3_target),
            "target_12m_ago": y1_target,
            "target_change_12m_pct": _pct_change(target, y1_target),
            "target_upside_abs": _abs_change(target, last_close),
            "target_upside_pct": _pct_change(target, last_close),
            "target_low": target_low,
            "target_high": target_high,
            "target_dispersion_pct": dispersion,
            "consensus_score_100": score100,
            "consensus_prev_run": _first_text(prev, ["consensus_rating"]) if prev is not None else None,
            "consensus_delta_run": _abs_change(score100, prev_score),
            "consensus_score_1m_ago": m1_score,
            "consensus_delta_1m": _abs_change(score100, m1_score),
            "consensus_score_3m_ago": m3_score,
            "consensus_delta_3m": _abs_change(score100, m3_score),
            "n_analysts": n_analysts,
            "consensus_source_count": source_count,
            "consensus_as_of": observed_at or run_day,
            **revision_metrics,
        }
        if values["target_change_1m_pct"] is not None and values["target_change_3m_pct"] is not None:
            values["target_revision_acceleration"] = round(
                values["target_change_1m_pct"] - values["target_change_3m_pct"] / 3.0, 4
            )
        else:
            values["target_revision_acceleration"] = None

        values["consensus_confidence"] = _confidence(
            source_count, n_analysts, dispersion, observed_at, run_ts
        )
        values["analyst_momentum_score"] = _momentum_score(values, analyst_cfg)
        signal, gate, review = _signal_and_gate(values, analyst_cfg)
        values["committee_analyst_signal"] = signal
        values["committee_analyst_gate"] = gate
        values["committee_review_required"] = review

        base_score = _first_num(row, ["score_brut"])
        overall_weight = float(analyst_cfg.get("overall_weight", 0.15))
        overall_weight = max(0.0, min(1.0, overall_weight))
        values["committee_score_with_analyst_momentum"] = (
            round(base_score * (1.0 - overall_weight) + values["analyst_momentum_score"] * overall_weight, 2)
            if base_score is not None
            else None
        )

        for field, value in values.items():
            out.at[idx, field] = value

        if target is not None or score100 is not None:
            scored += 1
            snapshots.append({
                "date": run_day,
                "isin": isin,
                "source": source,
                "consensus_rating": rating,
                "consensus_score_100": score100,
                "n_analysts": n_analysts,
                "strong_buy": _first_num(row, ["analyst_strong_buy", "strong_buy"]),
                "buy": _first_num(row, ["analyst_buy", "buy"]),
                "hold": _first_num(row, ["analyst_hold", "hold"]),
                "sell": _first_num(row, ["analyst_sell", "sell"]),
                "strong_sell": _first_num(row, ["analyst_strong_sell", "strong_sell"]),
                "target_low": target_low,
                "target_mean": target,
                "target_high": target_high,
                "last_close": last_close,
            })
        reviews += int(review)
        strong_positive += int(signal == "STRONG_POSITIVE")

    new_snapshots = pd.DataFrame(snapshots, columns=SNAPSHOT_COLUMNS)
    if history.empty:
        combined = new_snapshots
    else:
        combined = pd.concat([history[SNAPSHOT_COLUMNS], new_snapshots], ignore_index=True)
    if not combined.empty:
        combined = combined.drop_duplicates(
            subset=["date", "isin", "source"], keep="last"
        ).sort_values(["date", "isin", "source"]).reset_index(drop=True)

    metrics = {
        "scored_or_snapshotted": scored,
        "mandatory_reviews": reviews,
        "strong_positive": strong_positive,
        "snapshot_rows": len(combined),
        "overall_weight": float(analyst_cfg.get("overall_weight", 0.15)),
        "execution_gate": "SHADOW_BLOCKED",
    }
    return out, combined, metrics


def process_enriched_outputs(root: Path | None = None) -> dict:
    from v182.io.frames import load_master, save_master
    from v182.reporting.exports import export_master_excel

    root = root or ROOT
    outputs = root / "outputs"
    config = root / "config"
    history_dir = outputs / "history"
    audit_dir = outputs / "audit"
    history_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = config / "V18.2_CONSENSUS_PIPELINE.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    actions_path = outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    history_path = history_dir / "V18.2_CONSENSUS_SNAPSHOTS.csv"
    revisions_path = history_dir / "V18.2_ANALYST_REVISIONS.csv"
    broker_weights_path = config / "V18.2_BROKER_WEIGHTS.csv"

    actions = load_master(actions_path)
    history = _load_csv(history_path, SNAPSHOT_COLUMNS)
    revisions = _load_csv(revisions_path, REVISION_COLUMNS)
    weights = _broker_weight_map(broker_weights_path)

    enriched, snapshots, metrics = enrich_analyst_momentum(
        actions,
        history=history,
        revisions=revisions,
        broker_weights=weights,
        cfg=cfg,
        run_date=datetime.now(timezone.utc),
    )
    save_master(enriched, actions_path)
    snapshots.to_csv(history_path, sep=";", index=False, encoding="utf-8-sig")
    if not revisions_path.exists():
        pd.DataFrame(columns=REVISION_COLUMNS).to_csv(
            revisions_path, sep=";", index=False, encoding="utf-8-sig"
        )

    shortlist = enriched
    if "comite_status" in enriched.columns:
        shortlist = enriched[enriched["comite_status"].isin(["COMMITTEE", "WATCH"])].copy()
    committee_fields = [
        field for field in [
            "isin", "name", "yahoo_ticker", "comite_status", "score_brut",
            "committee_score_with_analyst_momentum", "analyst_momentum_score",
            "committee_analyst_signal", "committee_analyst_gate",
            "committee_review_required", "target_price", "last_close",
            "target_upside_abs", "target_upside_pct", "target_change_run_abs",
            "target_change_run_pct", "target_change_1m_abs", "target_change_1m_pct",
            "target_change_3m_abs", "target_change_3m_pct",
            "target_revision_acceleration", "consensus_rating",
            "consensus_score_100", "consensus_delta_run", "consensus_delta_1m",
            "consensus_delta_3m", "upgrades_30d", "downgrades_30d",
            "target_raises_30d", "target_cuts_30d", "revision_breadth_30d",
            "weighted_target_revision_30d_pct", "n_analysts",
            "consensus_source_count", "consensus_confidence", "consensus_as_of",
        ] if field in shortlist.columns
    ]
    shortlist[committee_fields].to_csv(
        outputs / "V18.2_COMMITTEE_ANALYST_MOMENTUM.csv",
        sep=";", index=False, encoding="utf-8-sig"
    )
    export_master_excel(
        enriched,
        outputs / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
        "V18.2 Actions PEA actualisées",
    )
    (audit_dir / "V18.2_ANALYST_MOMENTUM_METRICS.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


def main() -> None:
    metrics = process_enriched_outputs()
    print(
        "WAVE_09_ANALYST_MOMENTUM — "
        f"snapshots={metrics['snapshot_rows']} | "
        f"reviews={metrics['mandatory_reviews']} | "
        f"strong_positive={metrics['strong_positive']}"
    )


if __name__ == "__main__":
    main()
