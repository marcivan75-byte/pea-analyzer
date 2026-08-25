from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import math
from pathlib import Path
from statistics import fmean
from typing import Any, cast

from .io import load_json, write_json_atomic
from .utils import clamp, finite, parse_utc


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def _rsi(values: list[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    deltas = [values[index] - values[index - 1] for index in range(len(values) - window, len(values))]
    gains = fmean(max(delta, 0.0) for delta in deltas)
    losses = fmean(max(-delta, 0.0) for delta in deltas)
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _weighted_quality(components: dict[str, float | None], weights: dict[str, float]) -> tuple[float, float]:
    weighted = 0.0
    observed = 0.0
    for name, weight in weights.items():
        value = components.get(name)
        if value is None:
            continue
        weighted += clamp(value) * weight
        observed += weight
    return (weighted / observed if observed else 0.0, observed)


def _volume_score(ratio: float | None, minimum: float) -> float | None:
    if ratio is None:
        return None
    return clamp(50.0 + (ratio - minimum) / max(2.0 - minimum, 0.25) * 50.0)


def _breakout_score(distance_atr: float | None) -> float | None:
    if distance_atr is None:
        return None
    if distance_atr < 0:
        return 0.0
    if distance_atr <= 0.5:
        return clamp(70.0 + distance_atr * 60.0)
    if distance_atr <= 1.5:
        return clamp(100.0 - (distance_atr - 0.5) * 70.0)
    return clamp(30.0 - (distance_atr - 1.5) * 30.0)


def _event_id(asset_id: str, bar_time: str) -> str:
    raw = f"{asset_id.lower()}|{bar_time}|CRYPTO_T1"
    return "CT1_" + sha256(raw.encode("utf-8")).hexdigest()[:20]


def _state_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = finite(value)
        return number is not None and number >= 0.5
    return str(value or "").strip().lower() in {"true", "1", "yes", "y", "oui", "pass"}


def _clean_history(history: list[dict[str, Any]], as_of: datetime) -> list[dict[str, float | int | None]]:
    as_of_ms = int(as_of.timestamp() * 1000)
    rows: list[dict[str, float | int | None]] = []
    seen: set[int] = set()
    candidates = list(history)
    if any(int(candidates[index].get("ts", 0)) < int(candidates[index - 1].get("ts", 0)) for index in range(1, len(candidates))):
        candidates.sort(key=lambda item: int(item.get("ts", 0)))
    for row in candidates:
        timestamp = int(row.get("ts", 0))
        price = finite(row.get("price"))
        volume = finite(row.get("volume"))
        if timestamp <= 0 or timestamp > as_of_ms or timestamp in seen or price is None or price <= 0:
            continue
        seen.add(timestamp)
        high = finite(row.get("high"))
        low = finite(row.get("low"))
        open_price = finite(row.get("open"))
        valid_ohlc = high is not None and low is not None and high >= price >= low and high >= low
        rows.append({
            "ts": timestamp, "price": price, "volume": volume or 0.0,
            "open": open_price if valid_ohlc else None, "high": high if valid_ohlc else None, "low": low if valid_ohlc else None,
        })
    return rows


def compute_timing_metrics(history: list[dict[str, Any]], as_of: datetime, config: dict[str, Any]) -> dict[str, Any] | None:
    rows = _clean_history(history, as_of)
    minimum = int(config["minimum_history_days"])
    if len(rows) < minimum:
        return None
    rows = rows[-minimum:]
    closes = [cast(float, row["price"]) for row in rows]
    volumes = [cast(float, row["volume"]) for row in rows]
    upper: list[float | None] = []
    bandwidth: list[float | None] = []
    rolling_sum = 0.0
    rolling_square_sum = 0.0
    for index in range(len(closes)):
        value = closes[index]
        rolling_sum += value
        rolling_square_sum += value * value
        if index >= 20:
            expired = closes[index - 20]
            rolling_sum -= expired
            rolling_square_sum -= expired * expired
        if index < 19:
            upper.append(None)
            bandwidth.append(None)
            continue
        middle = rolling_sum / 20.0
        variance = max(0.0, rolling_square_sum / 20.0 - middle * middle)
        sigma = math.sqrt(variance)
        upper.append(middle + 2.0 * sigma)
        bandwidth.append(4.0 * sigma / middle if middle else None)
    current_bw, previous_bw = bandwidth[-1], bandwidth[-2]
    current_upper, previous_upper = upper[-1], upper[-2]
    if current_bw is None or previous_bw is None or current_upper is None or previous_upper is None:
        return None
    squeeze = config["squeeze"]
    lookback = int(squeeze["lookback_days"])
    prior_bandwidth = [value for value in bandwidth[-lookback - 1 : -1] if value is not None]
    threshold = _percentile(prior_bandwidth, float(squeeze["percentile"]))
    squeeze_days = int(squeeze["minimum_consecutive_days"])
    segment = [value for value in bandwidth[-squeeze_days - 1 : -1] if value is not None]
    compression_fraction = sum(value < threshold for value in segment) / len(segment) if threshold and segment else 0.0
    depth = fmean(segment) / threshold if threshold and segment else None
    compression_score = None if depth is None else 0.6 * clamp((compression_fraction - 0.5) / 0.5 * 100.0) + 0.4 * clamp((1.0 - depth) / 0.35 * 100.0)
    prior_volume = volumes[-21:-1]
    volume_average = fmean(prior_volume) if prior_volume and any(prior_volume) else None
    rvol = volumes[-1] / volume_average if volume_average else None
    previous_volume_ratio = volumes[-1] / volumes[-2] if volumes[-2] > 0 else None
    ema12, ema26 = _ema(closes, 12), _ema(closes, 26)
    macd = [fast - slow for fast, slow in zip(ema12, ema26)]
    signal = _ema(macd, 9)
    macd_hist = [value - reference for value, reference in zip(macd, signal)]
    recent_hist = macd_hist[-3:]
    rising_share = sum(current > previous for previous, current in zip(recent_hist, recent_hist[1:])) / max(len(recent_hist) - 1, 1)
    hist_scale = max((abs(value) for value in recent_hist), default=1e-12) or 1e-12
    near_zero = clamp(1.0 - abs(macd_hist[-1]) / hist_scale, 0.0, 1.0)
    daily_moves = [abs(current - previous) for previous, current in zip(closes[-15:-1], closes[-14:])]
    atr_proxy = fmean(daily_moves) if daily_moves else None
    distance_atr = (closes[-1] - current_upper) / atr_proxy if atr_proxy else None
    distance_pct = closes[-1] / current_upper - 1.0 if current_upper else None
    return_30d = (closes[-1] / closes[-31] - 1.0) * 100.0 if len(closes) >= 31 else None
    risk_move_pct = atr_proxy / closes[-1] if atr_proxy and closes[-1] else None
    return {
        "bar_time": datetime.fromtimestamp(cast(int, rows[-1]["ts"]) / 1000.0, tz=timezone.utc).date().isoformat(),
        "bar_ts": cast(int, rows[-1]["ts"]),
        "close": closes[-1],
        "previous_close": closes[-2],
        "bb_upper": current_upper,
        "previous_bb_upper": previous_upper,
        "bandwidth": current_bw,
        "previous_bandwidth": previous_bw,
        "compression_fraction": compression_fraction,
        "squeeze_consecutive_days": len(segment) if threshold and len(segment) == squeeze_days and all(value < threshold for value in segment) else 0,
        "compression_score": compression_score,
        "rvol": rvol,
        "volume_increase_ratio": previous_volume_ratio,
        "macd_hist": macd_hist[-1],
        "previous_macd_hist": macd_hist[-2],
        "macd_rising_share": rising_share,
        "macd_near_zero": near_zero,
        "rsi14": _rsi(closes),
        "sma50": fmean(closes[-50:]),
        "atr_proxy": atr_proxy,
        "distance_atr": distance_atr,
        "distance_pct": distance_pct,
        "return_30d_pct": return_30d,
        "risk_move_pct": risk_move_pct,
    }


def evaluate_timing(
    asset_id: str,
    metrics: dict[str, Any],
    state: dict[str, Any] | None,
    baseline_ok: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    state = dict(state or {})
    state_invalidated = False
    t1, t2 = config["t1"], config["t2"]
    bar_time = str(metrics["bar_time"])
    bar_date = datetime.fromisoformat(bar_time).date()
    if state and state.get("rule_version") != config["version"]:
        state = {}
        state_invalidated = True
    if state:
        source_time = str(state.get("detected_at") or "")
        try:
            source_date = datetime.fromisoformat(source_time).date()
        except ValueError:
            state = {}
            state_invalidated = True
        else:
            age = (bar_date - source_date).days
            source_bar_ts = int(state.get("bar_ts", 0))
            current_bar_ts = int(metrics["bar_ts"])
            if age < 0 or current_bar_ts < source_bar_ts or age > int(config["ttl_calendar_days"]):
                state = {}
                state_invalidated = True
            elif current_bar_ts == source_bar_ts:
                return {
                    "setup": "T1",
                    "timing_state": str(state.get("timing_state") or "T1_WATCH_SHADOW"),
                    "quality": finite(state.get("quality")),
                    "coverage": finite(state.get("coverage")) or 0.0,
                    "event_id": state.get("event_id"),
                    "age_days": 0,
                    "reason": None,
                    "state_action": "KEEP",
                }
            else:
                source_bandwidth = finite(state.get("bandwidth"))
                ratio = metrics["bandwidth"] / source_bandwidth if source_bandwidth else None
                bandwidth_continues = bool(
                    ratio is not None and ratio > float(t2["bandwidth_expansion_ratio_min"])
                    and metrics["bandwidth"] > metrics["previous_bandwidth"]
                )
                macd_crossover = metrics["previous_macd_hist"] <= 0 < metrics["macd_hist"]
                volume_increases = bool(
                    metrics["volume_increase_ratio"] is not None
                    and metrics["volume_increase_ratio"] > float(t2["volume_increase_ratio_min"])
                )
                holds_upper = metrics["close"] > metrics["bb_upper"]
                above_sma50 = metrics["close"] > metrics["sma50"]
                t2_components: dict[str, float | None] = {
                    "bandwidth_continues": 100.0 if bandwidth_continues else 0.0,
                    "above_upper_band": 100.0 if holds_upper else 0.0,
                    "macd_bullish_crossover": 100.0 if macd_crossover else 0.0,
                    "volume_increases": 100.0 if volume_increases else 0.0,
                    "above_sma50": 100.0 if above_sma50 else 0.0,
                }
                quality, coverage = _weighted_quality(t2_components, t2["components"])
                gates = (
                    baseline_ok
                    and _state_bool(state.get("baseline_eligible_at_t1"))
                    and bool(str(state.get("event_id") or ""))
                    and 1 <= age <= int(config["ttl_calendar_days"])
                    and bandwidth_continues
                    and holds_upper
                    and macd_crossover
                    and volume_increases
                    and above_sma50
                    and coverage >= float(t2["minimum_component_coverage"])
                    and quality >= float(t2["quality_threshold"])
                )
                return {
                    "setup": "T2_CONFIRMATION" if gates else None,
                    "timing_state": "T2_CONFIRM_75_SHADOW" if gates else "T1_ACTIVE_WAIT_T2",
                    "quality": quality,
                    "coverage": coverage,
                    "event_id": state.get("event_id"),
                    "age_days": age,
                    "reason": None if gates else "T2_GATES_NOT_MET",
                    "state_action": "CONSUME" if gates else "KEEP",
                    "components": t2_components,
                }

    compression = finite(metrics.get("compression_score"))
    squeeze_ok = int(metrics.get("squeeze_consecutive_days") or 0) >= int(config["squeeze"]["minimum_consecutive_days"])
    breakout = metrics["previous_close"] <= metrics["previous_bb_upper"] and metrics["close"] > metrics["bb_upper"]
    bandwidth_expands = metrics["bandwidth"] > metrics["previous_bandwidth"]
    volume_increases = bool(
        metrics["volume_increase_ratio"] is not None
        and metrics["volume_increase_ratio"] > float(t1["volume_increase_ratio_min"])
    )
    macd_below = metrics["macd_hist"] < 0
    above_sma50 = metrics["close"] > metrics["sma50"]
    t1_components: dict[str, float | None] = {
        "seven_day_squeeze": compression,
        "upper_band_breakout_and_expansion": 100.0 if breakout and bandwidth_expands else 0.0,
        "volume_increases": 100.0 if volume_increases else 0.0,
        "macd_below_signal": 100.0 if macd_below else 0.0,
        "above_sma50": 100.0 if above_sma50 else 0.0,
    }
    quality, coverage = _weighted_quality(t1_components, t1["components"])
    gates = (
        baseline_ok
        and compression is not None
        and squeeze_ok
        and breakout
        and bandwidth_expands
        and volume_increases
        and macd_below
        and above_sma50
        and coverage >= float(t1["minimum_component_coverage"])
        and quality >= float(t1["quality_threshold"])
    )
    if not gates:
        return {"setup": None, "timing_state": "NO_T1_T2", "quality": quality, "coverage": coverage, "event_id": None, "age_days": None, "reason": "T1_GATES_NOT_MET", "state_action": "EXPIRE" if state_invalidated else "NONE", "components": t1_components}
    timing_state = "T1_STARTER_25_SHADOW" if quality >= float(t1["starter_threshold"]) else "T1_WATCH_SHADOW"
    event_id = _event_id(asset_id, bar_time)
    return {
        "setup": "T1",
        "timing_state": timing_state,
        "quality": quality,
        "coverage": coverage,
        "event_id": event_id,
        "age_days": 0,
        "reason": None,
        "state_action": "CREATE",
        "components": t1_components,
        "state_update": {
            "detected_at": bar_time,
            "bar_ts": int(metrics["bar_ts"]),
            "event_id": event_id,
            "bandwidth": metrics["bandwidth"],
            "breakout_price": metrics["close"],
            "atr_proxy": metrics["atr_proxy"],
            "return_30d_pct": metrics.get("return_30d_pct"),
            "quality": quality,
            "coverage": coverage,
            "timing_state": timing_state,
            "baseline_eligible_at_t1": True,
            "rule_version": config["version"],
        },
    }


def load_timing_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = load_json(path)
    except (OSError, ValueError, TypeError):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)} if isinstance(payload, dict) else {}


def apply_timing_overlay(
    rows: list[dict[str, Any]], snapshot: dict[str, Any], governance: dict[str, Any], root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = governance["t1_t2"]
    state_path = root / str(config["state_path"])
    state = load_timing_state(state_path)
    as_of = parse_utc(snapshot["as_of"])
    output: list[dict[str, Any]] = []
    counts = {"t1": 0, "t2": 0, "active_wait": 0, "no_signal": 0, "history_missing": 0}
    for original in rows:
        row = dict(original)
        row.update({
            "timing_setup": None,
            "timing_state": "NOT_APPLICABLE_CT" if row["horizon"] != "TCT" else "NO_T1_T2",
            "timing_quality": None,
            "timing_coverage": 0.0,
            "timing_event_id": None,
            "timing_age_days": None,
            "timing_reason": None,
            "timing_score_influence": 0.0,
            "timing_live_execution": False,
            "t1_research_fraction": float(config["research_entry_variant"]["t1_fraction"]),
            "t2_research_fraction": float(config["research_entry_variant"]["t2_fraction"]),
        })
        if row["horizon"] != "TCT":
            output.append(row)
            continue
        asset = snapshot.get("assets", {}).get(row["asset_id"], {})
        metrics = compute_timing_metrics(asset.get("history", []), as_of, config)
        if metrics is None:
            row["timing_state"] = "WAIT_T1_T2_HISTORY"
            row["timing_reason"] = "HISTORY_TOO_SHORT_OR_INCOMPLETE"
            counts["history_missing"] += 1
            output.append(row)
            continue
        baseline_ok = row["state"] in {"READY_FOR_REVIEW", "STRONG_REVIEW"}
        result = evaluate_timing(row["asset_id"], metrics, state.get(row["asset_id"]), baseline_ok, config)
        row.update({
            "timing_setup": result.get("setup"),
            "timing_state": result["timing_state"],
            "timing_quality": round(float(result["quality"]), 4) if result.get("quality") is not None else None,
            "timing_coverage": round(float(result.get("coverage", 0.0)), 4),
            "timing_event_id": result.get("event_id"),
            "timing_age_days": result.get("age_days"),
            "timing_reason": result.get("reason"),
        })
        if result.get("state_action") == "CREATE":
            state[row["asset_id"]] = dict(result["state_update"])
            counts["t1"] += 1
        elif result.get("state_action") == "CONSUME":
            state.pop(row["asset_id"], None)
            counts["t2"] += 1
        elif result.get("state_action") == "EXPIRE":
            state.pop(row["asset_id"], None)
            counts["no_signal"] += 1
        elif result["timing_state"] == "T1_ACTIVE_WAIT_T2":
            counts["active_wait"] += 1
        else:
            counts["no_signal"] += 1
        output.append(row)
    write_json_atomic(state_path, state)
    return output, {
        "version": config["version"],
        "state_rule_version_required": True,
        "scope": config["scope"],
        "state_path": str(state_path),
        "active_state_records": len(state),
        "counts": counts,
        "same_bar_can_confirm_t2": False,
        "score_influence": 0.0,
        "real_orders_enabled": False,
    }
