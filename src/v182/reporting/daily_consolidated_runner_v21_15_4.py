from __future__ import annotations

import json

from v182.reporting import daily_consolidated_runner_v21_15_5 as impl


# Compatibility entrypoint retained because the deployed workflow historically
# invokes V21.15.4. All runtime authority now lives in the finalized V21.15.5
# implementation; aliases below preserve existing tests and downstream imports.
ROOT = impl.ROOT
VERSION = impl.VERSION
collection = impl.collection
tactical = impl.tactical
etf_replay = impl.etf_replay
wave3_cpu = impl.wave3_cpu
refresh_earnings_clock = impl.refresh_earnings_clock

_bootstrap_safe_fast_install = impl._bootstrap_safe_fast_install
_bootstrap_safe_fast_restore = impl._bootstrap_safe_fast_restore
_safe_nonblocking = impl._safe_nonblocking
_run_collection_optimized_locals = impl._run_collection_optimized_locals
_collection_code_contract = impl._collection_code_contract
_load_fast_state_compatible = impl._load_fast_state_compatible
run = impl.run


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
