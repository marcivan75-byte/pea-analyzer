from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import os
import sys
import time

import pandas as pd

from v183.smart_money.audit.quality import run as run_smart_money_quality
from v183.smart_money.calibration import validate_calibration
from v183.smart_money.coverage import build_etf_registry
from v183.smart_money.events import deduplicate
from v183.smart_money.io.provenance import save as save_provenance, upsert_provenance
from v183.smart_money.sources.amf_official_import import load_normalized_official_events
from v183.smart_money.sources.amf_short_open_data import fetch_csv as fetch_amf_shorts, to_events as amf_short_events
from v183.smart_money.sources.etf_flow_import import (
    history_for_isin,
    load_normalized_snapshots,
    load_state as load_flow_state,
    save_state as save_flow_state,
    upsert_history,
)
from v183.smart_money.sources.finnhub_insiders import fetch as fetch_finnhub_insiders, normalize as normalize_finnhub_insiders
from v183.smart_money.sources.yfinance_etf_snapshot import collect_snapshots
from v183.smart_money.wave9 import as_observations, score_action, score_etf

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "inputs"
CONFIG = ROOT / "config"
STATE = ROOT / "state"
OUTPUTS = ROOT / "outputs"
CACHE = ROOT / "data" / "cache"
SMART_STATE = STATE / "smart_money"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _read_master(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _float(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _base_score(row: pd.Series) -> float | None:
    for field in ("score_brut", "score_total", "ecs", "ECS", "score", "score_final"):
        if field in row.index:
            value = _float(row.get(field))
            if value is not None:
                return max(0.0, min(100.0, value))
    return None


def _priority_actions(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    rows = frame.copy()
    rows["_base_score"] = rows.apply(_base_score, axis=1)
    if "comite_status" in rows.columns:
        rows["_committee_priority"] = rows["comite_status"].astype(str).isin(["COMMITTEE", "WATCH"]).astype(int)
    else:
        rows["_committee_priority"] = 0
    return rows.sort_values(["_committee_priority", "_base_score"], ascending=[False, False], na_position="last").head(limit)


def _best_history_frames(cache_dir: Path, ticker_isin_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    best: dict[str, tuple[tuple[int, int], pd.DataFrame]] = {}
    for path in sorted(cache_dir.glob("history_*.parquet")):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if not isinstance(frame.columns, pd.MultiIndex):
            continue
        for ticker in frame.columns.get_level_values(0).unique():
            isin = ticker_isin_map.get(str(ticker))
            if not isin:
                continue
            try:
                sub = frame[ticker].copy()
                close = pd.to_numeric(sub.get("Close"), errors="coerce").dropna()
            except Exception:
                continue
            if len(close) < 20:
                continue
            try:
                last = int(pd.to_datetime(close.index[-1], utc=True).value)
            except Exception:
                last = 0
            rank = (len(close), last)
            if isin not in best or rank > best[isin][0]:
                best[isin] = (rank, sub)
    return {isin: item[1] for isin, item in best.items()}


def _adv20_eur(frame: pd.DataFrame | None) -> float | None:
    if frame is None or frame.empty or "Close" not in frame.columns or "Volume" not in frame.columns:
        return None
    close = pd.to_numeric(frame["Close"], errors="coerce")
    volume = pd.to_numeric(frame["Volume"], errors="coerce")
    value = (close * volume).dropna().tail(20).mean()
    return None if pd.isna(value) else float(value)


def _event_context(row: pd.Series) -> dict:
    def truthy(field: str) -> bool:
        if field not in row.index:
            return False
        value = row.get(field)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().upper() in {"1", "TRUE", "YES", "OUI", "Y"}

    earnings = truthy("earnings_event") or truthy("publication_event")
    if "days_to_earnings" in row.index:
        days = _float(row.get("days_to_earnings"))
        earnings = earnings or (days is not None and abs(days) <= 1)
    return {
        "earnings_event": earnings,
        "index_rebalance": truthy("index_rebalance") or truthy("index_rebalance_flag"),
        "corporate_action": truthy("corporate_action") or truthy("corporate_action_flag"),
    }


def _load_existing_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return pd.read_parquet(path).to_dict("records")
    except Exception:
        return []


def _collect_finnhub_events(
    actions: pd.DataFrame,
    existing_events: list[dict],
    cfg: dict,
    api_key: str | None,
    as_of: str,
) -> tuple[list[dict], dict, set[str]]:
    source_cfg = cfg["sources"]["finnhub_insiders"]
    if not api_key or not source_cfg.get("enabled", True):
        return [], {"enabled": bool(source_cfg.get("enabled", True)), "key_present": bool(api_key), "attempted": 0, "successful": 0}, set()

    map_path = CONFIG / "V18.2_FINNHUB_SYMBOL_MAP.csv"
    if not map_path.exists():
        return [], {"enabled": True, "key_present": True, "attempted": 0, "successful": 0, "reason": "NO_VALIDATED_SYMBOL_MAP"}, set()
    try:
        mapping = pd.read_csv(map_path, sep=";", encoding="utf-8-sig", dtype=str)
    except Exception:
        mapping = pd.DataFrame()
    if mapping.empty or not {"isin", "finnhub_symbol"}.issubset(mapping.columns):
        return [], {"enabled": True, "key_present": True, "attempted": 0, "successful": 0, "reason": "EMPTY_VALIDATED_SYMBOL_MAP"}, set()

    symbol_by_isin = {
        str(row["isin"]).strip().upper(): str(row["finnhub_symbol"]).strip()
        for _, row in mapping.iterrows()
        if str(row.get("isin") or "").strip() and str(row.get("finnhub_symbol") or "").strip()
    }
    official_insider_isins = {
        str(e.get("isin")) for e in existing_events
        if e.get("event_type") == "INSIDER" and e.get("evidence_level") == "A"
    }
    priority = _priority_actions(actions, int(cfg["runtime"].get("priority_action_limit", 300)))
    limit = min(
        int(cfg["runtime"].get("finnhub_fallback_limit", 120)),
        int(source_cfg.get("max_symbols_per_run", 120)),
    )
    selected: list[tuple[str, str]] = []
    for _, row in priority.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        if not isin or isin in official_insider_isins:
            continue
        symbol = symbol_by_isin.get(isin)
        if symbol:
            selected.append((isin, symbol))
        if len(selected) >= limit:
            break

    from_date = (datetime.fromisoformat(as_of).date() - timedelta(days=190)).isoformat()
    events: list[dict] = []
    failures: list[dict] = []
    covered: set[str] = set()
    for isin, symbol in selected:
        try:
            rows = fetch_finnhub_insiders(
                symbol,
                api_key,
                from_date=from_date,
                to_date=as_of,
                limit=int(source_cfg.get("limit_per_call", 100)),
            )
            covered.add(isin)
            events.extend(normalize_finnhub_insiders(rows, isin))
        except Exception as exc:
            failures.append({"isin": isin, "symbol": symbol, "reason": type(exc).__name__})
        time.sleep(max(0.0, float(source_cfg.get("delay_seconds", 1.05))))
    return events, {
        "enabled": True,
        "key_present": True,
        "attempted": len(selected),
        "successful": len(covered),
        "events": len(events),
        "failures": failures[:50],
    }, covered


def _prune_by_date(frame: pd.DataFrame, field: str, as_of: str, days: int) -> pd.DataFrame:
    if frame.empty or field not in frame.columns:
        return frame
    cutoff = (datetime.fromisoformat(as_of).date() - timedelta(days=max(1, int(days)))).isoformat()
    return frame[frame[field].astype(str).str[:10] >= cutoff].copy()


def _merge_scores(base: pd.DataFrame, scores: pd.DataFrame) -> pd.DataFrame:
    frame = base.copy()
    if scores.empty:
        return frame
    conflicts = [c for c in scores.columns if c != "isin" and c in frame.columns]
    if conflicts:
        frame = frame.rename(columns={c: f"v182_{c}" for c in conflicts})
    return frame.merge(scores.drop(columns=["universe", "as_of", "run_id"], errors="ignore"), on="isin", how="left")


def run() -> None:
    cfg = _json(CONFIG / "V18.3_SMART_MONEY_CONFIG.json")
    as_of = datetime.now(timezone.utc).date().isoformat()
    run_id = os.environ.get("V183_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.environ.setdefault("V182_RUN_ID", run_id)

    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "audit").mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "gaps").mkdir(parents=True, exist_ok=True)
    SMART_STATE.mkdir(parents=True, exist_ok=True)

    if os.environ.get("V183_SKIP_V182", "").lower() not in {"1", "true", "yes"}:
        from v182.reporting.run import run as run_v182
        run_v182()

    v182_quality_path = OUTPUTS / "audit" / "V18.2_QUALITY_GATES.json"
    if not v182_quality_path.exists():
        raise RuntimeError("V18.2 quality gate evidence missing")
    v182_quality = _json(v182_quality_path)
    if cfg["runtime"].get("fail_if_v182_quality_fails", True) and not v182_quality.get("passed"):
        raise RuntimeError("V18.2 quality gates are not green")

    actions_path = OUTPUTS / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    etf_path = OUTPUTS / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not actions_path.exists() or not etf_path.exists():
        raise RuntimeError("V18.2 enriched masters missing")
    actions = _read_master(actions_path)
    etfs = _read_master(etf_path)

    calibration = validate_calibration(cfg)
    _write_json(OUTPUTS / "audit" / "V18.3_SMART_MONEY_CALIBRATION.json", calibration)
    if not calibration["passed"]:
        raise RuntimeError("V18.3 structural calibration contract failed")

    new_events: list[dict] = []
    source_metrics: dict[str, dict] = {}
    short_source_ok = False
    try:
        short_cfg = cfg["sources"]["amf_short_open_data"]
        raw_short = fetch_amf_shorts(short_cfg["stable_resource_url"])
        short_events = amf_short_events(
            raw_short,
            as_of=as_of,
            history_depth_per_holder=int(short_cfg.get("history_depth_per_holder", 4)),
        )
        new_events.extend(short_events)
        short_source_ok = True
        source_metrics["amf_short_open_data"] = {
            "success": True,
            "events": len(short_events),
            "isins": len({e.get("isin") for e in short_events}),
        }
    except Exception as exc:
        source_metrics["amf_short_open_data"] = {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    official_path = ROOT / cfg["sources"]["amf_bdif_official_import"].get("normalized_input", "inputs/V18.3_AMF_OFFICIAL_EVENTS.csv")
    official_events: list[dict] = []
    if official_path.exists():
        try:
            official_events = load_normalized_official_events(official_path)
            official_events = [e for e in official_events if str(e.get("publication_date") or "")[:10] <= as_of]
            new_events.extend(official_events)
            source_metrics["amf_bdif_official_import"] = {"success": True, "events": len(official_events), "input_present": True}
        except Exception as exc:
            source_metrics["amf_bdif_official_import"] = {"success": False, "input_present": True, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    else:
        source_metrics["amf_bdif_official_import"] = {"success": True, "events": 0, "input_present": False, "mode": "FAIL_CLOSED"}

    finnhub_events, finnhub_metrics, finnhub_covered = _collect_finnhub_events(
        actions,
        official_events,
        cfg,
        os.environ.get("FINNHUB_API_KEY"),
        as_of,
    )
    new_events.extend(finnhub_events)
    source_metrics["finnhub_insiders"] = finnhub_metrics

    event_state_path = SMART_STATE / "SMART_MONEY_EVENTS.parquet"
    combined_events = _load_existing_events(event_state_path) + new_events
    kept_events, event_quarantine = deduplicate(combined_events) if combined_events else ([], [])
    events_df = pd.DataFrame(kept_events)
    if not events_df.empty:
        events_df = _prune_by_date(events_df, "publication_date", as_of, int(cfg["runtime"].get("persist_event_history_days", 730)))
        events_df.to_parquet(event_state_path, index=False)
        events_df.to_parquet(OUTPUTS / "V18.3_SMART_MONEY_EVENTS.parquet", index=False)
    if event_quarantine:
        pd.DataFrame(event_quarantine).to_csv(
            OUTPUTS / "gaps" / "V18.3_SMART_MONEY_EVENT_QUARANTINE.csv",
            sep=";", index=False, encoding="utf-8-sig",
        )

    # Build best OHLCV frame per ISIN from the V18.2 cache without altering v182.
    action_ticker_map = {
        str(ticker): str(isin)
        for ticker, isin in zip(actions.get("yahoo_ticker", pd.Series(dtype=str)), actions["isin"])
        if str(ticker or "").strip()
    }
    etf_ticker_map = {
        str(ticker): str(isin)
        for ticker, isin in zip(etfs.get("yahoo_ticker", pd.Series(dtype=str)), etfs["isin"])
        if str(ticker or "").strip()
    }
    action_frames = _best_history_frames(CACHE / "actions", action_ticker_map)
    etf_frames = _best_history_frames(CACHE / "etf", etf_ticker_map)

    events_by_isin = {
        str(isin): group.to_dict("records")
        for isin, group in (events_df.groupby("isin") if not events_df.empty else [])
    }
    official_insider_isins = {
        str(e.get("isin")) for e in official_events if e.get("event_type") == "INSIDER"
    }
    official_threshold_isins = {
        str(e.get("isin")) for e in official_events if e.get("event_type") == "THRESHOLD"
    }

    action_scores: list[dict] = []
    for _, row in actions.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        frame = action_frames.get(isin)
        source_availability = {
            "insiders": isin in official_insider_isins or isin in finnhub_covered,
            "thresholds": isin in official_threshold_isins,
            "shorts": short_source_ok,
            "tape": frame is not None,
        }
        scored = score_action(
            isin,
            _base_score(row),
            events_by_isin.get(isin, []),
            frame,
            as_of,
            cfg,
            source_availability,
            market_cap=_float(row.get("market_cap")) or _float(row.get("market_cap_yf")),
            adv20_eur=_adv20_eur(frame),
            event_context=_event_context(row),
        )
        scored.update({"universe": "ACTION", "as_of": as_of, "run_id": run_id})
        action_scores.append(scored)

    # ETF flow history: normalized issuer imports first, then bounded yfinance
    # snapshots as evidence C. Market price is never substituted for NAV.
    flow_cfg = cfg["sources"]["etf_flow_normalized_import"]
    flow_state_path = ROOT / flow_cfg.get("state_file", "state/smart_money/ETF_FLOW_HISTORY.parquet")
    flow_existing = load_flow_state(flow_state_path)
    flow_incoming = load_normalized_snapshots(ROOT / flow_cfg.get("normalized_input", "inputs/V18.3_ETF_FLOW_SNAPSHOTS.csv"), as_of=as_of)

    yf_cfg = cfg["sources"].get("yfinance_etf_snapshot", {})
    yf_snapshots = pd.DataFrame()
    yf_failures: list[dict] = []
    if yf_cfg.get("enabled", True):
        securities = []
        for _, row in etfs.iterrows():
            ticker = str(row.get("yahoo_ticker") or "").strip()
            if ticker:
                securities.append({"isin": row.get("isin"), "yahoo_ticker": ticker, "provider": row.get("provider")})
        securities = securities[: int(yf_cfg.get("max_symbols_per_run", 102))]
        yf_snapshots, yf_failures = collect_snapshots(
            securities,
            delay_seconds=float(yf_cfg.get("delay_seconds", 0.45)),
        )
    flow_history = upsert_history(flow_existing, flow_incoming)
    flow_history = upsert_history(flow_history, yf_snapshots)
    flow_history = _prune_by_date(flow_history, "date", as_of, int(cfg["runtime"].get("persist_score_history_days", 730)))
    save_flow_state(flow_history, flow_state_path)

    registry, coverage = build_etf_registry(
        etfs,
        flow_history,
        min_flow_observations=int(cfg["etf_flows"].get("min_history_observations", 20)),
    )
    registry.to_csv(OUTPUTS / "V18.3_ETF_FLOW_PROVIDER_REGISTRY.csv", sep=";", index=False, encoding="utf-8-sig")
    coverage["flow_snapshot_ingestion"] = {
        "normalized_input_rows": len(flow_incoming),
        "yfinance_snapshot_rows": len(yf_snapshots),
        "yfinance_failures": len(yf_failures),
        "persisted_rows": len(flow_history),
    }
    coverage["action_sources"] = source_metrics
    coverage["action_tape_isins"] = len(action_frames)
    coverage["etf_tape_isins"] = len(etf_frames)
    _write_json(OUTPUTS / "audit" / "V18.3_SMART_MONEY_COVERAGE.json", coverage)

    etf_scores: list[dict] = []
    for _, row in etfs.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        frame = etf_frames.get(isin)
        history = history_for_isin(flow_history, isin, as_of=as_of)
        scored = score_etf(
            isin,
            _base_score(row),
            history,
            frame,
            cfg,
            {"flows": not history.empty, "tape": frame is not None},
            event_context=_event_context(row),
        )
        scored.update({"universe": "ETF", "as_of": as_of, "run_id": run_id})
        etf_scores.append(scored)

    action_scores_df = pd.DataFrame(action_scores)
    etf_scores_df = pd.DataFrame(etf_scores)
    current_scores = pd.concat([action_scores_df, etf_scores_df], ignore_index=True, sort=False)
    current_scores.to_parquet(OUTPUTS / "V18.3_SMART_MONEY_DAILY_SCORES.parquet", index=False)

    score_state_path = SMART_STATE / "SMART_MONEY_DAILY_SCORES.parquet"
    if score_state_path.exists():
        try:
            prior_scores = pd.read_parquet(score_state_path)
        except Exception:
            prior_scores = pd.DataFrame()
    else:
        prior_scores = pd.DataFrame()
    score_state = pd.concat([prior_scores, current_scores], ignore_index=True, sort=False)
    if not score_state.empty:
        score_state = score_state.drop_duplicates(["universe", "isin", "as_of"], keep="last")
        score_state = _prune_by_date(score_state, "as_of", as_of, int(cfg["runtime"].get("persist_score_history_days", 730)))
        score_state.to_parquet(score_state_path, index=False)

    observations: list[dict] = []
    for row in action_scores + etf_scores:
        observations.extend(as_observations(row["universe"], row, as_of))
    provenance_path = SMART_STATE / "SMART_MONEY_PROVENANCE.parquet"
    if provenance_path.exists():
        try:
            existing_provenance = pd.read_parquet(provenance_path)
        except Exception:
            existing_provenance = None
    else:
        existing_provenance = None
    provenance = upsert_provenance(existing_provenance, observations)
    save_provenance(provenance, provenance_path)
    save_provenance(provenance, OUTPUTS / "V18.3_SMART_MONEY_PROVENANCE.parquet")

    _merge_scores(actions, action_scores_df).to_csv(
        OUTPUTS / "V18.3_PEA_ACTIONS_SMART_MONEY_SHADOW.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )
    _merge_scores(etfs, etf_scores_df).to_csv(
        OUTPUTS / "V18.3_PEA_ETF_SMART_MONEY_SHADOW.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )

    quality = run_smart_money_quality(
        events_df,
        current_scores,
        as_of,
        cfg,
        coverage=coverage,
        calibration=calibration,
    )
    smart_quality = {"passed": quality.passed, "checks": quality.checks, "run_id": run_id, "as_of": as_of}
    _write_json(OUTPUTS / "audit" / "V18.3_SMART_MONEY_QUALITY_GATES.json", smart_quality)
    _write_json(
        OUTPUTS / "audit" / "V18.3_QUALITY_GATES.json",
        {
            "passed": bool(v182_quality.get("passed")) and quality.passed,
            "v182_passed": bool(v182_quality.get("passed")),
            "smart_money_passed": quality.passed,
            "calibration_passed": calibration["passed"],
            "shadow_only": True,
            "run_id": run_id,
        },
    )
    _write_json(
        OUTPUTS / "audit" / "V18.3_RUN_SUMMARY.json",
        {
            "run_id": run_id,
            "as_of": as_of,
            "actions": len(action_scores_df),
            "etfs": len(etf_scores_df),
            "events": len(events_df),
            "event_quarantine": len(event_quarantine),
            "etf_registry_coverage_pct": coverage.get("registry_coverage_pct"),
            "etf_flow_ready_20d_pct": coverage.get("flow_ready_20d_pct"),
            "v182_quality_passed": bool(v182_quality.get("passed")),
            "smart_money_quality_passed": quality.passed,
            "active_scoring_allowed": False,
            "empirical_walk_forward_required": True,
        },
    )

    if not quality.passed:
        failed = [c["check"] for c in quality.checks if not c["passed"]]
        raise RuntimeError(f"SMART_MONEY_QUALITY_GATE_BLOCK: {failed}")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"ECHEC V18.3: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
