from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import os

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


def _append_ledger(frame: pd.DataFrame, path: Path) -> tuple[pd.DataFrame, int]:
    """Persist the first PREOPEN/POSTMARKET snapshot per candidate and day.

    A manual rerun may update the current report, but must not rewrite the first
    PIT observation used later for validation.
    """
    existing = _read_csv(path)
    if frame.empty:
        return existing, 0
    before = len(existing)
    combined = pd.concat([existing, frame], ignore_index=True, sort=False) if not existing.empty else frame.copy()
    if "snapshot_key" in combined.columns:
        combined = combined.drop_duplicates("snapshot_key", keep="first")
    sort_cols = [c for c in ["snapshot_generated_at_utc", "movement_potential_score", "isin"] if c in combined.columns]
    if sort_cols:
        combined = combined.sort_values(sort_cols, ascending=[True, False, True][: len(sort_cols)])
    _write_csv(combined, path)
    return combined, max(0, len(combined) - before)


def _flatten_candidate(row: pd.Series, scored: dict, *, generated_at: str, window_start: str, window_end: str) -> dict:
    keep = [
        "isin",
        "name",
        "yahoo_ticker",
        "as_of_date",
        "source_tct_decision",
        "source_tct_setup",
        "source_t1_quality",
        "source_t2_quality",
        "entry_state",
        "entry_score",
        "entry_confirmation_count",
        "exit_state",
        "exit_risk_score",
        "atr14_pct",
        "range_expansion",
        "sector_yf",
        "industry_yf",
        "country_yf",
        "market_cap",
        "days_to_earnings",
        "earnings_within_7d_flag",
        "next_earnings_timestamp_yf",
        "news_catalyst_score",
        "funnel_instrument_news_score",
        "funnel_sector_news_score",
        "funnel_global_news_score",
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
    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    generated_at = current.isoformat()
    window_start, window_end = catalyst_window(selected_phase, current, cfg)

    errors: list[str] = []
    if seed.empty:
        candidates = pd.DataFrame()
        output = pd.DataFrame()
        market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_SEED", ())
        errors.append("TCT_DAILY_CONTEXT_SEED_MISSING")
    else:
        candidates = select_catalyst_candidates(seed, cfg)
        if candidates.empty:
            output = pd.DataFrame()
            market = GlobalMarketSnapshot(None, None, {}, {}, "NOT_FETCHED_NO_CANDIDATES", ())
        else:
            news = fetch_candidate_news(
                candidates.to_dict("records"),
                start_utc=window_start,
                end_utc=window_end,
                phase=selected_phase,
                cfg=cfg,
            )
            market = fetch_global_market_snapshot(cfg, phase=selected_phase)
            rows: list[dict] = []
            for _, candidate in candidates.iterrows():
                isin = str(candidate.get("isin") or "")
                scored = score_candidate(candidate, news.get(isin), market, phase=selected_phase, cfg=cfg)
                rows.append(
                    _flatten_candidate(
                        candidate,
                        scored,
                        generated_at=generated_at,
                        window_start=window_start.isoformat(),
                        window_end=window_end.isoformat(),
                    )
                )
            output = pd.DataFrame(rows)
            if not output.empty:
                output["_move"] = pd.to_numeric(output["movement_potential_score"], errors="coerce")
                output = output.sort_values("_move", ascending=False).drop(columns=["_move"], errors="ignore")

    output_path = outdir / "TCT_NEXT_SESSION_CATALYST_V24_4_0.csv"
    _write_csv(output, output_path)
    ledger_path = root / cfg["state"]["catalyst_ledger_path"]
    ledger, new_ledger_rows = _append_ledger(output, ledger_path)

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
        "seed_rows": int(len(seed)),
        "candidate_rows": int(len(candidates)),
        "output_rows": int(len(output)),
        "high_movement_potential": high_potential,
        "up_catalysts": up,
        "down_catalysts": down,
        "volatility_alerts": volatility,
        "ledger_rows": int(len(ledger)),
        "new_ledger_rows": int(new_ledger_rows),
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
