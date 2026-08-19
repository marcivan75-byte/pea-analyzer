from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json

import pandas as pd

from v182.decision.tct_intraday_shadow_v24_2 import evaluate_intraday_session
from v182.decision.tct_timing_exact_v24_1_7 import _extract_histories
from v182.features.tct_intraday_v24_2 import compute_intraday_features
from v182.sources.yfinance_bulk import download_history


ROOT = Path(__file__).resolve().parents[3]
VERSION = "TCT_V24.2.0_INTRADAY_SCALPING_SHADOW"
CACHE_LAYOUT = "PER_TICKER_V1"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _latest_daily_dates(cache_dir: Path, tickers: set[str]) -> dict[str, str]:
    histories = _extract_histories(cache_dir, tickers)
    dates: dict[str, str] = {}
    for ticker, frame in histories.items():
        if frame is None or frame.empty:
            continue
        try:
            dates[ticker] = pd.Timestamp(frame.index[-1]).date().isoformat()
        except Exception:
            continue
    return dates


def _ticker_cache_dir(cache_root: Path, ticker: str) -> Path:
    """Return a stable cache directory independent of the daily candidate set.

    yfinance_bulk intentionally fingerprints the exact requested ticker list.
    A shared cache for a changing TCT candidate set would therefore be rebuilt
    whenever that list changes, destroying locally accumulated intraday history.
    One stable one-ticker cache avoids that failure mode while preserving the
    generic yfinance cache governance for every other module.
    """
    canonical = str(ticker).strip().upper()
    digest = sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return cache_root / f"ticker_{digest}"


def _download_intraday_histories(tickers: list[str], cache_root: Path, icfg: dict) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    histories: dict[str, pd.DataFrame] = {}
    failures: list[dict] = []
    cache_root.mkdir(parents=True, exist_ok=True)
    for ticker in tickers:
        ticker_cache = _ticker_cache_dir(cache_root, ticker)
        try:
            result = download_history(
                [ticker],
                str(ticker_cache),
                period=str(icfg["bootstrap_period"]),
                interval=str(icfg["interval"]),
                batch_size=1,
                auto_adjust=True,
                include_actions=False,
            )
            extracted = _extract_histories(ticker_cache, {ticker})
            history = extracted.get(ticker)
            if history is not None and not history.empty:
                histories[ticker] = history
            else:
                failures.append(
                    {
                        "ticker": ticker,
                        "error": "INTRADAY_HISTORY_MISSING_AFTER_REFRESH",
                        "download_successful": ticker in set(result.successful),
                        "download_failed": ticker in set(result.failed),
                    }
                )
        except Exception as exc:
            failures.append(
                {
                    "ticker": ticker,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:180],
                }
            )
    return histories, failures


def _signal_ledger(snapshot: pd.DataFrame, actions: pd.DataFrame, ledger_path: Path, daily_cache: Path, cfg: dict) -> tuple[pd.DataFrame, int]:
    existing = _read_csv(ledger_path)
    if snapshot.empty or actions.empty:
        return existing, 0

    ticker_col = "yahoo_ticker" if "yahoo_ticker" in actions.columns else "ticker"
    mapping = actions[["isin", ticker_col]].copy()
    mapping["isin"] = mapping["isin"].astype(str).str.upper()
    mapping[ticker_col] = mapping[ticker_col].astype(str).str.strip()
    work = snapshot.copy()
    work["isin"] = work["isin"].astype(str).str.upper()
    work = work.merge(mapping.drop_duplicates("isin"), on="isin", how="left")
    eligible = set(cfg["signal_bridge"]["eligible_source_decisions"])
    work = work[work["decision"].astype(str).isin(eligible)].copy()
    work = work[work[ticker_col].notna() & ~work[ticker_col].astype(str).isin({"", "nan", "None"})]
    if work.empty:
        return existing, 0

    tickers = set(work[ticker_col].astype(str))
    latest_dates = _latest_daily_dates(daily_cache, tickers)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for _, row in work.iterrows():
        ticker = str(row[ticker_col]).strip()
        signal_date = latest_dates.get(ticker)
        if not signal_date:
            continue
        raw_event = row.get("t1_source_event_id")
        source_event = "" if pd.isna(raw_event) else str(raw_event).strip()
        decision = str(row.get("decision") or "")
        isin = str(row.get("isin") or "").upper()
        key = f"{isin}|{signal_date}|{decision}|{source_event}"
        rows.append(
            {
                "signal_key": key,
                "isin": isin,
                "name": str(row.get("name") or ""),
                "yahoo_ticker": ticker,
                "signal_date": signal_date,
                "source_decision": decision,
                "source_setup": str(row.get("setup") or ""),
                "source_t1_t2_event_id": source_event,
                "source_t1_quality": row.get("t1_quality_score"),
                "source_t2_quality": row.get("t2_quality_score"),
                "recorded_at_utc": now,
            }
        )
    if not rows:
        return existing, 0

    fresh = pd.DataFrame(rows)
    before = len(existing)
    combined = pd.concat([existing, fresh], ignore_index=True, sort=False) if not existing.empty else fresh
    combined = combined.drop_duplicates("signal_key", keep="first").sort_values(["signal_date", "isin", "signal_key"])
    _write_csv(combined, ledger_path)
    return combined, max(0, len(combined) - before)


