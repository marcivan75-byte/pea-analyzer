from __future__ import annotations

import json

from v182.reporting import ci_entry_watch_v22_2
from v182.reporting import daily_consolidated_runner_v21_15_7 as impl

ROOT = impl.ROOT
VERSION = impl.VERSION
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


def run(root=ROOT):
    """Run the existing Daily, then refresh only current CI candidates.

    V22.2 reuses Committee outputs and persistent OHLCV. It adds no broad-universe
    network collection and remains non-blocking for the historical Daily runtime.
    """
    payload = impl.run(root=root)
    try:
        watch = ci_entry_watch_v22_2.run(root=root)
    except Exception as exc:
        watch = {"status": "FAILED_NON_BLOCKING", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["ci_entry_watch_v22_2"] = watch
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
