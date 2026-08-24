from __future__ import annotations

import json
import shutil

from v182.reporting import ci_entry_confidence_v22_2
from v182.reporting import daily_consolidated_runner_v21_15_7 as impl


# Compatibility entrypoint retained because the deployed workflow historically
# invokes V21.15.4. Runtime authority now lives in finalized V21.15.7.
ROOT = impl.ROOT
VERSION = impl.VERSION

# Compatibility aliases retained for tests/downstream imports that used the old
# V21.15.4 module as a facade.
base = impl.base
collection = impl.base.collection
etf_replay = impl.base.etf_replay
wave3_cpu = impl.base.wave3_cpu
refresh_earnings_clock = impl.base.refresh_earnings_clock
tactical = impl.tactical

_bootstrap_safe_fast_install = impl.base._bootstrap_safe_fast_install
_bootstrap_safe_fast_restore = impl.base._bootstrap_safe_fast_restore
_safe_nonblocking = impl.base._safe_nonblocking
_run_collection_optimized_locals = impl.base._run_collection_optimized_locals
_collection_code_contract = impl.base._collection_code_contract
_load_fast_state_compatible = impl.base._load_fast_state_compatible


def _restore_ci_watch_state(root):
    runtime_state = root / ci_entry_confidence_v22_2.STATE
    cached_state = root / "state/provenance/CI_ENTRY_WATCH_V22_2_STATE.csv"
    if not runtime_state.exists() and cached_state.exists():
        runtime_state.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached_state, runtime_state)


def _checkpoint_ci_watch_state(root):
    runtime_state = root / ci_entry_confidence_v22_2.STATE
    cached_state = root / "state/provenance/CI_ENTRY_WATCH_V22_2_STATE.csv"
    if runtime_state.exists():
        cached_state.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(runtime_state, cached_state)


def run(root=ROOT):
    """Run existing Daily, then refresh only current CI candidates.

    The watch reuses Daily Committee decisions and OHLCV cache. No broad-universe
    network collection is added. Stability state is mirrored into state/provenance,
    which is already part of the consolidated Daily decision cache.
    """
    payload = impl.run(root=root)
    try:
        _restore_ci_watch_state(root)
        watch = ci_entry_confidence_v22_2.run(root=root)
        _checkpoint_ci_watch_state(root)
    except Exception as exc:
        watch = {"status": "FAILED_NON_BLOCKING", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["ci_entry_confidence_v22_2"] = watch
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