def _flatten_result(signal: pd.Series, result, cfg: dict) -> dict:
    raw = asdict(result)
    components = raw.pop("components", {}) or {}
    return {
        "observation_key": f"{signal['signal_key']}|{result.session_date}",
        "version": VERSION,
        "signal_key": signal["signal_key"],
        "isin": signal["isin"],
        "name": signal.get("name", ""),
        "yahoo_ticker": signal["yahoo_ticker"],
        "source_signal_date": signal["signal_date"],
        "source_decision": signal["source_decision"],
        "source_setup": signal.get("source_setup", ""),
        "source_t1_t2_event_id": signal.get("source_t1_t2_event_id", ""),
        **raw,
        **{f"component_{k}": v for k, v in components.items()},
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_execution_influence": 0.0,
        "stop_loss_influence": 0.0,
        "real_orders_enabled": False,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "holdout_locked": bool(cfg["governance"]["holdout_locked"]),
        "outcome_fields_are_research_labels_only": True,
    }


def _append_observations(path: Path, new_rows: list[dict]) -> tuple[pd.DataFrame, int]:
    existing = _read_csv(path)
    if not new_rows:
        return existing, 0
    fresh = pd.DataFrame(new_rows)
    before = len(existing)
    combined = pd.concat([existing, fresh], ignore_index=True, sort=False) if not existing.empty else fresh
    combined = combined.drop_duplicates("observation_key", keep="first")
    combined = combined.sort_values(["session_date", "isin", "source_signal_date", "observation_key"])
    _write_csv(combined, path)
    return combined, max(0, len(combined) - before)


def _eligible_completed_sessions(
    sessions: list[str],
    signal_date: str,
    cfg: dict,
    *,
    as_of_date: str | None = None,
) -> list[str]:
    """Return only completed post-signal sessions eligible for persistence.

    V24.2 is a research collector, not a live execution engine. Persisting the
    current calendar session would allow a manual intraday run to freeze a
    partial session and partial outcome labels. For PIT integrity the current
    calendar session is therefore deferred and becomes eligible on a later run.
    """
    bridge = cfg["signal_bridge"]
    minimum_lag = max(1, int(bridge.get("minimum_lag_sessions", 1)))
    max_sessions = int(bridge["max_execution_sessions_after_signal"])
    current = as_of_date or datetime.now(timezone.utc).date().isoformat()
    future = sorted({str(s) for s in sessions if str(s) > str(signal_date)})
    if bool(bridge.get("current_calendar_session_persistence_forbidden", True)):
        future = [s for s in future if s < current]
    return future[minimum_lag - 1:minimum_lag - 1 + max_sessions]


