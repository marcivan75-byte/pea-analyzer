from __future__ import annotations

import json

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


def run(root=ROOT):
    """Run the existing Daily, then refresh only the current CI candidate watch.

    V22.2 uses the Committee decisions and OHLCV cache already produced/restored by
    Daily. It performs no new broad-universe network collection and never mutates
    the historical Daily score/decision outputs.
    """
    payload = impl.run(root=root)
    try:
        watch = ci_entry_confidence_v22_2.run(root=root)
    except Exception as exc:
        watch = {"status": "FAILED_NON_BLOCKING", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["ci_entry_confidence_v22_2"] = watch
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
