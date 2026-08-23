from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import time

import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.features.action_decision_enhancements import build_action_enhancement_observations
from v182.features.sector_rotation import build_rotation_observations
from v182.io.frames import apply_observations, load_master, save_master
from v182.reporting import waves
from v182.reporting.daily_context_baseline import load_context_baseline, publish_context_baseline

ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_FAST_COLLECTION_V21_16_3"


def _load_cfg(root: Path) -> dict:
    return json.loads((root / "config" / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))


def _validate_same_universe(current: pd.DataFrame, baseline: pd.DataFrame, label: str) -> None:
    if "isin" not in current or "isin" not in baseline:
        raise RuntimeError(f"DAILY_FAST_{label}_ISIN_MISSING")
    current_isins = set(current["isin"].astype(str))
    baseline_isins = set(baseline["isin"].astype(str))
    if current_isins != baseline_isins or len(current) != len(baseline):
        raise RuntimeError(
            f"DAILY_FAST_{label}_UNIVERSE_MISMATCH:current={len(current)} baseline={len(baseline)}"
        )


def _coverage(frame: pd.DataFrame, field: str) -> int:
    if field not in frame:
        return 0
    values = frame[field]
    return int((values.notna() & ~values.astype(str).str.strip().str.lower().isin({"", "nan", "none", "na", "n/a"})).sum())


