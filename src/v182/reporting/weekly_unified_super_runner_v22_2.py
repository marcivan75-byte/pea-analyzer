from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_entry_watch_v22_2
from v182.reporting import etf_structure_refresh
from v182.reporting import slow_data_cache_v22_2 as slow_cache
from v182.reporting import weekly_unified_super_runner_v22_1 as previous

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2.json"


def run(root: Path = ROOT) -> dict:
    """V22.2 = V22.1 runtime gains + CI watch + cadence-aware slow-data cache."""
    started = perf_counter()
    audit_dir = root / "outputs/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    watch_payload: dict = {}
    slow_metrics: dict = {}
    error = None

    original_structural = etf_structure_refresh.collect_etf_structural_data
    original_inception = etf_structure_refresh.collect_etf_inception_data
    original_fund_structure = etf_structure_refresh.collect_fund_structure

    etf_structure_refresh.collect_etf_structural_data = slow_cache.cached_call(
        original_structural,
        slow_cache.ETF_STRUCTURAL,
        root=root,
        metrics=slow_metrics,
    )
    etf_structure_refresh.collect_etf_inception_data = slow_cache.cached_call(
        original_inception,
        slow_cache.ETF_INCEPTION,
        root=root,
        metrics=slow_metrics,
    )
    etf_structure_refresh.collect_fund_structure = slow_cache.cached_call(
        original_fund_structure,
        slow_cache.ETF_FUND_STRUCTURE,
        root=root,
        metrics=slow_metrics,
    )

    try:
        payload = previous.run(root=root)
        watch_payload = ci_entry_watch_v22_2.run(root=root)
        if watch_payload.get("status") != "SUCCESS":
            raise RuntimeError(f"V22_2_CI_WATCH_FAILED:{watch_payload.get('status')}")
        payload = dict(payload)
        payload["ci_entry_watch_v22_2"] = watch_payload
        payload["slow_data_cache_v22_2"] = slow_metrics
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        etf_structure_refresh.collect_etf_structural_data = original_structural
        etf_structure_refresh.collect_etf_inception_data = original_inception
        etf_structure_refresh.collect_fund_structure = original_fund_structure
        slow_cache.write_audit(root, slow_metrics)
        core_payload = watch_payload.get("core", {}) if isinstance(watch_payload, dict) else {}
        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "ci_entry_watch_status": watch_payload.get("status"),
            "ci_entry_candidate_rows": watch_payload.get("candidate_rows"),
            "ci_entry_ready_for_review": watch_payload.get("ready_for_review"),
            "ci_entry_strong_confidence": core_payload.get("strong_confidence"),
            "slow_data_cache": slow_metrics,
            "etf_structural_ttl_days": slow_cache.ETF_STRUCTURAL.ttl_days,
            "etf_inception_ttl_days": slow_cache.ETF_INCEPTION.ttl_days,
            "etf_fund_structure_ttl_days": slow_cache.ETF_FUND_STRUCTURE.ttl_days,
            "wave09_disabled": True,
            "selection_score_changed": False,
            "selection_decision_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "universe_changed": False,
            "t1_t2_scope_changed": False,
            "real_orders_enabled": False,
        }
        (audit_dir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
