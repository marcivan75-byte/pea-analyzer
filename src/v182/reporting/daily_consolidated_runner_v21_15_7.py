from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import traceback

from v182.reporting import daily_ci_restitution_v21_15_7 as daily_ci
from v182.reporting import daily_consolidated_runner_v21_15_5 as base
from v182.reporting import daily_tactical_super_runner_v21_15_6 as tactical
from v182.reporting import daily_w09_seed_v21_15_7 as w09_seed
from v182.reporting import daily_provenance_compact_cache_v21_15_8 as provenance_compact


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_7"
QUARANTINE_IDENTITY_FIELDS = (
    "universe",
    "isin",
    "field",
    "value",
    "source",
    "evidence_level",
    "validation_status",
    "reason",
)


def _write_final_audit(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    for name in (
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_7.json",
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_6.json",
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_5.json",
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_4.json",
    ):
        (auditdir / name).write_text(text, encoding="utf-8")


def _write_base_failure_audit(root: Path, exc: Exception, elapsed_seconds: float) -> None:
    payload = {
        "status": "FAILED_BASE_RUN",
        "failed_stage": "base_run",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "base_elapsed_seconds": round(float(elapsed_seconds), 6),
        "traceback": traceback.format_exc(),
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
    }
    _write_final_audit(root, payload)


def _write_ci_failure_audit(root: Path, exc: Exception, elapsed_seconds: float) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "FAILED_CI_RESTITUTION",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "ci_elapsed_seconds": round(float(elapsed_seconds), 6),
        "traceback": traceback.format_exc(),
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "real_orders_enabled": False,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    for name in ("DAILY_CI_FAILURE_V21_15_7.json", "DAILY_CI_RESTITUTION_V21_15_7.json"):
        (auditdir / name).write_text(text, encoding="utf-8")


def _quarantine_semantic_key(row: dict) -> str:
    payload = {field: row.get(field) for field in QUARANTINE_IDENTITY_FIELDS}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _dedupe_quarantine_latest(rows: list[dict]) -> tuple[list[dict], set[str], int]:
    """Keep the latest occurrence of every semantically identical conflict."""
    latest: dict[str, tuple[int, dict]] = {}
    passthrough: list[tuple[int, dict]] = []
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            passthrough.append((position, row))
            continue
        latest[_quarantine_semantic_key(row)] = (position, row)
    ordered = sorted([*latest.values(), *passthrough], key=lambda item: item[0])
    unique = [row for _, row in ordered]
    return unique, set(latest), max(0, len(rows) - len(unique))


def _install_post_wave7_quarantine_guard(self, original_fast_install, state: dict, stats: dict) -> None:
    """Suppress rediscovery of already retained conflicts after WAVE07 only."""
    original_fast_install(self)
    installed_apply = base.collection.legacy.apply_and_track

    def guarded_apply(frame, observations):
        output, quarantined = installed_apply(frame, observations)
        if not state.get("active"):
            return output, quarantined
        unique: list[dict] = []
        seen: set[str] = state["seen"]
        for row in quarantined:
            if not isinstance(row, dict):
                unique.append(row)
                continue
            key = _quarantine_semantic_key(row)
            if key in seen:
                stats["semantic_duplicates_removed_after_wave7"] += 1
                continue
            seen.add(key)
            unique.append(row)
        stats["distinct_semantic_conflicts_final_seen"] = len(seen)
        return output, unique

    base.collection.legacy.apply_and_track = guarded_apply


def run(root: Path = ROOT) -> dict:
    """Final Daily: zero-network W09, bounded tactical engines and same-run CI restitution."""
    started = perf_counter()
    provenance_original, provenance_stats = provenance_compact.install()
    provenance_restored = False

    def restore_provenance() -> None:
        nonlocal provenance_restored
        if not provenance_restored:
            provenance_compact.restore(provenance_original)
            provenance_restored = True

    original_tactical = base.tactical
    original_version = base.VERSION
    original_fast_install = base._ORIGINAL_FAST_INSTALL
    original_wave7 = base.collection.waves.wave7_official_validation
    quarantine_state: dict = {"active": False, "seen": set()}
    quarantine_stats = {
        "status": "SEMANTIC_DUPLICATE_GUARD_ENABLED",
        "semantic_identity_fields": list(QUARANTINE_IDENTITY_FIELDS),
        "semantic_duplicates_removed_at_wave7": 0,
        "semantic_duplicates_removed_after_wave7": 0,
        "distinct_semantic_conflicts_at_wave7": 0,
        "distinct_semantic_conflicts_final_seen": 0,
        "latest_occurrence_preserved": True,
        "decision_logic_changed": False,
        "data_quality_rules_changed": False,
    }

    def fast_install_with_guard(self):
        return _install_post_wave7_quarantine_guard(self, original_fast_install, quarantine_state, quarantine_stats)

    def wave7_with_semantic_dedupe(quarantine, overrides_path):
        unique, seen, removed = _dedupe_quarantine_latest(quarantine)
        quarantine[:] = unique
        quarantine_state["seen"] = seen
        quarantine_state["active"] = True
        quarantine_stats["semantic_duplicates_removed_at_wave7"] = int(removed)
        quarantine_stats["distinct_semantic_conflicts_at_wave7"] = int(len(seen))
        quarantine_stats["distinct_semantic_conflicts_final_seen"] = int(len(seen))
        return original_wave7(quarantine, overrides_path)

    base.tactical = tactical
    base.VERSION = VERSION
    base._ORIGINAL_FAST_INSTALL = fast_install_with_guard
    base.collection.waves.wave7_official_validation = wave7_with_semantic_dedupe
    try:
        try:
            payload = dict(base.run(root=root) or {})
        except Exception as exc:
            _write_base_failure_audit(root, exc, perf_counter() - started)
            restore_provenance()
            raise
    finally:
        base.tactical = original_tactical
        base.VERSION = original_version
        base._ORIGINAL_FAST_INSTALL = original_fast_install
        base.collection.waves.wave7_official_validation = original_wave7

    base_status = str(payload.get("status") or "")
    ci_started = perf_counter()
    try:
        ci_payload = daily_ci.run(root=root)
    except Exception as exc:
        _write_ci_failure_audit(root, exc, perf_counter() - ci_started)
        restore_provenance()
        raise
    ci_seconds = perf_counter() - ci_started

    provenance_persist_started = perf_counter()
    try:
        provenance_compact.persist(provenance_stats)
    except Exception as exc:
        provenance_stats["persist_status"] = "FAILED_NON_BLOCKING"
        provenance_stats["persist_error_type"] = type(exc).__name__
        provenance_stats["persist_error"] = str(exc)[:500]
    finally:
        provenance_stats["persist_wrapper_seconds"] = round(perf_counter() - provenance_persist_started, 6)
        restore_provenance()

    timings = dict(payload.get("timings_seconds") or {})
    timings["ci_restitution"] = round(float(ci_seconds), 6)
    timings["provenance_compact_persist"] = round(float(provenance_stats.get("persist_wrapper_seconds", 0.0)), 6)
    timings["total"] = round(float(perf_counter() - started), 6)
    steps = dict(payload.get("steps") or {})
    steps["ci_restitution"] = {
        "status": ci_payload.get("status"),
        "version": ci_payload.get("version"),
        "selected_rows": ci_payload.get("selected_rows"),
        "word_output": ci_payload.get("word_output"),
        "excel_output": ci_payload.get("excel_output"),
    }
    steps["quarantine_deduplication"] = quarantine_stats
    steps["provenance_compact_cache"] = provenance_stats
    final_status = (
        "SUCCESS_DAILY_CONSOLIDATED_WITH_CI_AND_ETF_REPLAY_WARNING"
        if "ETF_REPLAY_WARNING" in base_status
        else "SUCCESS_DAILY_CONSOLIDATED_WITH_CI"
    )

    payload.update({
        "status": final_status,
        "base_status_before_ci": base_status,
        "version": VERSION,
        "tactical_runtime_version": tactical.VERSION,
        "daily_ci_version": daily_ci.VERSION,
        "wave09_refresh_cadence": "WEEKLY_ONLY",
        "wave09_daily_network_calls": 0,
        "wave09_bootstrap_seed": w09_seed.audit_contract(),
        "weekly_snapshot_preferred_when_available": True,
        "legacy_validated_w09_seed_used_only_when_fast_or_weekly_master_missing": True,
        "committee_model_reruns": 0,
        "committee_external_collection_calls": 0,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
        "steps": steps,
        "timings_seconds": timings,
    })
    _write_final_audit(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