def _bootstrap_full(root: Path, reason: str) -> dict:
    """Fail safe: force the historical complete LIVE collector once, then seed the fast baseline."""
    if root.resolve() != ROOT.resolve():
        raise RuntimeError("DAILY_FAST_FULL_FALLBACK_REQUIRES_REPOSITORY_ROOT")
    from v182.reporting import run as full_enrichment

    keys = ("PEA_RUN_PROFILE", "PEA_SLOW_SOURCE_MODE", "PEA_YF_INCREMENTAL_PERIOD")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["PEA_RUN_PROFILE"] = "FULL"
    os.environ["PEA_SLOW_SOURCE_MODE"] = "LIVE"
    os.environ["PEA_YF_INCREMENTAL_PERIOD"] = "1mo"
    try:
        result = full_enrichment.run()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    actions = pd.read_csv(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", sep=";", encoding="utf-8-sig", low_memory=False)
    etfs = pd.read_csv(root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv", sep=";", encoding="utf-8-sig", low_memory=False)
    baseline = publish_context_baseline(
        actions,
        etfs,
        root,
        full_refresh=True,
        profile="DAILY_FAST_FULL_FALLBACK",
        run_id=str(result.get("run_id") or ""),
    )
    payload = {
        "status": "FULL_BOOTSTRAP_FALLBACK",
        "version": VERSION,
        "fallback_reason": reason,
        "full_enrichment": result,
        "baseline": baseline,
        "normal_daily_fast_path_used": False,
        "forced_full_profile": True,
        "forced_slow_sources_live": True,
        "forced_yfinance_incremental_period": "1mo",
        "decision_logic_changed": False,
        "score_logic_changed": False,
    }
    audit = root / "outputs" / "audit" / "DAILY_FAST_COLLECTION.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def run(root: Path = ROOT) -> dict:
    wall_start = time.perf_counter()
    cfg = _load_cfg(root)
    max_age = float(os.environ.get("PEA_DAILY_BASELINE_MAX_AGE_DAYS", "8"))
    try:
        actions, etfs, baseline_meta = load_context_baseline(root, max_full_age_days=max_age)
    except RuntimeError as exc:
        return _bootstrap_full(root, str(exc))

    raw_actions = load_master(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    action_spec = cfg.get("canonical_universe", {})
    whitelist = action_spec.get("actions_whitelist_path")
    if not whitelist:
        raise RuntimeError("DAILY_FAST_CANONICAL_ACTION_WHITELIST_MISSING")
    canonical = filter_actions(raw_actions, root / whitelist).included.reset_index(drop=True)
    raw_etfs = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    _validate_same_universe(canonical, actions, "ACTION")
    _validate_same_universe(raw_etfs, etfs, "ETF")

    initial_action_close_coverage = _coverage(actions, "last_close")
    initial_etf_close_coverage = _coverage(etfs, "last_close")
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    action_history = waves.wave_history(actions, "ACTION", str(root / "data" / "cache" / "actions"), cfg)
    timings["action_ohlcv_seconds"] = round(time.perf_counter() - t0, 6)

    t0 = time.perf_counter()
    etf_with_tickers, etf_gaps = waves.resolve_etf_tickers(etfs, root / "config" / "V18.2_ETF_TICKER_MAP.csv")
    if not etf_gaps.empty:
        gaps = root / "outputs" / "gaps"
        gaps.mkdir(parents=True, exist_ok=True)
        etf_gaps.to_csv(gaps / "V18.2_ETF_TICKER_GAPS.csv", sep=";", index=False, encoding="utf-8-sig")
    etf_history = waves.wave_history(etf_with_tickers, "ETF", str(root / "data" / "cache" / "etf"), cfg)
    timings["etf_ohlcv_seconds"] = round(time.perf_counter() - t0, 6)

    t0 = time.perf_counter()
    actions_map = dict(zip(actions["yahoo_ticker"], actions["isin"]))
    etf_map = dict(zip(etf_with_tickers["yahoo_ticker"], etf_with_tickers["isin"]))
    workers = int(cfg.get("runtime_optimization", {}).get("daily_profile", {}).get("wave3_local_workers", 2))
    obs_actions, obs_etfs, obs_beta = waves.wave3_local_features(
        str(root / "data" / "cache" / "actions"),
        actions_map,
        str(root / "data" / "cache" / "etf"),
        etf_map,
        max_workers=workers,
    )
    before_actions = len(actions)
    before_etfs = len(etfs)
    actions, action_quarantine = apply_observations(actions, obs_actions)
    etfs, etf_quarantine = apply_observations(etfs, obs_etfs + obs_beta)
    if len(actions) != before_actions or len(etfs) != before_etfs:
        raise RuntimeError("DAILY_FAST_LOCAL_FEATURE_ROW_MUTATION")
    timings["local_features_seconds"] = round(time.perf_counter() - t0, 6)

    t0 = time.perf_counter()
    rotation_obs, sectors, rotation_diag = build_rotation_observations(actions)
    actions, rotation_quarantine = apply_observations(actions, rotation_obs)
    output_dir = root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    sectors.to_csv(output_dir / "V21_3_SECTOR_ROTATION.csv", sep=";", index=False, encoding="utf-8-sig")
    audit_dir = output_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "V21_3_SECTOR_ROTATION.json").write_text(
        json.dumps(rotation_diag, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    timings["sector_rotation_local_seconds"] = round(time.perf_counter() - t0, 6)

    t0 = time.perf_counter()
    shortlist = set(
        actions.loc[actions["comite_status"].astype(str).isin(["COMMITTEE", "WATCH"]), "isin"]
    ) if "comite_status" in actions else set()
    scenario_obs = waves.wave8_scenarios(actions, shortlist)
    actions, scenario_quarantine = apply_observations(actions, scenario_obs)
    enhancement_obs = build_action_enhancement_observations(actions)
    actions, enhancement_quarantine = apply_observations(actions, enhancement_obs)
    timings["local_decision_features_seconds"] = round(time.perf_counter() - t0, 6)

    final_action_close_coverage = _coverage(actions, "last_close")
    final_etf_close_coverage = _coverage(etfs, "last_close")
    if final_action_close_coverage < initial_action_close_coverage or final_etf_close_coverage < initial_etf_close_coverage:
        raise RuntimeError(
            "DAILY_FAST_TECHNICAL_COVERAGE_REGRESSION:"
            f"action={initial_action_close_coverage}->{final_action_close_coverage};"
            f"etf={initial_etf_close_coverage}->{final_etf_close_coverage}"
        )

    save_master(actions, output_dir / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    save_master(etfs, output_dir / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")

    now = datetime.now(timezone.utc).isoformat()
    baseline = publish_context_baseline(
        actions,
        etfs,
        root,
        full_refresh=False,
        profile="DAILY_TACTICAL_FAST",
        run_id=now,
    )
    quarantine = (
        action_quarantine
        + etf_quarantine
        + rotation_quarantine
        + scenario_quarantine
        + enhancement_quarantine
    )
    if quarantine:
        gaps = output_dir / "gaps"
        gaps.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(quarantine).to_csv(gaps / "DAILY_FAST_QUARANTINE.csv", sep=";", index=False, encoding="utf-8-sig")

    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": now,
        "normal_daily_fast_path_used": True,
        "baseline": baseline_meta,
        "baseline_after_daily_refresh": baseline,
        "actions_rows": int(len(actions)),
        "etf_rows": int(len(etfs)),
        "action_ohlcv_usable": int(len(action_history.successful)),
        "action_ohlcv_failed": int(len(action_history.failed)),
        "etf_ohlcv_usable": int(len(etf_history.successful)),
        "etf_ohlcv_failed": int(len(etf_history.failed)),
        "action_last_close_coverage": final_action_close_coverage,
        "etf_last_close_coverage": final_etf_close_coverage,
        "market_observations": int(len(obs_actions) + len(obs_etfs) + len(obs_beta)),
        "sector_rotation_observations": int(len(rotation_obs)),
        "sector_rotation_sectors": int(len(sectors)),
        "scenario_observations": int(len(scenario_obs)),
        "action_enhancement_observations": int(len(enhancement_obs)),
        "quarantine_rows": int(len(quarantine)),
        "heavy_slow_source_waves_skipped": [
            "WAVE_04_ACTION_FUNDAMENTALS",
            "WAVE_05_FINNHUB_CONSENSUS",
            "WAVE_06_ETF_INFO",
            "WAVE_06B_MORNINGSTAR_ACTIONS",
            "WAVE_06C_PUBLIC_FALLBACKS",
            "WAVE_09_TOPDOWN_EXTERNAL_REFRESH",
        ],
        "fast_market_sensitive_modules_retained": [
            "WAVE_01_ACTION_OHLCV",
            "WAVE_02_ETF_OHLCV",
            "WAVE_03_LOCAL_OHLCV_FEATURES",
            "WAVE_08_SCENARIOS",
            "WAVE_10_SECTOR_ROTATION_LOCAL",
            "WAVE_11_ACTION_DECISION_FACTORS_LOCAL",
        ],
        "slow_context_preserved_from_last_full_baseline": True,
        "slow_source_freshness_extended_by_daily_write": False,
        "decision_logic_changed": False,
        "score_logic_changed": False,
        "universe_reduced": False,
        "timings": timings,
        "wall_seconds": round(time.perf_counter() - wall_start, 6),
    }
    audit = audit_dir / "DAILY_FAST_COLLECTION.json"
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
