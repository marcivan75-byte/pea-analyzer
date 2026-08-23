from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import perf_counter
import json
import os
import traceback

import pandas as pd

from v182.reporting import daily_fast_collection_run as collection
from v182.reporting import daily_tactical_super_runner_v21_15_5 as tactical
from v182.reporting import daily_w09_seed_v21_15_7 as w09_seed
from v182.reporting import etf_structure_state_replay as etf_replay
from v182.reporting import wave3_cpu_budget_v21_15_4 as wave3_cpu
from v182.reporting.earnings_clock_v21_15_4 import refresh_frame as refresh_earnings_clock


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_5"
CACHE_CONTRACT_VERSION = "DAILY_COLLECTION_COMPAT_V21_15_5"
WEEKLY_SNAPSHOT_DIR = ROOT / "state" / "provenance" / "weekly_master_snapshot_v1"
WEEKLY_ACTIONS = WEEKLY_SNAPSHOT_DIR / "actions.parquet"
WEEKLY_ETF = WEEKLY_SNAPSHOT_DIR / "etf.parquet"
WEEKLY_MANIFEST = WEEKLY_SNAPSHOT_DIR / "manifest.json"
CACHE_CONTRACT_FILES = (
    "src/v182/reporting/run.py",
    "src/v182/reporting/waves.py",
    "src/v182/reporting/daily_fast_collection_run.py",
    "src/v182/io/frames.py",
    "src/v182/sources/yfinance_info.py",
    "src/v182/sources/finnhub_consensus.py",
)

_ORIGINAL_FAST_INSTALL = collection.DailyFastRuntime.install
_ORIGINAL_FAST_RESTORE = collection.DailyFastRuntime.restore
_ORIGINAL_FAST_LOADER = collection._load_fast_state


def _collection_code_contract(root: Path = ROOT) -> str:
    digest = sha256()
    for relative in CACHE_CONTRACT_FILES:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.exists() and path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _empty_fast(manifest: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict, str]:
    return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"


def _valid_weekly_snapshot() -> tuple[pd.DataFrame, pd.DataFrame, dict] | None:
    if not WEEKLY_MANIFEST.exists() or not WEEKLY_ACTIONS.exists() or not WEEKLY_ETF.exists():
        return None
    try:
        manifest = json.loads(WEEKLY_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return None
    if manifest.get("version") != "WEEKLY_MASTER_SNAPSHOT_V1" or manifest.get("validated") is not True:
        return None
    if manifest.get("static_contract") != collection._static_contract():
        return None
    if manifest.get("actions_sha256") != collection._sha256_file(WEEKLY_ACTIONS):
        return None
    if manifest.get("etf_sha256") != collection._sha256_file(WEEKLY_ETF):
        return None
    actions = collection._read_fast_frame(WEEKLY_ACTIONS)
    etf = collection._read_fast_frame(WEEKLY_ETF)
    if not collection._valid_fast_frame(actions, expected_rows=int(manifest.get("actions_rows", 0) or 0)):
        return None
    if not collection._valid_fast_frame(etf, expected_rows=int(manifest.get("etf_rows", 0) or 0)):
        return None
    return actions, etf, manifest


def _load_fast_state_compatible() -> tuple[pd.DataFrame, pd.DataFrame, dict, str]:
    """Use functional state identity; fall back to the last validated weekly master."""
    if os.environ.get("PEA_RUN_PROFILE", "").strip().upper() != "DAILY_TACTICAL":
        return _ORIGINAL_FAST_LOADER()

    manifest = collection._load_manifest()
    valid_daily_manifest = bool(
        manifest.get("version") == collection.VERSION
        and manifest.get("validated") is True
        and manifest.get("static_contract") == collection._static_contract()
    )
    if valid_daily_manifest:
        recorded_contract = str(manifest.get("daily_collection_code_contract") or "").strip()
        current_contract = _collection_code_contract(ROOT)
        code_ok = not recorded_contract or recorded_contract == current_contract
        state_hashes_ok = bool(
            manifest.get("actions_sha256") == collection._sha256_file(collection.ACTIONS_STATE)
            and manifest.get("etf_sha256") == collection._sha256_file(collection.ETF_STATE)
        )
        if code_ok and state_hashes_ok:
            actions = collection._read_fast_frame(collection.ACTIONS_STATE)
            etf = collection._read_fast_frame(collection.ETF_STATE)
            frames_ok = bool(
                collection._valid_fast_frame(actions, expected_rows=int(manifest.get("actions_rows", 0) or 0))
                and collection._valid_fast_frame(etf, expected_rows=int(manifest.get("etf_rows", 0) or 0))
            )
            if frames_ok:
                mode = "DELTA_ONLY" if manifest.get("cache_contract") == collection._cache_contract() else "RECONCILE_CACHE"
                return actions, etf, manifest, mode

    weekly = _valid_weekly_snapshot()
    if weekly is not None:
        actions, etf, weekly_manifest = weekly
        migrated_manifest = {
            "version": collection.VERSION,
            "validated": True,
            "source": "WEEKLY_MASTER_SNAPSHOT_V1",
            "weekly_snapshot_generated_at_utc": weekly_manifest.get("generated_at_utc"),
            "static_contract": collection._static_contract(),
            "cache_contract": {},
            "daily_collection_code_contract": _collection_code_contract(ROOT),
        }
        return actions, etf, migrated_manifest, "RECONCILE_CACHE"

    return _empty_fast(manifest)


def _bootstrap_safe_fast_install(self) -> None:
    """Allow a full fallback run to promote the masters needed by the next Daily."""
    if self.enabled:
        return _ORIGINAL_FAST_INSTALL(self)

    def capture_save_master(frame, path):
        self.original_save_master(frame, path)
        name = Path(path).name
        if name == collection._ACTION_OUTPUT:
            self.captured["ACTION"] = frame.copy(deep=True)
        elif name == collection._ETF_OUTPUT:
            self.captured["ETF"] = frame.copy(deep=True)

    collection.legacy.save_master = capture_save_master


def _bootstrap_safe_fast_restore(self) -> None:
    if self.enabled:
        return _ORIGINAL_FAST_RESTORE(self)
    collection.legacy.save_master = self.original_save_master


def _safe_nonblocking(name: str, runner) -> tuple[dict, dict | None, float]:
    started = perf_counter()
    try:
        return runner(), None, perf_counter() - started
    except Exception as exc:
        return {}, {
            "step": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=5),
        }, perf_counter() - started


