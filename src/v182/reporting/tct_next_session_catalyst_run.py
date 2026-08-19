from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os

import numpy as np
import pandas as pd

from v182.features.tct_catalyst_context_v24_4 import (
    VERSION,
    catalyst_window,
    infer_phase,
    score_candidate,
    select_catalyst_candidates,
)
from v182.sources.global_market_snapshot import GlobalMarketSnapshot, fetch_global_market_snapshot
from v182.sources.tct_catalyst_news import fetch_candidate_news


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _seed_anchor_date(seed: pd.DataFrame) -> str | None:
    if seed is None or seed.empty or "as_of_date" not in seed.columns:
        return None
    parsed = pd.to_datetime(seed["as_of_date"], errors="coerce").dropna()
    if parsed.empty:
        return None
    return pd.Timestamp(parsed.max()).date().isoformat()


def _seed_staleness_days(seed_anchor: str | None, now: datetime, cfg: dict) -> int | None:
    if not seed_anchor:
        return None
    try:
        anchor_day = pd.Timestamp(seed_anchor).date()
    except (TypeError, ValueError):
        return None
    tz = ZoneInfo(str(cfg["data_policy"].get("timezone", "Europe/Paris")))
    local_day = now.astimezone(tz).date()
    return int((local_day - anchor_day).days)


def _label_preopen_outcomes(ledger: pd.DataFrame, seed: pd.DataFrame, generated_at: str) -> tuple[pd.DataFrame, int]:
    """Label prior PREOPEN forecasts only after a later completed daily seed exists."""
    if ledger.empty or seed.empty or "reference_close" not in seed.columns:
        return ledger, 0
    out = ledger.copy()
    if "realized_close_to_close_return_pct" not in out.columns:
        out["realized_close_to_close_return_pct"] = np.nan
        out["realized_abs_return_pct"] = np.nan
        out["realized_direction_hit"] = np.nan
        out["outcome_as_of_date"] = pd.NA
        out["outcome_labeled_at_utc"] = pd.NA
        out["realized_abs_move_rank_within_snapshot"] = np.nan

    current = seed[[c for c in ["isin", "as_of_date", "reference_close"] if c in seed.columns]].copy()
    if len(current.columns) < 3:
        return out, 0
    current["isin"] = current["isin"].astype(str)
    current["reference_close"] = pd.to_numeric(current["reference_close"], errors="coerce")
    current = current.dropna(subset=["reference_close"]).drop_duplicates("isin")
    current_map = current.set_index("isin").to_dict("index")

    labeled_indices: list[int] = []
    for idx, row in out.iterrows():
        if str(row.get("phase") or "") != "PREOPEN":
            continue
        if pd.notna(row.get("realized_close_to_close_return_pct")):
            continue
        isin = str(row.get("isin") or "")
        latest = current_map.get(isin)
        if not latest:
            continue
        source_date = str(row.get("as_of_date") or "")[:10]
        outcome_date = str(latest.get("as_of_date") or "")[:10]
        if not source_date or not outcome_date or outcome_date <= source_date:
            continue
        before = pd.to_numeric(pd.Series([row.get("reference_close")]), errors="coerce").iloc[0]
        after = float(latest["reference_close"])
        if pd.isna(before) or float(before) <= 0 or after <= 0:
            continue
        realized = (after / float(before) - 1.0) * 100.0
        bias = pd.to_numeric(pd.Series([row.get("direction_bias_score")]), errors="coerce").iloc[0]
        hit = np.nan
        if not pd.isna(bias) and abs(float(bias)) >= 25.0 and realized != 0:
            hit = 1.0 if np.sign(float(bias)) == np.sign(realized) else 0.0
        out.at[idx, "realized_close_to_close_return_pct"] = round(realized, 6)
        out.at[idx, "realized_abs_return_pct"] = round(abs(realized), 6)
        out.at[idx, "realized_direction_hit"] = hit
        out.at[idx, "outcome_as_of_date"] = outcome_date
        out.at[idx, "outcome_labeled_at_utc"] = generated_at
        labeled_indices.append(idx)

    if labeled_indices:
        labeled = out.loc[labeled_indices].copy()
        for _, group in labeled.groupby("snapshot_generated_at_utc", dropna=False):
            ranks = pd.to_numeric(group["realized_abs_return_pct"], errors="coerce").rank(method="min", ascending=False)
            for idx, rank in ranks.items():
                out.at[idx, "realized_abs_move_rank_within_snapshot"] = float(rank)
    return out, len(labeled_indices)


