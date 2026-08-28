"""Façade opérationnelle V4.4 — orchestration only."""
from __future__ import annotations

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
from v182.reporting import weekly_run_synthesis_v1 as run_synthesis
from v182.backtest import weekly_pit_snapshot_v1 as pit_snapshot
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
    return {
        "yf_cache": str(yf_runtime),
        "tmpdir": str(tmp_runtime),
        "incremental_period": os.environ.get("PEA_YF_INCREMENTAL_PERIOD", "10d"),
    }


def _safe_step(name: str, fn, timings: dict[str, float]):
    started = perf_counter()
    try:
        payload = fn()
        if not isinstance(payload, dict):
            payload = {"status": "SUCCESS"}
    except Exception as exc:
        payload = {"status": f"SHADOW_FAILED:{type(exc).__name__}", "error": str(exc)[:400], "shadow_only": True}
    timings[name] = round(perf_counter() - started, 6)
    return payload


def _has_daily_input(root: Path) -> bool:
    return any((root / path).exists() and (root / path).stat().st_size for path in DAILY_OR_INPUTS)


def _write_runtime(root: Path, payload: dict) -> None:
    audit = root / "outputs/audit/WEEKLY_OPERATIONAL_RUNTIME_V4_4.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    runtime_dirs = _prepare_runtime_dirs(root)
    ohlcv_policy = write_ohlcv_policy(root)
    or_timings: dict[str, float] = {}
    payload: dict = {"status": "RUNNING", "version": VERSION}
    try:
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
        _safe_step("objectives_risk_v1", lambda: objectives_risk.run(root=root), or_timings)
        or_payload = _safe_step("objectives_risk_challenger", lambda: objectives_risk_challenger.run(root=root), or_timings)
        sector_or_payload = _safe_step("sector_or_shadow", lambda: sector_or_shadow.run(root=root), or_timings)
        _safe_step("portfolio_budget", lambda: portfolio_budget.run(root=root), or_timings)
        publication_payload = _safe_step("challenger_publication", lambda: challenger_publication.run(root=root), or_timings)
        skip_daily = not _has_daily_input(root)
        if skip_daily:
            daily_or_payload = {"status": "SKIPPED_NO_DAILY_INPUT", "shadow_only": True, "rows": 0}
            or_timings["daily_or_shadow"] = 0.0
        else:
            daily_or_payload = _safe_step("daily_or_shadow", lambda: daily_or_shadow.run(root=root), or_timings)
        or_report_payload = _safe_step("or_hebdo_report", lambda: or_hebdo_report.run(root=root), or_timings)
        snapshot_payload = _safe_step("weekly_pit_snapshot", lambda: pit_snapshot.run(root=root), or_timings)
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
                "ci": core_payload.get("ci_seconds"),
                "ci_light": core_payload.get("ci_light_seconds"),
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
            "weekly_pit_snapshot_status": snapshot_payload.get("status"),
            "objectives_risk_reference_influence": 0.0,
            "runtime_dirs": runtime_dirs,
            "ohlcv_incremental_policy": ohlcv_policy.get("policy"),
            "runtime_optimizations": {
                "or_steps_sequential": True,
                "shadow_failures_isolated": True,
                "weekly_pit_snapshot_enabled": True,
                "criteria_changed": False,
                "weights_changed": False,
                "thresholds_changed": False,
            },
            "real_orders_enabled": False,
        }
        _write_runtime(root, payload)
        try:
            synthesis_payload = run_synthesis.run(root=root)
            payload["synthesis_status"] = synthesis_payload.get("status")
            _write_runtime(root, payload)
        except Exception as exc:
            payload["synthesis_status"] = f"FAILED:{type(exc).__name__}"
            _write_runtime(root, payload)
        if not under_target:
            raise RuntimeError(
                f"WEEKLY_RUNTIME_TARGET_EXCEEDED:{total_seconds:.3f}>={TARGET_SECONDS:.3f}"
            )
        return payload
    except Exception:
        if payload.get("status") == "RUNNING":
            payload["status"] = "FAILED_EXCEPTION"
            payload["total_seconds"] = round(perf_counter() - started, 6)
        _write_runtime(root, payload)
        raise


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