def _run_collection_optimized_locals() -> tuple[dict, dict]:
    """Daily collection: no W09 network, retained/seeded W09, functional fast state."""
    original_loader = collection._load_fast_state
    original_wave3 = collection.waves.wave3_local_features
    original_wave9 = collection.waves.wave9_topdown
    original_prefetch = collection.topdown_prefetch.fetch_external
    original_fixed_window = collection._fixed_window_fetcher
    original_runtime_install = collection.DailyFastRuntime.install
    original_runtime_restore = collection.DailyFastRuntime.restore
    diagnostics: dict = {
        "earnings_clock": {
            "status": "NOT_APPLIED_NO_FAST_STATE",
            "network_calls": 0,
            "source_timestamp_changed": False,
        },
        "wave3_cpu_budget": wave3_cpu.audit_contract(),
        "fast_state_bootstrap": {
            "status": "ENABLED",
            "capture_enriched_masters_on_full_fallback": True,
        },
        "fast_state_identity": {
            "exact_github_sha_required": False,
            "policy": "STATIC_DATA_CONTRACT_PLUS_COLLECTION_CODE_CONTRACT",
            "weekly_snapshot_fallback": True,
        },
        "wave09_daily_policy": {
            "status": "WEEKLY_ONLY",
            "daily_execution": False,
            "weekly_execution_unchanged": True,
            "daily_fred_calls": 0,
            "daily_gdelt_calls": 0,
            "daily_observations_applied": 0,
            "weekly_snapshot_reused_when_needed": True,
            "validated_seed_fallback": w09_seed.audit_contract(),
            "calls_intercepted": 0,
        },
    }

    def current_loader():
        actions, etf, manifest, mode = _load_fast_state_compatible()
        diagnostics["fast_state_identity"]["resolved_mode"] = mode
        diagnostics["fast_state_identity"]["source"] = manifest.get("source", "DAILY_FAST_STATE") if isinstance(manifest, dict) else "NONE"
        if mode in {"DELTA_ONLY", "RECONCILE_CACHE"} and not actions.empty:
            actions, clock = refresh_earnings_clock(actions)
            diagnostics["earnings_clock"] = {**clock, "status": "APPLIED", "fast_mode": mode}
        return actions, etf, manifest, mode

    def disabled_prefetch(prepared, *, fred_api_key):
        return collection.topdown_prefetch.ExternalTopdown(
            macro=None,
            news_results={},
            query_fingerprint="WAVE09_WEEKLY_ONLY_NO_DAILY_PREFETCH",
        )

    def weekly_only_wave9(actions_df, etf_df, cfg, fred_api_key):
        policy = diagnostics["wave09_daily_policy"]
        policy["calls_intercepted"] = int(policy.get("calls_intercepted", 0)) + 1
        mode = str(diagnostics.get("fast_state_identity", {}).get("resolved_mode") or "DISABLED")
        if mode == "DISABLED":
            obs_actions, seed_diag = w09_seed.action_observations(actions_df)
            policy["daily_observations_applied"] = int(len(obs_actions))
            policy["bootstrap_source"] = "VALIDATED_W09_SEED_RUN_32626511307"
            return obs_actions, [], seed_diag
        return [], [], {
            "status": "SKIPPED_DAILY_WEEKLY_ONLY_RETAINED_MASTER",
            "refresh_cadence": "WEEKLY_ONLY",
            "fred_calls": 0,
            "gdelt_calls": 0,
            "observations_applied": 0,
            "weekly_values_retained_in_input_master": True,
        }

    collection._load_fast_state = current_loader
    collection.waves.wave3_local_features = wave3_cpu.wave3_local_features
    collection.waves.wave9_topdown = weekly_only_wave9
    collection.topdown_prefetch.fetch_external = disabled_prefetch
    collection._fixed_window_fetcher = lambda _anchor, original_fetch: original_fetch
    collection.DailyFastRuntime.install = _bootstrap_safe_fast_install
    collection.DailyFastRuntime.restore = _bootstrap_safe_fast_restore
    try:
        return collection.run(), diagnostics
    finally:
        collection._load_fast_state = original_loader
        collection.waves.wave3_local_features = original_wave3
        collection.waves.wave9_topdown = original_wave9
        collection.topdown_prefetch.fetch_external = original_prefetch
        collection._fixed_window_fetcher = original_fixed_window
        collection.DailyFastRuntime.install = original_runtime_install
        collection.DailyFastRuntime.restore = original_runtime_restore


