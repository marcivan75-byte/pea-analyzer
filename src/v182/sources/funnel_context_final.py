from __future__ import annotations

import json

import v182.sources.funnel_context_fast as funnel
from v182.sources.eurostat_hicp_current import eurostat_hicp


def main() -> None:
    # Replace the legacy HICP helper inside the already hardened/parallel funnel
    # with the current ECOICOP2 collector. This keeps one execution path while
    # enforcing the 2026 coicop18=TOTAL contract and freshness gate.
    funnel._eurostat_hicp = eurostat_hicp
    result = funnel.apply()
    print("V20.5_FUNNEL_CONTEXT_FINAL_OK", json.dumps({
        "global_macro": result["global_macro"]["score"],
        "coverage": result["mean_context_coverage"],
        "gates": result["risk_gates"],
        "hicp": result["global_macro"].get("eurostat_hicp", {}),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
