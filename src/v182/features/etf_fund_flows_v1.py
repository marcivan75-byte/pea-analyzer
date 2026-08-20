from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

CONFIDENCE_ORDER = {"QUARANTINE": 0, "D": 1, "C": 2, "B": 3, "A": 4}
REQUIRED_SNAPSHOT_COLUMNS = {
    "instrument_id", "as_of", "name", "universe", "asset_class",
    "economic_family", "region", "source", "source_type", "confidence",
}


@dataclass(frozen=True)
class FlowComputation:
    observations: pd.DataFrame
    instruments: pd.DataFrame
    families: pd.DataFrame
    rotations: pd.DataFrame
    diagnostics: dict


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _confidence_grade(value: object) -> str:
    grade = str(value or "").strip().upper()
    return grade if grade in CONFIDENCE_ORDER else "D"


def _min_confidence(*values: object) -> str:
    grades = [_confidence_grade(value) for value in values]
    return min(grades, key=lambda grade: CONFIDENCE_ORDER[grade]) if grades else "D"


def _rank_score(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() <= 1:
        return pd.Series(np.where(numeric.notna(), 50.0, np.nan), index=series.index, dtype=float)
    return numeric.rank(method="average", pct=True).mul(100.0)


def _weighted_mean(values: list[tuple[float | None, float]]) -> float | None:
    usable = [(float(value), float(weight)) for value, weight in values if value is not None and math.isfinite(float(value))]
    if not usable:
        return None
    total_weight = sum(weight for _, weight in usable)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in usable) / total_weight


def _flow_price_score(flow_rate: float | None, price_return: float | None) -> tuple[float | None, str]:
    if flow_rate is None or price_return is None:
        return None, "DATA_INSUFFICIENT"
    if flow_rate > 0.001 and price_return > 0.005:
        return 80.0, "ROTATION_CONFIRMED"
    if flow_rate > 0.001 and price_return <= 0.005:
        return 90.0, "EARLY_ACCUMULATION"
    if flow_rate < -0.001 and price_return > 0.005:
        return 20.0, "DISTRIBUTION_WARNING"
    if flow_rate < -0.001 and price_return < -0.005:
        return 10.0, "EXIT_CONFIRMED"
    if abs(flow_rate) <= 0.001 and price_return > 0.005:
        return 45.0, "PRICE_UP_WITHOUT_FLOW_CONFIRMATION"
    if abs(flow_rate) <= 0.001 and price_return < -0.005:
        return 40.0, "PRICE_DOWN_WITHOUT_FLOW_CONFIRMATION"
    return 50.0, "NEUTRAL"


def _normalise_snapshot(snapshot: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_SNAPSHOT_COLUMNS - set(snapshot.columns)
    if missing:
        raise ValueError(f"ETF_FLOW_SNAPSHOT_MISSING_COLUMNS:{','.join(sorted(missing))}")
    frame = snapshot.copy()
    frame["instrument_id"] = frame["instrument_id"].astype(str).str.strip()
    frame["as_of"] = pd.to_datetime(frame["as_of"], errors="coerce", utc=True)
    if frame["as_of"].isna().any():
        raise ValueError("ETF_FLOW_INVALID_AS_OF")
    if frame["as_of"].gt(pd.Timestamp.now(tz="UTC").normalize()).any():
        raise ValueError("ETF_FLOW_FUTURE_AS_OF_FORBIDDEN")
    frame["confidence"] = frame["confidence"].map(_confidence_grade)
    for col in ("aum", "nav", "shares_outstanding", "market_price", "distribution_per_share"):
        if col not in frame.columns:
            frame[col] = np.nan
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    def parse_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "oui"}

    for col in ("is_pea", "is_inverse_or_leveraged", "is_synthetic"):
        if col not in frame.columns:
            frame[col] = False
        frame[col] = frame[col].map(parse_bool)
    for col in ("sector_or_theme", "currency", "benchmark", "ticker", "isin", "provider"):
        if col not in frame.columns:
            frame[col] = ""
        frame[col] = frame[col].fillna("").astype(str)
    if "source_priority" not in frame.columns:
        frame["source_priority"] = 0.0
    frame["source_priority"] = pd.to_numeric(frame["source_priority"], errors="coerce").fillna(0.0)
    frame["_confidence_rank"] = frame["confidence"].map(CONFIDENCE_ORDER).fillna(0)
    frame = frame.sort_values(["instrument_id", "as_of", "source_priority", "_confidence_rank"], ascending=[True, True, False, False])
    return frame.drop_duplicates(["instrument_id", "as_of"], keep="first").drop(columns="_confidence_rank").reset_index(drop=True)


