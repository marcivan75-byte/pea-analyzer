from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os

from v182.reporting import daily_consolidated_runner_v21_15_4 as base
from v182.reporting import daily_tactical_super_runner_v21_15_5 as tactical_v155


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_5"
CACHE_CONTRACT_VERSION = "DAILY_COLLECTION_COMPAT_V21_15_5"
CACHE_CONTRACT_FILES = (
    "src/v182/reporting/run.py",
    "src/v182/reporting/waves.py",
    "src/v182/reporting/daily_fast_collection_run.py",
    "src/v182/io/frames.py",
    "src/v182/sources/yfinance_info.py",
    "src/v182/sources/finnhub_consensus.py",
)


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


def _load_fast_state_compatible() -> tuple:
    """Reuse Daily state across unrelated commits while keeping functional gates.

    The previous exact-GITHUB_SHA gate invalidated retained masters after workflow,
    documentation or tactical-only commits. This loader keeps every substantive
    state/data validation and adds a collection-code contract; only the coarse
    repository-wide SHA equality check is removed.
    """
    collection = base.collection
    if os.environ.get("PEA_RUN_PROFILE", "").strip().upper() != "DAILY_TACTICAL":
        return collection._load_fast_state_original_v21_15_5()

    manifest = collection._load_manifest()
    if manifest.get("version") != collection.VERSION or manifest.get("validated") is not True:
        return collection._empty_fast_state_v21_15_5(manifest)
    if manifest.get("static_contract") != collection._static_contract():
        return collection._empty_fast_state_v21_15_5(manifest)

    recorded_contract = str(manifest.get("daily_collection_code_contract") or "").strip()
    current_contract = _collection_code_contract(ROOT)
    if recorded_contract and recorded_contract != current_contract:
        return collection._empty_fast_state_v21_15_5(manifest)

    if manifest.get("actions_sha256") != collection._sha256_file(collection.ACTIONS_STATE):
        return collection._empty_fast_state_v21_15_5(manifest)
    if manifest.get("etf_sha256") != collection._sha256_file(collection.ETF_STATE):
        return collection._empty_fast_state_v21_15_5(manifest)

    actions = collection._read_fast_frame(collection.ACTIONS_STATE)
    etf = collection._read_fast_frame(collection.ETF_STATE)
    if not collection._valid_fast_frame(actions, expected_rows=int(manifest.get("actions_rows", 0) or 0)):
        return collection._empty_fast_state_v21_15_5(manifest)
    if not collection._valid_fast_frame(etf, expected_rows=int(manifest.get("etf_rows", 0) or 0)):
        return collection._empty_fast_state_v21_15_5(manifest)

    mode = "DELTA_ONLY" if manifest.get("cache_contract") == collection._cache_contract() else "RECONCILE_CACHE"
    return actions, etf, manifest, mode


def _install_compat_helpers() -> None:
    collection = base.collection
    if not hasattr(collection, "_load_fast_state_original_v21_15_5"):
        collection._load_fast_state_original_v21_15_5 = collection._load_fast_state
    if not hasattr(collection, "_empty_fast_state_v21_15_5"):
        def empty(manifest):
            import pandas as pd
            return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"
        collection._empty_fast_state_v21_15_5 = empty


def _stamp_collection_contract(root: Path = ROOT) -> None:
    collection = base.collection
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


def _patch_consolidated_audit(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    enriched = dict(payload or {})
    enriched["version"] = VERSION
    enriched["daily_cache_identity"] = {
        "policy": "STATIC_DATA_CONTRACT_PLUS_COLLECTION_CODE_CONTRACT",
        "exact_github_sha_required": False,
        "github_sha_audit_only": True,
        "collection_code_contract": _collection_code_contract(root),
        "collection_contract_version": CACHE_CONTRACT_VERSION,
    }
    enriched["wave09_refresh_cadence"] = "WEEKLY_ONLY"
    enriched["weekly_full_research_preserved"] = True
    text = json.dumps(enriched, ensure_ascii=False, indent=2, default=str)
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_4.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_5.json").write_text(text, encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    """Final consolidated Daily runtime with functional cache identity and V21.15.5 tactical scope."""
    _install_compat_helpers()
    collection = base.collection
    original_loader = collection._load_fast_state
    original_tactical = base.tactical
    original_version = base.VERSION

    collection._load_fast_state = _load_fast_state_compatible
    base.tactical = tactical_v155
    base.VERSION = VERSION
    try:
        payload = base.run(root=root)
    finally:
        collection._load_fast_state = original_loader
        base.tactical = original_tactical
        base.VERSION = original_version

    _stamp_collection_contract(root)
    payload = dict(payload or {})
    payload["version"] = VERSION
    payload["daily_cache_exact_sha_dependency_removed"] = True
    payload["wave09_refresh_cadence"] = "WEEKLY_ONLY"
    payload["tactical_runtime_version"] = tactical_v155.VERSION
    _patch_consolidated_audit(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