def _append_ledger(frame: pd.DataFrame, path: Path, *, seed: pd.DataFrame, generated_at: str) -> tuple[pd.DataFrame, int, int]:
    """Preserve first PIT snapshot and label older PREOPEN rows only post-close."""
    existing = _read_csv(path)
    existing, labeled = _label_preopen_outcomes(existing, seed, generated_at)
    if frame.empty:
        _write_csv(existing, path)
        return existing, 0, labeled
    before = len(existing)
    combined = pd.concat([existing, frame], ignore_index=True, sort=False) if not existing.empty else frame.copy()
    if "snapshot_key" in combined.columns:
        combined = combined.drop_duplicates("snapshot_key", keep="first")
    sort_cols = [c for c in ["snapshot_generated_at_utc", "movement_potential_score", "isin"] if c in combined.columns]
    if sort_cols:
        combined = combined.sort_values(sort_cols, ascending=[True, False, True][: len(sort_cols)])
    _write_csv(combined, path)
    return combined, max(0, len(combined) - before), labeled


def _flatten_candidate(row: pd.Series, scored: dict, *, generated_at: str, window_start: str, window_end: str) -> dict:
    keep = [
        "isin", "name", "yahoo_ticker", "as_of_date", "reference_close",
        "source_tct_decision", "source_tct_setup", "source_t1_quality", "source_t2_quality",
        "entry_state", "entry_score", "entry_confirmation_count", "exit_state", "exit_risk_score",
        "atr14_pct", "range_expansion", "sector_yf", "industry_yf", "country_yf", "market_cap",
        "days_to_earnings", "earnings_within_7d_flag", "next_earnings_timestamp_yf",
        "news_catalyst_score", "funnel_instrument_news_score", "funnel_sector_news_score", "funnel_global_news_score",
    ]
    base = {key: row.get(key) for key in keep if key in row.index}
    phase = str(scored.get("phase") or "")
    session_key = str(window_end)[:10]
    isin = str(row.get("isin") or "")
    return {
        **base,
        **scored,
        "snapshot_generated_at_utc": generated_at,
        "snapshot_window_start_utc": window_start,
        "snapshot_window_end_utc": window_end,
        "snapshot_key": f"{session_key}|{phase}|{isin}",
        "real_orders_enabled": False,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "holdout_opened": False,
    }