def _derive_period_return(current: pd.Series, previous: pd.Series) -> float | None:
    current_nav, previous_nav = _num(current.get("nav")), _num(previous.get("nav"))
    distribution = _num(current.get("distribution_per_share")) or 0.0
    if current_nav is not None and previous_nav not in (None, 0.0):
        return (current_nav + distribution) / previous_nav - 1.0
    current_price, previous_price = _num(current.get("market_price")), _num(previous.get("market_price"))
    if current_price is not None and previous_price not in (None, 0.0):
        return current_price / previous_price - 1.0
    return None


def _daily_flow(current: pd.Series, previous: pd.Series) -> tuple[float | None, str, str]:
    confidence = _min_confidence(current.get("confidence"), previous.get("confidence"))
    if confidence in {"D", "QUARANTINE"}:
        return None, "UNSCORABLE_LOW_CONFIDENCE", confidence
    current_shares, previous_shares = _num(current.get("shares_outstanding")), _num(previous.get("shares_outstanding"))
    current_nav, previous_nav = _num(current.get("nav")), _num(previous.get("nav"))
    if any(value is not None and value <= 0 for value in (current_shares, previous_shares, current_nav, previous_nav)):
        return None, "QUARANTINED_NON_POSITIVE_STRUCTURE", "QUARANTINE"
    if current_shares is not None and previous_shares is not None and current_nav is not None:
        if previous_shares > 0 and previous_nav is not None:
            share_ratio = current_shares / previous_shares
            nav_ratio = current_nav / previous_nav
            if (share_ratio >= 1.5 or share_ratio <= 2.0 / 3.0) and abs(share_ratio * nav_ratio - 1.0) <= 0.08:
                return None, "QUARANTINED_SPLIT_LIKE_EVENT", "QUARANTINE"
        return (current_shares - previous_shares) * current_nav, "SHARES_NAV", confidence
    current_aum, previous_aum = _num(current.get("aum")), _num(previous.get("aum"))
    if any(value is not None and value <= 0 for value in (current_aum, previous_aum)):
        return None, "QUARANTINED_NON_POSITIVE_AUM", "QUARANTINE"
    performance = _derive_period_return(current, previous)
    if current_aum is not None and previous_aum is not None and performance is not None:
        return current_aum - previous_aum * (1.0 + performance), "AUM_PERFORMANCE_ADJUSTED", confidence
    return None, "DATA_INSUFFICIENT", confidence


def compute_daily_flows(snapshot_history: pd.DataFrame) -> pd.DataFrame:
    frame = _normalise_snapshot(snapshot_history)
    rows: list[dict] = []
    for instrument_id, group in frame.groupby("instrument_id", sort=False):
        ordered = group.sort_values("as_of").reset_index(drop=True)
        for index, current in ordered.iterrows():
            row = current.to_dict()
            row.update({"flow": np.nan, "flow_method": "FIRST_OBSERVATION", "flow_confidence": _confidence_grade(current.get("confidence")), "organic_flow_rate": np.nan, "period_return": np.nan})
            if index > 0:
                previous = ordered.iloc[index - 1]
                flow, method, confidence = _daily_flow(current, previous)
                row["flow"], row["flow_method"], row["flow_confidence"] = (flow if flow is not None else np.nan), method, confidence
                previous_aum = _num(previous.get("aum"))
                if flow is not None and previous_aum not in (None, 0.0):
                    row["organic_flow_rate"] = flow / previous_aum
                period_return = _derive_period_return(current, previous)
                row["period_return"] = period_return if period_return is not None else np.nan
            row["instrument_id"] = instrument_id
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["instrument_id", "as_of"]).reset_index(drop=True)


def _rolling_features(group: pd.DataFrame) -> pd.DataFrame:
    group = group.sort_values("as_of").copy()
    flow = pd.to_numeric(group["flow"], errors="coerce")
    aum = pd.to_numeric(group["aum"], errors="coerce")
    returns = pd.to_numeric(group["period_return"], errors="coerce")
    for horizon in (5, 20, 60, 252):
        group[f"flow_{horizon}d"] = flow.rolling(horizon, min_periods=horizon).sum()
        group[f"organic_flow_rate_{horizon}d"] = group[f"flow_{horizon}d"] / aum.shift(horizon).replace(0.0, np.nan)
        group[f"price_return_{horizon}d"] = (1.0 + returns).rolling(horizon, min_periods=horizon).apply(np.prod, raw=True) - 1.0
    positive = flow.gt(0).where(flow.notna())
    group["positive_days_20d_pct"] = positive.rolling(20, min_periods=20).mean().mul(100.0)
    group["flow_acceleration"] = group["organic_flow_rate_5d"].div(5.0) - group["organic_flow_rate_20d"].div(20.0)
    group["daily_flow_percentile_252d"] = flow.div(aum.shift(1).replace(0.0, np.nan)).rolling(252, min_periods=60).apply(lambda values: pd.Series(values).rank(pct=True).iloc[-1] * 100.0, raw=False)
    years = group["as_of"].dt.year
    group["flow_ytd"] = flow.groupby(years).cumsum()
    group["organic_flow_rate_ytd"] = group["flow_ytd"] / aum.groupby(years).transform("first").replace(0.0, np.nan)
    group["history_observations"] = np.arange(1, len(group) + 1)
    group["flow_observations"] = flow.notna().cumsum()
    return group


