from __future__ import annotations

import json

from v182.reporting import daily_consolidated_runner_v21_15_9 as impl


# Compatibility entrypoint retained because the deployed workflow historically
# invokes V21.15.4. Runtime authority now lives in finalized V21.15.9.
ROOT = impl.ROOT
VERSION = impl.VERSION
run = impl.run

# Compatibility aliases retained for tests/downstream imports that used the old
# V21.15.4 module as a facade.
base = impl.base
collection = impl.collection
etf_replay = impl.etf_replay
wave3_cpu = impl.wave3_cpu
refresh_earnings_clock = impl.refresh_earnings_clock
tactical = impl.tactical

_bootstrap_safe_fast_install = impl.base._bootstrap_safe_fast_install
_bootstrap_safe_fast_restore = impl.base._bootstrap_safe_fast_restore
_safe_nonblocking = impl.base._safe_nonblocking
_run_collection_optimized_locals = impl.base._run_collection_optimized_locals
_collection_code_contract = impl.base._collection_code_contract
_load_fast_state_compatible = impl.base._load_fast_state_compatible


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
