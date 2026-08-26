from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

from v182.decision.tct_timing_exact_v24_1_7 import _extract_histories
from v182.features.tct_v24_1_7_exact import compute_technical_indicators

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/CI_ENTRY_CONFIDENCE_V22_2.json")
STATE = Path("state/ci_entry_watch/V22_2_CANDIDATE_STATE.csv")
STATE_CACHE = Path("state/provenance/CI_ENTRY_WATCH_V22_2_STATE.csv")
OUTPUT = Path("outputs/committee_master/CI_ENTRY_CONFIDENCE_V22_2.csv")
MOBILE = Path("outputs/mobile/CI_ENTRY_WATCH_V22_2.csv")
AUDIT = Path("outputs/audit/CI_ENTRY_CONFIDENCE_V22_2.json")


def _num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _bool(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "oui", "pass", "confirmed"}


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def _first_text(*values) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path.exists()), None)


def _load_metadata(root: Path) -> pd.DataFrame:
    parts = []
    for asset, candidates in (
        ("ACTION", [root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ACTIONS_MASTER.csv"]),
        ("ETF", [root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ETF_MASTER.csv"]),
    ):
        path = _first_existing(candidates)
        if path is None:
            continue
        frame = _read_csv(path)
        if frame.empty or "isin" not in frame.columns:
            continue
        frame = frame.copy()
        frame["asset_class"] = asset
        parts.append(frame)
    return pd.concat(parts, ignore_index=True, sort=False) if parts else pd.DataFrame()


def _metadata_maps(meta: pd.DataFrame) -> dict[tuple[str, str], dict]:
    by_key: dict[tuple[str, str], dict] = {}
    if meta.empty:
        return by_key
    for _, row in meta.iterrows():
        asset = _text(row.get("asset_class")).upper()
        isin = _text(row.get("isin"))
        if asset and isin:
            by_key[(asset, isin)] = row.to_dict()
    return by_key


def _ticker(row: pd.Series, meta: dict) -> str:
    return _first_text(meta.get("yahoo_ticker"), meta.get("ticker"), row.get("yahoo_ticker"), row.get("ticker"))


def _load_candidate_histories(root: Path, candidates: pd.DataFrame, meta_map: dict[tuple[str, str], dict]) -> dict[str, pd.DataFrame]:
    tickers_by_asset: dict[str, set[str]] = {"ACTION": set(), "ETF": set()}
    for _, row in candidates.iterrows():
        asset = _text(row.get("asset_class")).upper()
        isin = _text(row.get("isin"))
        ticker = _ticker(row, meta_map.get((asset, isin), {}))
        if asset in tickers_by_asset and ticker:
            tickers_by_asset[asset].add(ticker)
    histories: dict[str, pd.DataFrame] = {}
    for asset, tickers in tickers_by_asset.items():
        if not tickers:
            continue
        cache_dir = root / "data/cache" / ("actions" if asset == "ACTION" else "etf")
        histories.update(_extract_histories(cache_dir, tickers))
    return histories


def _technical_snapshot(history: pd.DataFrame | None, cfg: dict) -> dict:
    if history is None or history.empty:
        return {"history_status": "MISSING"}
    try:
        tech = compute_technical_indicators(history)
    except Exception as exc:
        return {"history_status": "ERROR", "history_error": f"{type(exc).__name__}:{str(exc)[:120]}"}
    if tech.empty or len(tech) < 205:
        return {"history_status": "TOO_SHORT_FOR_SMA200", "sessions": int(len(tech))}
    close = pd.to_numeric(tech.get("close"), errors="coerce")
    if close is None or close.dropna().empty:
        return {"history_status": "NO_CLOSE"}
    c = _num(close.iloc[-1])
    prev = _num(close.iloc[-2])
    sma20_series = close.rolling(20, min_periods=20).mean()
    sma50_series = close.rolling(50, min_periods=50).mean()
    sma200_series = close.rolling(200, min_periods=200).mean()
    sma20 = _num(sma20_series.iloc[-1])
    sma50 = _num(sma50_series.iloc[-1])
    sma200 = _num(sma200_series.iloc[-1])
    prev_sma20 = _num(sma20_series.iloc[-2])
    ret20 = _num(close.pct_change(20).iloc[-1])
    ret5_series = close.pct_change(5)
    ret5_now = _num(ret5_series.iloc[-1])
    ret5_prev = _num(ret5_series.iloc[-6]) if len(ret5_series) >= 11 else None
    accel = None if ret5_now is None or ret5_prev is None else ret5_now - ret5_prev
    returns = close.pct_change()
    vol20 = _num(returns.rolling(20, min_periods=20).std().iloc[-1])
    macd = pd.to_numeric(tech.get("macd"), errors="coerce") if "macd" in tech else None
    signal = pd.to_numeric(tech.get("macd_signal"), errors="coerce") if "macd_signal" in tech else None
    macd_hist = _num((macd - signal).iloc[-1]) if macd is not None and signal is not None else None
    prev_macd_hist = _num((macd - signal).iloc[-2]) if macd is not None and signal is not None else None
    volume = pd.to_numeric(tech.get("volume"), errors="coerce") if "volume" in tech else None
    volume_ratio = None
    if volume is not None and len(volume) >= 21:
        avg = _num(volume.shift(1).rolling(20, min_periods=20).mean().iloc[-1])
        curv = _num(volume.iloc[-1])
        if avg is not None and avg > 0 and curv is not None:
            volume_ratio = curv / avg
    lookback = int(cfg["entry"].get("breakout_lookback_sessions", 20))
    prior_high = _num(close.shift(1).rolling(lookback, min_periods=lookback).max().iloc[-1])
    breakout = bool(c is not None and prior_high is not None and c > prior_high)
    reclaim20 = bool(c is not None and sma20 is not None and prev is not None and prev_sma20 is not None and c > sma20 and prev <= prev_sma20)
    dist50 = None if c is None or sma50 in {None, 0.0} else c / sma50 - 1.0
    dist200 = None if c is None or sma200 in {None, 0.0} else c / sma200 - 1.0
    overextension = False
    if dist50 is not None and vol20 is not None and vol20 > 0:
        overextension = dist50 > float(cfg["entry"].get("dynamic_overextension_volatility_multiple", 2.0)) * vol20
    return {
        "history_status": "OK",
        "sessions": int(len(tech)),
        "close": c,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "dist_sma50": dist50,
        "dist_sma200": dist200,
        "ret20": ret20,
        "momentum_acceleration": accel,
        "volatility_20d": vol20,
        "macd_hist": macd_hist,
        "macd_hist_accelerating": bool(macd_hist is not None and prev_macd_hist is not None and macd_hist >= prev_macd_hist),
        "volume_ratio_20d": volume_ratio,
        "breakout_20d": breakout,
        "reclaim_sma20": reclaim20,
        "overextension_dynamic": overextension,
    }


def _entry_state(row: pd.Series, tech: dict, cfg: dict) -> tuple[str, list[str], float]:
    horizon = _text(row.get("horizon")).upper()
    asset = _text(row.get("asset_class")).upper()
    if horizon == "TCT":
        setup = _first_text(row.get("setup"), row.get("tct_setup")).upper()
        confirmed = _bool(row.get("t2_confirmed")) or setup in {"T2", "T2_CONFIRMATION", "T2_EXACT_TIMING_CONFIRMATION"}
        if confirmed:
            return "READY_FOR_REVIEW", ["TCT_EXACT_T2_CONFIRMED"], 100.0
        return "WAIT", ["TCT_EXACT_T2_CONFIRMATION_REQUIRED"], 0.0
    if tech.get("history_status") != "OK":
        return "WAIT", [f"TECHNICAL_HISTORY_{tech.get('history_status', 'MISSING')}"], 0.0

    reasons: list[str] = []
    blockers: list[str] = []
    dist200 = _num(tech.get("dist_sma200"))
    accel = _num(tech.get("momentum_acceleration"))
    if bool(cfg["entry"].get("block_on_below_sma200", True)) and dist200 is not None and dist200 < 0:
        blockers.append("BELOW_SMA200")
    if bool(cfg["entry"].get("block_on_negative_momentum_acceleration", True)) and accel is not None and accel < 0:
        blockers.append("MOMENTUM_DECELERATION")
    if _bool(tech.get("overextension_dynamic")):
        blockers.append("DYNAMIC_OVEREXTENSION")

    if asset == "ACTION" and horizon == "CT":
        event = _bool(tech.get("breakout_20d")) or _bool(tech.get("reclaim_sma20"))
        hist = _num(tech.get("macd_hist"))
        momentum_ok = (hist is not None and hist >= 0) or _bool(tech.get("macd_hist_accelerating"))
        volume = _num(tech.get("volume_ratio_20d"))
        volume_ok = volume is not None and volume >= 1.0
        if _bool(tech.get("breakout_20d")):
            reasons.append("BREAKOUT_20D_CONFIRMED")
        if _bool(tech.get("reclaim_sma20")):
            reasons.append("SMA20_RECLAIM_CONFIRMED")
        if momentum_ok:
            reasons.append("MOMENTUM_CONFIRMATION")
        if volume_ok:
            reasons.append("VOLUME_AT_OR_ABOVE_20D_REFERENCE")
        elif volume is None:
            blockers.append("VOLUME_EVIDENCE_MISSING")
        else:
            blockers.append("VOLUME_BELOW_20D_REFERENCE")
        if blockers:
            return "WAIT", blockers + reasons, 40.0
        if event and momentum_ok and volume_ok:
            return "READY_FOR_REVIEW", reasons, 100.0
        return "WAIT", reasons + ["CT_TRIGGER_NOT_YET_CONFIRMED"], 60.0

    if horizon == "MT" and asset in {"ACTION", "ETF"}:
        close = _num(tech.get("close")); sma50 = _num(tech.get("sma50")); sma200 = _num(tech.get("sma200"))
        trend_ok = bool(close is not None and sma50 is not None and sma200 is not None and close > sma50 > sma200)
        ret20 = _num(tech.get("ret20")); hist = _num(tech.get("macd_hist"))
        momentum_ok = bool(ret20 is not None and hist is not None and ret20 > 0 and hist >= 0)
        if trend_ok:
            reasons.append("MT_CLOSE_ABOVE_SMA50_ABOVE_SMA200")
        if momentum_ok:
            reasons.append("MT_MOMENTUM_POSITIVE")
        if blockers:
            return "WAIT", blockers + reasons, 40.0
        if trend_ok and momentum_ok:
            return "READY_FOR_REVIEW", reasons + ["MT_CLOSE_CONFIRMATION"], 100.0
        return "WAIT", reasons + ["MT_CLOSE_TRIGGER_NOT_YET_CONFIRMED"], 60.0

    if blockers:
        return "WAIT", blockers, 40.0
    return "WAIT", ["HORIZON_TRIGGER_RULE_SHADOW_ONLY"], 50.0


def _provenance_score(row: pd.Series, meta: dict) -> float:
    evidence_tokens: list[str] = []
    for key in ("evidence_level", "source_evidence_level", "validation_status", "evidence_grade"):
        value = _first_text(row.get(key) if key in row.index else None, meta.get(key)).upper()
        if value:
            evidence_tokens.append(value)
    joined = "|".join(evidence_tokens)
    if "A" in evidence_tokens or "EXACT_ISIN" in joined or "ISIN_MATCHED" in joined:
        return 100.0
    if "B" in evidence_tokens or "AUTO_MATCH" in joined:
        return 80.0
    if "C" in evidence_tokens:
        return 60.0
    source_fields = sum(bool(_text(meta.get(key))) for key in ("yahoo_ticker", "provider", "official_benchmark", "name"))
    return min(40.0, source_fields * 10.0)


def _trend_score(tech: dict) -> float:
    if tech.get("history_status") != "OK":
        return 0.0
    score = 0.0
    close = _num(tech.get("close")); sma50 = _num(tech.get("sma50")); sma200 = _num(tech.get("sma200"))
    if close is not None and sma50 is not None and close > sma50:
        score += 35.0
    if close is not None and sma200 is not None and close > sma200:
        score += 25.0
    if sma50 is not None and sma200 is not None and sma50 > sma200:
        score += 20.0
    accel = _num(tech.get("momentum_acceleration")); hist = _num(tech.get("macd_hist"))
    if accel is not None and accel >= 0:
        score += 10.0
    if hist is not None and hist >= 0:
        score += 10.0
    return min(100.0, score)


def _market_sector_score(row: pd.Series, meta: dict) -> float:
    values: list[float] = []
    for key in ("sector_rotation_score", "market_regime_score", "risk_score_0_100_shadow"):
        value = row.get(key) if key in row.index else meta.get(key)
        n = _num(value)
        if n is not None:
            values.append(max(0.0, min(100.0, 100.0 - n if key == "risk_score_0_100_shadow" else n)))
    verdict = _text(row.get("risk_verdict")).upper()
    if verdict:
        mapped = {"GREEN": 100.0, "GREEN_AMBER": 80.0, "AMBER": 60.0, "ORANGE": 35.0, "RED": 10.0}.get(verdict)
        if mapped is not None:
            values.append(mapped)
    return float(np.mean(values)) if values else 0.0


def _load_state(path: Path) -> pd.DataFrame:
    frame = _read_csv(path)
    if frame.empty:
        return pd.DataFrame(columns=["asset_class", "horizon", "isin", "last_observed_date", "consecutive_observations", "last_entry_state"])
    return frame


def _stability_update(candidates: pd.DataFrame, state: pd.DataFrame, observed_date: str) -> tuple[list[int], pd.DataFrame]:
    prior = {}
    for _, row in state.iterrows():
        key = (_text(row.get("asset_class")).upper(), _text(row.get("horizon")).upper(), _text(row.get("isin")))
        prior[key] = row.to_dict()
    counts: list[int] = []
    rows = []
    for _, row in candidates.iterrows():
        key = (_text(row.get("asset_class")).upper(), _text(row.get("horizon")).upper(), _text(row.get("isin")))
        old = prior.get(key, {})
        old_date = _text(old.get("last_observed_date"))
        old_count = int(_num(old.get("consecutive_observations")) or 0)
        count = old_count if old_date == observed_date else old_count + 1
        counts.append(count)
        rows.append({"asset_class": key[0], "horizon": key[1], "isin": key[2], "last_observed_date": observed_date, "consecutive_observations": count, "last_entry_state": _text(row.get("v22_2_entry_state"))})
    return counts, pd.DataFrame(rows)


def _stability_score(count: int, cfg: dict) -> float:
    strong = int(cfg["stability"].get("strong_observations", 3))
    intermediate = int(cfg["stability"].get("intermediate_observations", 2))
    if count >= strong:
        return 100.0
    if count >= intermediate:
        return 70.0
    return 40.0


def _confidence_label(score: float, cfg: dict, entry_state: str) -> str:
    if entry_state != "READY_FOR_REVIEW":
        return "INSUFFICIENT_ENTRY_PROOF"
    if score >= float(cfg["confidence_labels"]["strong_min"]):
        return "STRONG"
    if score >= float(cfg["confidence_labels"]["intermediate_min"]):
        return "INTERMEDIATE"
    return "INSUFFICIENT"


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    decisions_path = root / "outputs/committee_master/COMMITTEE_DECISIONS.csv"
    if not decisions_path.exists():
        return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING", "version": cfg.get("version")}
    decisions = _read_csv(decisions_path)
    score_guard = decisions["score"].copy() if "score" in decisions.columns else None
    decision_guard = decisions["decision"].copy() if "decision" in decisions.columns else None
    allowed = {str(x).upper() for x in cfg.get("candidate_decisions", [])}
    horizons = {str(x).upper() for x in cfg.get("monitored_horizons", [])}
    mask = decisions["decision"].astype(str).str.upper().isin(allowed) & decisions["horizon"].astype(str).str.upper().isin(horizons)
    candidates = decisions.loc[mask].copy().reset_index(drop=True)

    meta_map = _metadata_maps(_load_metadata(root))
    histories = _load_candidate_histories(root, candidates, meta_map)
    technical_rows: list[dict] = []
    entry_states: list[str] = []
    entry_reasons: list[str] = []
    entry_scores: list[float] = []
    provenance_scores: list[float] = []
    trend_scores: list[float] = []
    context_scores: list[float] = []
    tickers: list[str] = []
    for _, row in candidates.iterrows():
        asset = _text(row.get("asset_class")).upper(); isin = _text(row.get("isin"))
        record = meta_map.get((asset, isin), {})
        ticker = _ticker(row, record)
        tickers.append(ticker)
        tech = _technical_snapshot(histories.get(ticker), cfg) if ticker else {"history_status": "TICKER_MISSING"}
        technical_rows.append(tech)
        state, reasons, timing_score = _entry_state(row, tech, cfg)
        entry_states.append(state); entry_reasons.append("|".join(reasons)); entry_scores.append(timing_score)
        provenance_scores.append(_provenance_score(row, record))
        trend_scores.append(_trend_score(tech))
        context_scores.append(_market_sector_score(row, record))

    candidates["yahoo_ticker_v22_2"] = tickers
    candidates["v22_2_entry_state"] = entry_states
    candidates["v22_2_entry_reasons"] = entry_reasons
    for field in sorted({key for tech in technical_rows for key in tech}):
        candidates[f"v22_2_{field}"] = [tech.get(field) for tech in technical_rows]
    coverage = pd.to_numeric(candidates["coverage_pct"], errors="coerce") if "coverage_pct" in candidates.columns else pd.Series(0.0, index=candidates.index)
    candidates["v22_2_component_selection_coverage"] = coverage.fillna(0.0).clip(0, 100)
    candidates["v22_2_component_provenance_quality"] = provenance_scores
    candidates["v22_2_component_entry_timing"] = entry_scores
    candidates["v22_2_component_trend_momentum"] = trend_scores
    candidates["v22_2_component_market_sector_context"] = context_scores

    observed_date = datetime.now(timezone.utc).date().isoformat()
    state_path = root / STATE
    cache_state_path = root / STATE_CACHE
    prior_state = _load_state(state_path if state_path.exists() else cache_state_path)
    counts, next_state = _stability_update(candidates, prior_state, observed_date)
    candidates["v22_2_consecutive_observations"] = counts
    candidates["v22_2_component_temporal_stability"] = [_stability_score(count, cfg) for count in counts]

    weights = cfg["confidence_weights"]
    components = {
        "selection_coverage": candidates["v22_2_component_selection_coverage"],
        "provenance_quality": candidates["v22_2_component_provenance_quality"],
        "entry_timing": candidates["v22_2_component_entry_timing"],
        "trend_momentum": candidates["v22_2_component_trend_momentum"],
        "market_sector_context": candidates["v22_2_component_market_sector_context"],
        "temporal_stability": candidates["v22_2_component_temporal_stability"],
    }
    confidence = pd.Series(0.0, index=candidates.index, dtype=float)
    for name, values in components.items():
        confidence = confidence + pd.to_numeric(values, errors="coerce").fillna(0.0) * float(weights[name])
    candidates["CI_CONFIDENCE_SCORE_0_100"] = confidence.round(2)
    candidates["CI_CONFIDENCE_LEVEL"] = [_confidence_label(float(score), cfg, state) for score, state in zip(candidates["CI_CONFIDENCE_SCORE_0_100"], candidates["v22_2_entry_state"])]
    candidates["v22_2_real_order"] = False
    candidates["v22_2_selection_score_influence"] = 0.0
    candidates["v22_2_selection_decision_influence"] = 0.0
    candidates["v22_2_generated_at_utc"] = datetime.now(timezone.utc).isoformat()

    if score_guard is not None and not score_guard.equals(decisions["score"]):
        raise RuntimeError("V22_2_SELECTION_SCORE_MUTATION_FORBIDDEN")
    if decision_guard is not None and not decision_guard.equals(decisions["decision"]):
        raise RuntimeError("V22_2_SELECTION_DECISION_MUTATION_FORBIDDEN")

    for path in (root / OUTPUT, root / MOBILE, state_path, cache_state_path, root / AUDIT):
        path.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(root / OUTPUT, sep=";", index=False, encoding="utf-8-sig")
    mobile_cols = [c for c in ["asset_class", "horizon", "isin", "name", "score", "coverage_pct", "decision", "v22_2_entry_state", "v22_2_entry_reasons", "CI_CONFIDENCE_SCORE_0_100", "CI_CONFIDENCE_LEVEL", "v22_2_close", "v22_2_sma20", "v22_2_sma50", "v22_2_sma200", "v22_2_breakout_20d", "v22_2_reclaim_sma20", "v22_2_consecutive_observations"] if c in candidates.columns]
    candidates[mobile_cols].to_csv(root / MOBILE, sep=";", index=False, encoding="utf-8-sig")
    next_state.to_csv(state_path, sep=";", index=False, encoding="utf-8-sig")
    next_state.to_csv(cache_state_path, sep=";", index=False, encoding="utf-8-sig")

    payload = {
        "status": "SUCCESS",
        "version": cfg.get("version"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_rows": int(len(candidates)),
        "ready_for_review": int((candidates["v22_2_entry_state"] == "READY_FOR_REVIEW").sum()) if not candidates.empty else 0,
        "wait": int((candidates["v22_2_entry_state"] == "WAIT").sum()) if not candidates.empty else 0,
        "strong_confidence": int((candidates["CI_CONFIDENCE_LEVEL"] == "STRONG").sum()) if not candidates.empty else 0,
        "intermediate_confidence": int((candidates["CI_CONFIDENCE_LEVEL"] == "INTERMEDIATE").sum()) if not candidates.empty else 0,
        "history_loaded_tickers": int(len(histories)),
        "state_rows": int(len(next_state)),
        "selection_score_changed": False,
        "selection_decision_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "real_orders_enabled": False,
        "shadow_until_pit_oos_validation": True,
        "outputs": {"full": str(OUTPUT), "mobile": str(MOBILE), "state": str(STATE), "cached_state": str(STATE_CACHE)},
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    payload = run(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload.get("status") == "SUCCESS" else 2)


if __name__ == "__main__":
    main()