def add_rolling_features(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily.copy()
    return pd.concat([_rolling_features(group.copy()) for _, group in daily.groupby("instrument_id", sort=False)], ignore_index=True)


def _current_instruments(rolling: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if rolling.empty:
        return pd.DataFrame()
    current = rolling.sort_values("as_of").groupby("instrument_id", as_index=False).tail(1).copy()
    current["score_flow_5d"] = _rank_score(current["organic_flow_rate_5d"])
    current["score_flow_20d"] = _rank_score(current["organic_flow_rate_20d"])
    current["score_flow_60d"] = _rank_score(current["organic_flow_rate_60d"])
    current["score_acceleration"] = _rank_score(current["flow_acceleration"])
    current["score_persistence"] = pd.to_numeric(current["positive_days_20d_pct"], errors="coerce")
    current["peer_relative_raw"] = pd.to_numeric(current["organic_flow_rate_20d"], errors="coerce") - current.groupby("economic_family")["organic_flow_rate_20d"].transform("median")
    current["score_peer_relative"] = _rank_score(current["peer_relative_raw"])

    currency_count = current.groupby("economic_family")["currency"].transform(lambda values: len({str(value).strip() for value in values if str(value).strip()}))
    family_abs_flow = current.groupby("economic_family")["flow_20d"].transform(lambda values: pd.to_numeric(values, errors="coerce").abs().sum(min_count=1))
    share = pd.to_numeric(current["flow_20d"], errors="coerce").div(family_abs_flow.replace(0.0, np.nan)).mul(100.0)
    current["flow_share_family_20d_pct"] = share.where(currency_count.eq(1), np.nan)

    confirmation = [_flow_price_score(_num(row.get("organic_flow_rate_20d")), _num(row.get("price_return_20d"))) for _, row in current.iterrows()]
    current["score_flow_price_confirmation"] = [score if score is not None else np.nan for score, _ in confirmation]
    current["flow_price_state"] = [state for _, state in confirmation]

    score_columns = {
        "flow_5d": "score_flow_5d", "flow_20d": "score_flow_20d", "flow_60d": "score_flow_60d",
        "acceleration": "score_acceleration", "persistence": "score_persistence",
        "peer_relative": "score_peer_relative", "flow_price_confirmation": "score_flow_price_confirmation",
    }
    scores: list[float] = []
    readiness: list[str] = []
    min_preliminary = int(cfg.get("preliminary_score_min_observations", 20))
    min_mature = int(cfg.get("mature_score_min_observations", 60))
    for _, row in current.iterrows():
        flow_count = int(row.get("flow_observations", 0))
        if flow_count < min_preliminary:
            scores.append(np.nan)
            readiness.append("DATA_INSUFFICIENT_LT20")
            continue
        score = _weighted_mean([(_num(row.get(column)), float(cfg["score_weights"][key])) for key, column in score_columns.items()])
        scores.append(round(score, 4) if score is not None else np.nan)
        readiness.append("MATURE_60_PLUS" if flow_count >= min_mature else "PRELIMINARY_20_59")
    current["efs_shadow"] = scores
    current["efs_readiness"] = readiness
    current["efs_status"] = pd.cut(current["efs_shadow"], bins=[-np.inf, 30, 45, 55, 65, 80, np.inf], labels=["STRONG_OUTFLOW", "OUTFLOW", "NEUTRAL", "MODERATE_INFLOW", "STRONG_INFLOW", "EXCEPTIONAL_ACCUMULATION"], right=False).astype("string")
    current.loc[current["efs_shadow"].isna(), "efs_status"] = "DATA_INSUFFICIENT"
    current["decision_influence"] = 0.0
    current["live_orders_enabled"] = False
    return current.reset_index(drop=True)


def build_family_scores(instruments: pd.DataFrame) -> pd.DataFrame:
    if instruments.empty:
        return pd.DataFrame()
    eligible = instruments[~instruments["is_inverse_or_leveraged"].fillna(False).astype(bool) & ~instruments["flow_confidence"].isin(["D", "QUARANTINE"])].copy()
    rows: list[dict] = []
    for (family_name, region), group in eligible.groupby(["economic_family", "region"], dropna=False):
        currencies = sorted({str(value).strip() for value in group["currency"] if str(value).strip()})
        comparable = len(currencies) == 1
        rows.append({
            "economic_family": family_name, "region": region, "instruments": int(group["instrument_id"].nunique()),
            "currency": currencies[0] if comparable else "MIXED_OR_UNKNOWN", "absolute_flow_comparable": comparable,
            "flow_5d": pd.to_numeric(group["flow_5d"], errors="coerce").sum(min_count=1) if comparable else np.nan,
            "flow_20d": pd.to_numeric(group["flow_20d"], errors="coerce").sum(min_count=1) if comparable else np.nan,
            "flow_60d": pd.to_numeric(group["flow_60d"], errors="coerce").sum(min_count=1) if comparable else np.nan,
            "mean_organic_flow_rate_20d": pd.to_numeric(group["organic_flow_rate_20d"], errors="coerce").mean(),
            "breadth_positive_20d_pct": pd.to_numeric(group["organic_flow_rate_20d"], errors="coerce").gt(0).mean() * 100.0,
            "mean_efs_shadow": pd.to_numeric(group["efs_shadow"], errors="coerce").mean(),
            "mean_price_return_20d": pd.to_numeric(group["price_return_20d"], errors="coerce").mean(),
        })
    family = pd.DataFrame(rows)
    if family.empty:
        return family
    family["family_flow_score"] = _rank_score(family["mean_organic_flow_rate_20d"])
    family["decision_influence"] = 0.0
    return family


def add_pea_overlay(instruments: pd.DataFrame, families: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if instruments.empty:
        return instruments.copy()
    result = instruments.copy()
    global_family = families.groupby("economic_family")["family_flow_score"].mean().to_dict() if not families.empty else {}
    pea_family = result[result["is_pea"].fillna(False).astype(bool)].groupby("economic_family")["efs_shadow"].mean().to_dict()
    threshold = int(cfg["pea_overlay_weights"]["young_history_threshold_observations"])
    overlays: list[float] = []
    for _, row in result.iterrows():
        if not bool(row.get("is_pea", False)):
            overlays.append(np.nan)
            continue
        weights = cfg["pea_overlay_weights"]["young_history"] if int(row.get("flow_observations", 0)) < threshold else cfg["pea_overlay_weights"]["mature"]
        score = _weighted_mean([
            (_num(row.get("efs_shadow")), weights["own"]),
            (_num(global_family.get(row.get("economic_family"))), weights["global_family"]),
            (_num(pea_family.get(row.get("economic_family"))), weights["pea_family"]),
        ])
        overlays.append(round(score, 4) if score is not None else np.nan)
    result["pea_flow_overlay_shadow"] = overlays
    result["pea_flow_overlay_influence"] = 0.0
    return result


def build_rotation_scores(instruments: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if instruments.empty:
        return pd.DataFrame()
    eligible = instruments[
        ~instruments["is_inverse_or_leveraged"].fillna(False).astype(bool)
        & ~instruments["flow_confidence"].isin(["D", "QUARANTINE"])
        & instruments["sector_or_theme"].astype(str).str.len().gt(0)
    ].copy()
    rows: list[dict] = []
    for label, group in eligible.groupby("sector_or_theme", sort=True):
        rates5 = pd.to_numeric(group["organic_flow_rate_5d"], errors="coerce")
        rates20 = pd.to_numeric(group["organic_flow_rate_20d"], errors="coerce")
        rates60 = pd.to_numeric(group["organic_flow_rate_60d"], errors="coerce")
        price = pd.to_numeric(group["price_return_20d"], errors="coerce")
        flow_price, state = _flow_price_score(_num(rates20.mean()), _num(price.mean()))
        currencies = sorted({str(value).strip() for value in group["currency"] if str(value).strip()})
        comparable = len(currencies) == 1
        positive_regions = group.loc[rates20.gt(0), "region"].astype(str).replace("", np.nan).dropna().nunique()
        total_regions = group["region"].astype(str).replace("", np.nan).dropna().nunique()
        rows.append({
            "sector_or_theme": label, "instrument_count": int(group["instrument_id"].nunique()),
            "currency": currencies[0] if comparable else "MIXED_OR_UNKNOWN", "absolute_flow_comparable": comparable,
            "aggregate_flow_5d": float(pd.to_numeric(group["flow_5d"], errors="coerce").sum(min_count=1)) if comparable else np.nan,
            "aggregate_flow_20d": float(pd.to_numeric(group["flow_20d"], errors="coerce").sum(min_count=1)) if comparable else np.nan,
            "aggregate_flow_60d": float(pd.to_numeric(group["flow_60d"], errors="coerce").sum(min_count=1)) if comparable else np.nan,
            "mean_rate_5d": rates5.mean(), "mean_rate_20d": rates20.mean(), "mean_rate_60d": rates60.mean(),
            "breadth_positive_20d_pct": rates20.gt(0).mean() * 100.0 if rates20.notna().any() else np.nan,
            "mean_acceleration": pd.to_numeric(group["flow_acceleration"], errors="coerce").mean(),
            "mean_persistence_20d_pct": pd.to_numeric(group["positive_days_20d_pct"], errors="coerce").mean(),
            "regional_confirmation_pct": positive_regions / total_regions * 100.0 if total_regions else np.nan,
            "mean_price_return_20d": price.mean(), "flow_price_confirmation_score": flow_price, "flow_price_state": state,
        })
    rotations = pd.DataFrame(rows)
    if rotations.empty:
        return rotations
    rotations["score_aggregate_flow"] = _rank_score(rotations["mean_rate_5d"]) * 0.35 + _rank_score(rotations["mean_rate_20d"]) * 0.40 + _rank_score(rotations["mean_rate_60d"]) * 0.25
    rotations["score_breadth"] = rotations["breadth_positive_20d_pct"]
    rotations["score_acceleration"] = _rank_score(rotations["mean_acceleration"])
    rotations["score_regional_confirmation"] = rotations["regional_confirmation_pct"]
    rotations["score_persistence"] = rotations["mean_persistence_20d_pct"]
    rotations["score_price_confirmation"] = rotations["flow_price_confirmation_score"]
    weights = cfg["sector_rotation_flow_weights"]
    rotations["srfs_shadow"] = [
        _weighted_mean([
            (_num(row["score_aggregate_flow"]), weights["aggregate_flow"]),
            (_num(row["score_breadth"]), weights["breadth"]),
            (_num(row["score_acceleration"]), weights["acceleration"]),
            (_num(row["score_regional_confirmation"]), weights["regional_confirmation"]),
            (_num(row["score_persistence"]), weights["persistence"]),
            (_num(row["score_price_confirmation"]), weights["price_confirmation"]),
        ]) for _, row in rotations.iterrows()
    ]
    rotations["srfs_shadow"] = pd.to_numeric(rotations["srfs_shadow"], errors="coerce").round(4)
    rotations["decision_influence"] = 0.0
    return rotations.sort_values("srfs_shadow", ascending=False, na_position="last").reset_index(drop=True)


def build_gold_crypto_summary(instruments: pd.DataFrame, cfg: dict) -> dict:
    if instruments.empty:
        return {"gold": {}, "crypto": {}, "crypto_short": {}}
    gold = instruments[instruments["asset_class"].astype(str).str.upper().isin(["GOLD_ETC", "GOLD_ETF", "GOLD_MINERS_ETF"])].copy()
    crypto = instruments[instruments["asset_class"].astype(str).str.upper().isin(["CRYPTO_ETP", "CRYPTO_ETF"])].copy()
    crypto_short = instruments[instruments["asset_class"].astype(str).str.upper().eq("CRYPTO_SHORT_ETF")].copy()

    def mean_score(frame: pd.DataFrame, mask: pd.Series) -> float | None:
        values = pd.to_numeric(frame.loc[mask, "efs_shadow"], errors="coerce")
        return float(values.mean()) if values.notna().any() else None

    gold_payload: dict = {}
    if not gold.empty:
        us = mean_score(gold, gold["region"].astype(str).str.upper().eq("US"))
        eu = mean_score(gold, gold["region"].astype(str).str.upper().isin(["EU", "EUROPE"]))
        miners = mean_score(gold, gold["asset_class"].astype(str).str.upper().eq("GOLD_MINERS_ETF"))
        price_value = pd.to_numeric(gold["score_flow_price_confirmation"], errors="coerce").mean()
        price = None if pd.isna(price_value) else float(price_value)
        weights = cfg["gold_flow_composite_weights"]
        composite = _weighted_mean([(us, weights["us_physical"]), (eu, weights["eu_physical"]), (miners, weights["gold_miners"]), (price, weights["price_confirmation"])])
        gold_payload = {"us_physical_score": us, "eu_physical_score": eu, "gold_miners_score": miners, "price_confirmation_score": price, "gold_flow_composite_shadow": round(composite, 4) if composite is not None else None, "decision_influence": 0.0}
    crypto_payload = {}
    for family, group in crypto.groupby("economic_family"):
        value = pd.to_numeric(group["efs_shadow"], errors="coerce").mean()
        crypto_payload[str(family)] = {"instrument_count": int(group["instrument_id"].nunique()), "flow_score_shadow": None if pd.isna(value) else round(float(value), 4), "decision_influence": 0.0}
    short_payload: dict = {}
    if not crypto_short.empty:
        value = pd.to_numeric(crypto_short["efs_shadow"], errors="coerce").mean()
        short_payload = {"instrument_count": int(crypto_short["instrument_id"].nunique()), "speculative_short_flow_score_shadow": None if pd.isna(value) else round(float(value), 4), "main_rotation_score_influence": 0.0, "kept_separate_from_long_crypto_flows": True}
    return {"gold": gold_payload, "crypto": crypto_payload, "crypto_short": short_payload}


def build_flow_computation(snapshot_history: pd.DataFrame, cfg: dict) -> FlowComputation:
    daily = compute_daily_flows(snapshot_history)
    rolling = add_rolling_features(daily)
    instruments = _current_instruments(rolling, cfg)
    families = build_family_scores(instruments)
    instruments = add_pea_overlay(instruments, families, cfg)
    rotations = build_rotation_scores(instruments, cfg)
    diagnostics = {
        "version": cfg["version"], "mode": cfg["mode"], "observations": int(len(rolling)),
        "instruments": int(instruments["instrument_id"].nunique()) if not instruments.empty else 0,
        "scorable_instruments": int(pd.to_numeric(instruments.get("efs_shadow"), errors="coerce").notna().sum()) if not instruments.empty else 0,
        "pea_instruments": int(instruments["is_pea"].fillna(False).astype(bool).sum()) if not instruments.empty else 0,
        "quarantined_or_d_grade": int(instruments["flow_confidence"].isin(["D", "QUARANTINE"]).sum()) if not instruments.empty else 0,
        "gold_crypto": build_gold_crypto_summary(instruments, cfg), "decision_influence": 0.0, "live_orders_enabled": False,
    }
    return FlowComputation(rolling, instruments, families, rotations, diagnostics)