def _android_summary(observations: pd.DataFrame, generated_at: str) -> str:
    lines = [
        "# TCT V24.2.0 — Intraday / Scalping SHADOW",
        "",
        f"Généré UTC : {generated_at}",
        "Diagnostic de recherche uniquement. Influence décision/score/sizing/stop = 0. Aucun ordre réel.",
        "Anti-look-ahead : un signal T1/T2 de clôture J n'est évalué en intraday qu'à partir d'une session ultérieure.",
        "",
    ]
    if observations.empty:
        lines.append("Aucune observation intraday éligible.")
        return "\n".join(lines) + "\n"
    latest = str(observations["session_date"].astype(str).max())
    subset = observations[observations["session_date"].astype(str) == latest].copy()
    subset["_score"] = pd.to_numeric(subset.get("score"), errors="coerce")
    subset = subset.sort_values("_score", ascending=False).head(12)
    lines.extend([f"## Session {latest}", ""])
    for _, row in subset.iterrows():
        score = row.get("_score")
        score_txt = "N/A" if pd.isna(score) else f"{float(score):.1f}"
        lines.append(f"- **{row.get('name') or row.get('isin')}** — {row.get('shadow_state')} — {row.get('setup') or 'sans setup'} — score {score_txt}")
        if row.get("rejection_reason") and str(row.get("rejection_reason")) != "nan":
            lines.append(f"  - Motif : {row.get('rejection_reason')}")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").read_text(encoding="utf-8"))
    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    snapshot = _read_csv(outdir / "TCT_SHADOW_V24_1_7.csv")
    actions = _read_csv(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    ledger_path = root / cfg["signal_bridge"]["ledger_path"]
    observation_path = root / cfg["signal_bridge"]["observation_ledger_path"]

    status = "SUCCESS_SHADOW"
    errors: list[str] = []
    ledger, new_signals = _signal_ledger(snapshot, actions, ledger_path, root / "data" / "cache" / "actions", cfg)

    observation_existing = _read_csv(observation_path)
    seen = set(observation_existing.get("observation_key", pd.Series(dtype=str)).astype(str))
    new_rows: list[dict] = []
    histories_found = 0
    requested_tickers: list[str] = []
    cache_failures: list[dict] = []
    deferred_incomplete_sessions = 0

    if not ledger.empty:
        signal_dates = pd.to_datetime(ledger["signal_date"], errors="coerce")
        max_signal = signal_dates.max()
        recent_cutoff = max_signal - pd.Timedelta(days=14) if pd.notna(max_signal) else None
        active = ledger if recent_cutoff is None else ledger[signal_dates >= recent_cutoff].copy()
        requested_tickers = sorted(set(active["yahoo_ticker"].dropna().astype(str)))
        if requested_tickers:
            try:
                icfg = cfg["intraday_data"]
                cache_root = root / icfg["cache_dir"]
                histories, cache_failures = _download_intraday_histories(requested_tickers, cache_root, icfg)
                histories_found = len(histories)
                if cache_failures and not histories:
                    status = "DEGRADED_INTRADAY_DATA"
                for _, signal in active.iterrows():
                    ticker = str(signal["yahoo_ticker"])
                    history = histories.get(ticker)
                    if history is None or history.empty:
                        continue
                    try:
                        features = compute_intraday_features(history, cfg)
                    except Exception as exc:
                        errors.append(f"{ticker}:FEATURES:{type(exc).__name__}:{str(exc)[:120]}")
                        continue
                    sessions = sorted(set(features.get("session_date", pd.Series(dtype=str)).astype(str)))
                    eligible_sessions = _eligible_completed_sessions(sessions, str(signal["signal_date"]), cfg)
                    for session_date in eligible_sessions:
                        key = f"{signal['signal_key']}|{session_date}"
                        if key in seen:
                            continue
                        result = evaluate_intraday_session(features, session_date, cfg)
                        if result.status == "SESSION_INCOMPLETE":
                            deferred_incomplete_sessions += 1
                            continue
                        new_rows.append(_flatten_result(signal, result, cfg))
                        seen.add(key)
            except Exception as exc:
                status = "DEGRADED_INTRADAY_DATA"
                errors.append(f"DOWNLOAD:{type(exc).__name__}:{str(exc)[:180]}")

    observations, new_observations = _append_observations(observation_path, new_rows)
    output_path = outdir / "TCT_INTRADAY_V24_2_0_SHADOW.csv"
    _write_csv(observations, output_path)
    android_path = mobile / "ANDROID_TCT_INTRADAY_SHADOW.md"
    android_path.write_text(_android_summary(observations, generated_at), encoding="utf-8")

    entry_events = 0
    if not observations.empty and "status" in observations.columns:
        entry_events = int((observations["status"].astype(str) == "CAUSAL_ENTRY_EVENT").sum())

    payload = {
        "status": status,
        "version": VERSION,
        "generated_at_utc": generated_at,
        "new_signals": int(new_signals),
        "signal_ledger_rows": int(len(ledger)),
        "requested_tickers": len(requested_tickers),
        "intraday_histories_found": int(histories_found),
        "intraday_cache_layout": CACHE_LAYOUT,
        "intraday_cache_root": str((root / cfg["intraday_data"]["cache_dir"]).relative_to(root)),
        "intraday_cache_failures": cache_failures[:50],
        "new_observations": int(new_observations),
        "observation_ledger_rows": int(len(observations)),
        "causal_entry_events": entry_events,
        "deferred_incomplete_sessions": int(deferred_incomplete_sessions),
        "completed_sessions_only": bool(cfg["signal_bridge"].get("completed_sessions_only", True)),
        "current_calendar_session_persistence_forbidden": bool(cfg["signal_bridge"].get("current_calendar_session_persistence_forbidden", True)),
        "same_session_execution_forbidden": True,
        "minimum_lag_sessions": int(cfg["signal_bridge"]["minimum_lag_sessions"]),
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_execution_influence": 0.0,
        "stop_loss_influence": 0.0,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "errors": errors[:50],
        "outputs": {
            "shadow_observations": str(output_path.relative_to(root)),
            "signal_ledger": str(ledger_path.relative_to(root)),
            "observation_ledger": str(observation_path.relative_to(root)),
            "android": str(android_path.relative_to(root)),
        },
    }
    (auditdir / "TCT_INTRADAY_V24_2_0_AUDIT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