def _android_summary(frame: pd.DataFrame, phase: str, generated_at: str, market: dict) -> str:
    lines = [
        f"# TCT V24.4 — {phase} Next-Session Catalysts SHADOW",
        "",
        f"Généré UTC : {generated_at}",
        "Objectif : anticiper les mouvements importants de la prochaine séance, sans day trading.",
        "Aucune cotation extended-hours individuelle des actions PEA. Aucun 1m/5m. Influence production = 0.",
        "",
        f"Contexte global : risk-on {market.get('risk_on_score', 'N/A')} | choc {market.get('shock_magnitude_score', 'N/A')}",
        "",
    ]
    if frame.empty:
        lines.append("Aucun candidat TCT disponible pour ce snapshot.")
        return "\n".join(lines) + "\n"

    work = frame.copy()
    work["_move"] = pd.to_numeric(work.get("movement_potential_score"), errors="coerce")
    work = work.sort_values("_move", ascending=False).head(15)
    for _, row in work.iterrows():
        move = row.get("_move")
        direction = pd.to_numeric(pd.Series([row.get("direction_bias_score")]), errors="coerce").iloc[0]
        move_txt = "N/A" if pd.isna(move) else f"{float(move):.1f}"
        direction_txt = "N/A" if pd.isna(direction) else f"{float(direction):+.1f}"
        lines.append(
            f"- **{row.get('name') or row.get('isin')}** — {row.get('catalyst_state')} — potentiel {move_txt} — biais {direction_txt}"
        )
        events = str(row.get("news_event_types") or "").strip()
        if events and events.lower() != "nan":
            lines.append(f"  - Catalyseurs : {events}")
        headlines = str(row.get("news_top_headlines") or "").strip()
        if headlines and headlines.lower() != "nan":
            first = headlines.split(" || ", 1)[0]
            lines.append(f"  - News principale : {first[:220]}")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT, *, phase: str | None = None, now: datetime | None = None) -> dict:
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_phase = str(phase or os.environ.get("TCT_CATALYST_PHASE") or infer_phase(current, cfg)).upper()
    if selected_phase not in set(cfg["data_policy"]["snapshot_phases"]):
        raise ValueError(f"Unsupported TCT catalyst phase: {selected_phase}")

    seed_path = root / cfg["state"]["context_seed_path"]
    seed = _read_csv(seed_path)
    seed_anchor = _seed_anchor_date(seed)
    seed_staleness = _seed_staleness_days(seed_anchor, current, cfg)
    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    generated_at = current.isoformat()
    window_start, window_end = catalyst_window(selected_phase, current, cfg, anchor_date=seed_anchor)

    errors: list[str] = []
    stale_limit = int(cfg["data_policy"].get("max_preopen_seed_staleness_calendar_days", 5))
    stale_preopen = bool(selected_phase == "PREOPEN" and seed_staleness is not None and seed_staleness > stale_limit)
    local_day = current.astimezone(ZoneInfo(str(cfg["data_policy"].get("timezone", "Europe/Paris")))).date().isoformat()
    stale_postmarket = bool(selected_phase == "POSTMARKET" and seed_anchor and seed_anchor != local_day)

    if seed.empty:
        candidates = pd.DataFrame()
        output = pd.DataFrame()
        market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_SEED", ())
        errors.append("TCT_DAILY_CONTEXT_SEED_MISSING")
    elif stale_preopen:
        candidates = pd.DataFrame()
        output = pd.DataFrame()
        market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_STALE_PREOPEN_SEED", ())
        errors.append("PREOPEN_SEED_TOO_STALE")
    elif stale_postmarket:
        candidates = pd.DataFrame()
        output = pd.DataFrame()
        market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_COMPLETED_EU_SESSION", ())
        errors.append("POSTMARKET_SEED_NOT_CURRENT_SESSION")
    else:
        candidates = select_catalyst_candidates(seed, cfg)
        if candidates.empty:
            output = pd.DataFrame()
            market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_CANDIDATES", ())
        else:
            news = fetch_candidate_news(
                candidates.to_dict("records"), start_utc=window_start, end_utc=window_end,
                phase=selected_phase, cfg=cfg,
            )
            market = fetch_global_market_snapshot(cfg, phase=selected_phase)
            rows: list[dict] = []
            for _, candidate in candidates.iterrows():
                isin = str(candidate.get("isin") or "")
                scored = score_candidate(candidate, news.get(isin), market, phase=selected_phase, cfg=cfg)
                rows.append(
                    _flatten_candidate(
                        candidate, scored, generated_at=generated_at,
                        window_start=window_start.isoformat(), window_end=window_end.isoformat(),
                    )
                )
            output = pd.DataFrame(rows)
            if not output.empty:
                output["_move"] = pd.to_numeric(output["movement_potential_score"], errors="coerce")
                output = output.sort_values("_move", ascending=False).drop(columns=["_move"], errors="ignore")

    output_path = outdir / "TCT_NEXT_SESSION_CATALYST_V24_4_0.csv"
    _write_csv(output, output_path)
    ledger_path = root / cfg["state"]["catalyst_ledger_path"]
    ledger, new_ledger_rows, newly_labeled = _append_ledger(output, ledger_path, seed=seed, generated_at=generated_at)

    market_payload = asdict(market)
    android_path = mobile / "ANDROID_TCT_NEXT_SESSION_CATALYST.md"
    android_path.write_text(_android_summary(output, selected_phase, generated_at, market_payload), encoding="utf-8")

    high_potential = 0
    up = down = volatility = 0
    if not output.empty:
        potential = pd.to_numeric(output.get("movement_potential_score"), errors="coerce")
        high_potential = int((potential >= float(cfg["thresholds"]["high_movement_potential"])).sum())
        states = output.get("catalyst_state", pd.Series(dtype=str)).astype(str)
        up = int((states == "UP_CATALYST_SHADOW").sum())
        down = int((states == "DOWN_CATALYST_SHADOW").sum())
        volatility = int((states == "VOLATILITY_ALERT_SHADOW").sum())

    payload = {
        "status": "SUCCESS_SHADOW" if not errors else "SUCCESS_SHADOW_WITH_WARNINGS",
        "version": VERSION,
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
        "ledger_rows": int(len(ledger)),
        "new_ledger_rows": int(new_ledger_rows),
        "preopen_outcomes_labeled_post_close": int(newly_labeled),
        "global_market": market_payload,
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
            "android": str(android_path.relative_to(root)),
        },
    }
    audit_path = auditdir / "TCT_NEXT_SESSION_CATALYST_V24_4_0_AUDIT.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