def _patch_fast_collection_audit() -> None:
    path = collection.AUDIT
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    payload["wave09_refresh_cadence"] = "WEEKLY_ONLY"
    payload["wave09_daily_network_calls"] = 0
    payload["topdown_external_prefetch_started_at_pipeline_start"] = False
    payload["topdown_prefetch_reused"] = False
    payload["topdown_prefetch_fail_closed_fallback"] = False
    payload["gdelt_exact_window_used_for_prefetch"] = False
    payload["daily_fast_audit_semantics_v21_15_5"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _stamp_collection_contract(root: Path = ROOT) -> None:
    path = collection.MANIFEST
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if manifest.get("version") != collection.VERSION or manifest.get("validated") is not True:
        return
    manifest["daily_collection_contract_version"] = CACHE_CONTRACT_VERSION
    manifest["daily_collection_code_contract"] = _collection_code_contract(root)
    manifest["github_sha_is_cache_identity"] = False
    manifest["github_sha_retained_for_audit_only"] = True
    manifest["wave09_refresh_cadence"] = "WEEKLY_ONLY"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _write_consolidated_audit(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_4.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_5.json").write_text(text, encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    """Final production Daily entrypoint."""
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    collection_started = perf_counter()
    collection_payload, local_optimizations = _run_collection_optimized_locals()
    collection_seconds = perf_counter() - collection_started
    _patch_fast_collection_audit()
    _stamp_collection_contract(root)

    replay_payload, replay_error, replay_seconds = _safe_nonblocking(
        "ETF_STRUCTURE_STATE_REPLAY",
        lambda: etf_replay.run(root=root),
    )

    tactical_started = perf_counter()
    tactical_payload = tactical.run(root=root)
    tactical_seconds = perf_counter() - tactical_started

    payload = {
        "status": "SUCCESS_DAILY_CONSOLIDATED" if replay_error is None else "SUCCESS_DAILY_CONSOLIDATED_WITH_ETF_REPLAY_WARNING",
        "version": VERSION,
        "single_python_process": True,
        "daily_cache_exact_sha_dependency_removed": True,
        "wave09_refresh_cadence": "WEEKLY_ONLY",
        "weekly_master_snapshot_fallback": True,
        "weekly_full_research_preserved": True,
        "tactical_runtime_version": tactical.VERSION,
        "local_optimizations": local_optimizations,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
        "steps": {
            "collection": {
                "status": collection_payload.get("status"),
                "fast_mode": collection_payload.get("daily_fast_collection", {}).get("mode"),
                "fast_state_promoted": collection_payload.get("daily_fast_collection", {}).get("promoted"),
            },
            "etf_structure_replay": {
                "status": replay_payload.get("status"),
                "error": replay_error,
            },
            "tactical_dag": {
                "status": tactical_payload.get("status"),
                "version": tactical_payload.get("version"),
            },
        },
        "timings_seconds": {
            "collection": round(float(collection_seconds), 6),
            "etf_structure_replay": round(float(replay_seconds), 6),
            "tactical_dag": round(float(tactical_seconds), 6),
            "total": round(float(perf_counter() - started), 6),
        },
    }
    _write_consolidated_audit(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))