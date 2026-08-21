from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Callable
from zoneinfo import ZoneInfo
import json
import os

import pandas as pd

from v182.reporting import tct_next_session_catalyst_run as legacy
from v182.sources.global_market_snapshot import GlobalMarketSnapshot, fetch_global_market_snapshot


ScoreFn = Callable[..., dict]
SelectFn = Callable[[pd.DataFrame, dict], pd.DataFrame]
WindowFn = Callable[..., tuple[datetime, datetime]]
InferPhaseFn = Callable[[datetime, dict], str]
FetchNewsFn = Callable[..., dict]
MarketFn = Callable[..., GlobalMarketSnapshot]
AndroidFn = Callable[[pd.DataFrame, str, str, dict, dict], str]


def _default_android(frame: pd.DataFrame, phase: str, generated_at: str, market: dict, audit: dict) -> str:
    return legacy._android_summary(frame, phase, generated_at, market)


def run_engine(
    *,
    root: Path,
    config_filename: str,
    version: str,
    catalyst_window_fn: WindowFn,
    infer_phase_fn: InferPhaseFn,
    select_candidates_fn: SelectFn,
    score_candidate_fn: ScoreFn,
    fetch_news_fn: FetchNewsFn,
    phase: str | None = None,
    now: datetime | None = None,
    market_fn: MarketFn = fetch_global_market_snapshot,
    output_filename: str,
    audit_filename: str,
    android_filename: str = "ANDROID_TCT_NEXT_SESSION_CATALYST.md",
    android_summary_fn: AndroidFn = _default_android,
) -> dict:
    """Run one TCT catalyst snapshot with explicit dependencies.

    No module globals are changed. Versioned runners supply their config,
    feature functions and news source explicitly, eliminating cross-version
    state pollution when several runners are imported in the same process.
    """
    cfg = json.loads((root / "config" / config_filename).read_text(encoding="utf-8"))
    started = monotonic()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_phase = str(phase or os.environ.get("TCT_CATALYST_PHASE") or infer_phase_fn(current, cfg)).upper()
    if selected_phase not in set(cfg["data_policy"]["snapshot_phases"]):
        raise ValueError(f"Unsupported TCT catalyst phase: {selected_phase}")

    seed_path = root / cfg["state"]["context_seed_path"]
    seed = legacy._read_csv(seed_path)
    seed_anchor = legacy._seed_anchor_date(seed)
    seed_staleness = legacy._seed_staleness_days(seed_anchor, current, cfg)
    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    generated_at = current.isoformat()
    window_start, window_end = catalyst_window_fn(selected_phase, current, cfg, anchor_date=seed_anchor)

    errors: list[str] = []
    stale_limit = int(cfg["data_policy"].get("max_preopen_seed_staleness_calendar_days", 5))
    stale_preopen = bool(selected_phase == "PREOPEN" and seed_staleness is not None and seed_staleness > stale_limit)
    local_day = current.astimezone(ZoneInfo(str(cfg["data_policy"].get("timezone", "Europe/Paris")))).date().isoformat()
    stale_postmarket = bool(selected_phase == "POSTMARKET" and seed_anchor and seed_anchor != local_day)
    candidates = pd.DataFrame()
    output = pd.DataFrame()
    news_metrics: dict = {}

    if seed.empty:
        market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_SEED", ())
        errors.append("TCT_DAILY_CONTEXT_SEED_MISSING")
    elif stale_preopen:
        market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_STALE_PREOPEN_SEED", ())
        errors.append("PREOPEN_SEED_TOO_STALE")
    elif stale_postmarket:
        market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_COMPLETED_EU_SESSION", ())
        errors.append("POSTMARKET_SEED_NOT_CURRENT_SESSION")
    else:
        candidates = select_candidates_fn(seed, cfg)
        if candidates.empty:
            market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_CANDIDATES", ())
        else:
            budget_cfg = cfg.get("runtime_budget", {})
            phase_budget = float(
                budget_cfg.get("preopen_seconds" if selected_phase == "PREOPEN" else "postmarket_seconds", 0) or 0
            )
            news_budget = None
            if phase_budget > 0:
                news_budget = max(1.0, phase_budget * float(budget_cfg.get("news_budget_fraction", 0.80)))
            news = fetch_news_fn(
                candidates.to_dict("records"),
                start_utc=window_start,
                end_utc=window_end,
                phase=selected_phase,
                cfg=cfg,
                budget_seconds=news_budget,
            )
            news_metrics = dict(getattr(news, "metrics", {}) or {})
            market = market_fn(cfg, phase=selected_phase)
            rows: list[dict] = []
            for _, candidate in candidates.iterrows():
                isin = str(candidate.get("isin") or "")
                scored = score_candidate_fn(candidate, news.get(isin), market, phase=selected_phase, cfg=cfg)
                flattened = legacy._flatten_candidate(
                    candidate,
                    scored,
                    generated_at=generated_at,
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat(),
                )
                for extra in ("candidate_rank_reason", "candidate_priority_score", "candidate_rank"):
                    if extra in candidate.index:
                        flattened[extra] = candidate.get(extra)
                rows.append(flattened)
            output = pd.DataFrame(rows)
            if not output.empty:
                output["_move"] = pd.to_numeric(output.get("movement_potential_score"), errors="coerce")
                output = output.sort_values("_move", ascending=False, na_position="last").drop(columns=["_move"], errors="ignore")

    elapsed = monotonic() - started
    budget_cfg = cfg.get("runtime_budget", {})
    phase_budget = float(
        budget_cfg.get("preopen_seconds" if selected_phase == "PREOPEN" else "postmarket_seconds", 0) or 0
    )
    budget_exceeded = bool(phase_budget > 0 and elapsed > phase_budget)
    if budget_exceeded:
        errors.append("RUNTIME_BUDGET_EXCEEDED")
        if not output.empty and bool(budget_cfg.get("fail_closed_on_budget_exhaustion", True)):
            if "data_quality_state" in output.columns:
                output["data_quality_state"] = output["data_quality_state"].astype(str).where(
                    output["data_quality_state"].astype(str).str.startswith("INSUFFICIENT"),
                    "RUNTIME_BUDGET_DEGRADED",
                )

    output_path = outdir / output_filename
    legacy._write_csv(output, output_path)
    ledger_path = root / cfg["state"]["catalyst_ledger_path"]
    ledger, new_ledger_rows, newly_labeled = legacy._append_ledger(output, ledger_path, seed=seed, generated_at=generated_at)

    market_payload = asdict(market)
    high_potential = 0
    up = down = volatility = degraded = conflicts = 0
    if not output.empty:
        potential = pd.to_numeric(output.get("movement_potential_score"), errors="coerce")
        high_potential = int((potential >= float(cfg["thresholds"]["high_movement_potential"])).sum())
        states = output.get("catalyst_state", pd.Series(dtype=str)).astype(str)
        up = int((states == "UP_CATALYST_SHADOW").sum())
        down = int((states == "DOWN_CATALYST_SHADOW").sum())
        volatility = int((states == "VOLATILITY_ALERT_SHADOW").sum())
        degraded = int((states == "DATA_DEGRADED_SHADOW").sum())
        conflicts = int((states == "NEWS_CONFLICT_SHADOW").sum())

    payload = {
        "status": "SUCCESS_SHADOW" if not errors else "SUCCESS_SHADOW_WITH_WARNINGS",
        "version": version,
        "phase": selected_phase,
        "generated_at_utc": generated_at,
        "window_start_utc": window_start.isoformat(),
        "window_end_utc": window_end.isoformat(),
        "seed_anchor_date": seed_anchor,
        "seed_staleness_calendar_days": seed_staleness,
        "seed_rows": int(len(seed)),
        "candidate_rows": int(len(candidates)),
        "output_rows": int(len(output)),
        "high_movement_potential": high_potential,
        "up_catalysts": up,
        "down_catalysts": down,
        "volatility_alerts": volatility,
        "data_degraded_rows": degraded,
        "news_conflicts": conflicts,
        "ledger_rows": int(len(ledger)),
        "new_ledger_rows": int(new_ledger_rows),
        "legacy_preopen_outcomes_labeled_post_close": int(newly_labeled),
        "global_market": market_payload,
        "news_batch_metrics": news_metrics,
        "runtime_seconds": round(float(elapsed), 4),
        "runtime_budget_seconds": phase_budget or None,
        "runtime_budget_exceeded": budget_exceeded,
        "dependency_injection": True,
        "module_global_mutation": False,
        "individual_pea_extended_hours_quotes_used": False,
        "intraday_bars_used": False,
        "five_minute_data_used": False,
        "continuous_monitoring_used": False,
        "snapshot_count_design_per_day": 2,
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_influence": 0.0,
        "stop_loss_influence": 0.0,
        "ct_influence": 0.0,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "errors": errors + list(market.errors),
        "outputs": {
            "ranked_candidates": str(output_path.relative_to(root)),
            "ledger": str(ledger_path.relative_to(root)),
            "android": str((mobile / android_filename).relative_to(root)),
        },
    }

    android_path = mobile / android_filename
    android_path.write_text(android_summary_fn(output, selected_phase, generated_at, market_payload, payload), encoding="utf-8")
    audit_path = auditdir / audit_filename
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload
