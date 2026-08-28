"""Façade opérationnelle V4.4 — orchestration only.

Ne change aucun critère, poids, seuil, univers ou contrat PIT.
Réutilise le cœur V22.2.3, le tail critique V21.16.0 et l'overlay V4
déjà audités le 27/08/2026.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import os

from v182.reporting import weekly_tail_super_runner_v21_16_0 as tail
from v182.reporting import weekly_unified_super_runner_v22_2_3 as core
from v182.reporting import weekly_unified_super_runner_v4 as overlay
from v182.reporting import objectives_risk_shadow_v1 as objectives_risk
from v182.reporting import objectives_risk_challenger_v2 as objectives_risk_challenger
from v182.reporting import portfolio_budget_challenger_v2 as portfolio_budget
from v182.reporting import ci_challenger_publication_v2 as challenger_publication
from v182.reporting import sector_or_shadow_v1 as sector_or_shadow
from v182.reporting import or_ranking_daily_shadow_v1 as daily_or_shadow
from v182.reporting import or_hebdo_report_v1 as or_hebdo_report
from v182.sources.ohlcv_incremental_policy import write_audit as write_ohlcv_policy


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_OPERATIONAL_V4_4_UNIFIED"
TARGET_SECONDS = 1200.0
DAILY_OR_INPUTS = (
    Path("outputs/action_ct/ACTION_CT_V22_1_0_DAILY_LATEST.csv"),
    Path("outputs/committee_master/ACTION_CT_V22_1_0_DAILY_LATEST.csv"),
    Path("outputs/action_ct/ACTION_CT_V22_0_0_DAILY_LATEST.csv"),
    Path("outputs/tct/TCT_DAILY_TRADER_LATEST.csv"),
)


def _prepare_runtime_dirs(root: Path) -> dict[str, str]:
    """Force un cache yfinance local inscriptible avant toute collecte."""
    yf_runtime = root / "data" / "cache" / ".yfinance-runtime"
    yf_runtime.mkdir(parents=True, exist_ok=True)
    tmp_runtime = root / "state" / "runtime_tmp"
    tmp_runtime.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YF_CACHE", str(yf_runtime))
    os.environ.setdefault("XDG_CACHE_HOME", str(yf_runtime))
    os.environ.setdefault("TMPDIR", str(tmp_runtime))
    os.environ.setdefault("PEA_SLOW_SOURCE_MODE", "CACHE_PREFERRED")
    os.environ.setdefault("PEA_WEEKLY_CRITICAL_ONLY", "1")
    os.environ.setdefault("PEA_YF_INCREMENTAL_PERIOD", "10d")
    os.environ.setdefault("PEA_YF_FORCE_FULL_HISTORY", "0")
    os.environ.setdefault("PEA_YF_FORCE_REFRESH", "0")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "2")
    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "2")
    return {
        "yf_cache": str(yf_runtime),
        "tmpdir": str(tmp_runtime),
        "yf_cache_writable": str(os.access(yf_runtime, os.W_OK)).lower(),
        "incremental_period": os.environ.get("PEA_YF_INCREMENTAL_PERIOD", "10d"),
    }


def _timed(name: str, fn):
    started = perf_counter()
    payload = fn()
    return name, payload, round(perf_counter() - started, 6)


def _has_daily_input(root: Path) -> bool:
    return any((root / path).exists() and (root / path).stat().st_size for path in DAILY_OR_INPUTS)


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    runtime_dirs = _prepare_runtime_dirs(root)
    ohlcv_policy = write_ohlcv_policy(root)
    or_timings: dict[str, float] = {}

    core_started = perf_counter()
    core_payload = core.run(root=root)
    core_seconds = perf_counter() - core_started

    previous_critical = os.environ.get("PEA_WEEKLY_CRITICAL_ONLY")
    os.environ["PEA_WEEKLY_CRITICAL_ONLY"] = "1"
    try:
        tail_started = perf_counter()
        tail_payload = tail.run(root=root)
        tail_seconds = perf_counter() - tail_started
    finally:
        if previous_critical is None:
            os.environ.pop("PEA_WEEKLY_CRITICAL_ONLY", None)
        else:
            os.environ["PEA_WEEKLY_CRITICAL_ONLY"] = previous_critical

    overlay_started = perf_counter()
    overlay_payload = overlay.run(
        root=root,
        ensure_upstream=False,
        run_ci_light=False,
        existing_ci_light=core_payload.get("ci_light_v4_2_independent"),
    )
    overlay_seconds = perf_counter() - overlay_started

    or_started = perf_counter()
    _, _, or_timings["objectives_risk_v1"] = _timed(
        "objectives_risk_v1", lambda: objectives_risk.run(root=root)
    )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="or-shadow") as pool:
        challenger_future = pool.submit(
            _timed, "objectives_risk_challenger", lambda: objectives_risk_challenger.run(root=root)
        )
        sector_future = pool.submit(
            _timed, "sector_or_shadow", lambda: sector_or_shadow.run(root=root)
        )
        _, or_payload, or_timings["objectives_risk_challenger"] = challenger_future.result()
        _, sector_or_payload, or_timings["sector_or_shadow"] = sector_future.result()

    _, _, or_timings["portfolio_budget"] = _timed(
        "portfolio_budget", lambda: portfolio_budget.run(root=root)
    )

    skip_daily = not _has_daily_input(root)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="or-publish") as pool:
        publication_future = pool.submit(
            _timed, "challenger_publication", lambda: challenger_publication.run(root=root)
        )
        if skip_daily:
            daily_or_payload = {
                "status": "SKIPPED_NO_DAILY_INPUT",
                "shadow_only": True,
                "real_orders_enabled": False,
                "score_influence": 0.0,
                "rows": 0,
            }
            or_timings["daily_or_shadow"] = 0.0
            _, publication_payload, or_timings["challenger_publication"] = publication_future.result()
        else:
            daily_future = pool.submit(
                _timed, "daily_or_shadow", lambda: daily_or_shadow.run(root=root)
            )
            _, publication_payload, or_timings["challenger_publication"] = publication_future.result()
            _, daily_or_payload, or_timings["daily_or_shadow"] = daily_future.result()

    _, or_report_payload, or_timings["or_hebdo_report"] = _timed(
        "or_hebdo_report", lambda: or_hebdo_report.run(root=root)
    )
    or_seconds = perf_counter() - or_started

    total_seconds = perf_counter() - started
    under_target = total_seconds < TARGET_SECONDS
    payload = {
        "status": "SUCCESS_UNDER_20_MINUTES" if under_target else "FAILED_RUNTIME_TARGET",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_seconds": TARGET_SECONDS,
        "total_seconds": round(total_seconds, 6),
        "under_target": under_target,
        "steps_seconds": {
            "core": round(core_seconds, 6),
            "critical_tail": round(tail_seconds, 6),
            "v4_overlay": round(overlay_seconds, 6),
            "objectives_risk_shadow_publication": round(or_seconds, 6),
            **{f"or_{name}": value for name, value in or_timings.items()},
        },
        "core_status": core_payload.get("status"),
        "tail_status": tail_payload.get("status"),
        "overlay_status": overlay_payload.get("status"),
        "objectives_risk_status": or_payload.get("status"),
        "objectives_risk_publication_status": publication_payload.get("status"),
        "sector_or_shadow_status": sector_or_payload.get("status"),
        "daily_or_shadow_status": daily_or_payload.get("status"),
        "or_hebdo_report_status": or_report_payload.get("status"),
        "objectives_risk_reference_influence": 0.0,
        "runtime_dirs": runtime_dirs,
        "ohlcv_incremental_policy": ohlcv_policy.get("policy"),
        "slow_source_mode": os.environ.get("PEA_SLOW_SOURCE_MODE", "CACHE_PREFERRED"),
        "runtime_optimizations": {
            "v4_upstream_recompute_removed": True,
            "duplicate_ci_light_run_removed": True,
            "ci_light_independence_preserved": True,
            "yfinance_runtime_dir_forced": True,
            "critical_tail_only_on_friday_path": True,
            "github_live_default_removed": True,
            "or_independent_steps_overlapped": True,
            "daily_or_skipped_without_input": skip_daily,
            "blas_threads_capped": True,
            "ohlcv_incremental_period_10d": True,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "information_loss": False,
        },
        "deferred_distinct_shadow_process": ["TACTICAL_SHADOW_BUNDLE", "POSTMARKET_V24_4_2", "ETF_STRUCTURE_STATE_REPLAY"],
        "deferred_decision_score_weight_influence": 0.0,
        "real_orders_enabled": False,
    }
    audit = root / "outputs/audit/WEEKLY_OPERATIONAL_RUNTIME_V4_4.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not under_target:
        raise RuntimeError(
            f"WEEKLY_RUNTIME_TARGET_EXCEEDED:{total_seconds:.3f}>={TARGET_SECONDS:.3f}"
        )
    return payload


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
