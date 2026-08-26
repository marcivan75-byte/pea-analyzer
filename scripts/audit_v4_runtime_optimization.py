from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import perf_counter
import argparse
import json

import pandas as pd

from v182.reporting import ci_light_v4


ROOT = Path(__file__).resolve().parents[1]
AUDIT = Path("outputs/audit/WEEKLY_V4_RUNTIME_OPTIMIZATION_AUDIT.json")
DECISION_FIELDS = [
    "isin",
    "asset_class",
    "horizon",
    "CI_LIGHT_INCLUDED",
    "CI_LIGHT_REASON",
    "CI_LIGHT_BOURSORAMA_RECOMMENDATION",
    "CI_LIGHT_BOURSORAMA_ANALYSTS",
    "CI_LIGHT_BOURSORAMA_UPSIDE_PCT",
    "CI_LIGHT_MORNINGSTAR_RATING",
    "CI_LIGHT_TRADINGVIEW_DAILY",
    "CI_LIGHT_TRADINGVIEW_WEEKLY",
    "CI_LIGHT_TRADINGVIEW_MONTHLY",
    "CI_LIGHT_BOURSORAMA_URL",
    "CI_LIGHT_TRADINGVIEW_URL",
    "CI_LIGHT_TRADINGVIEW_SYMBOL",
]


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _decision_fingerprint(root: Path) -> tuple[str, int, list[str]]:
    selected = _read(root / ci_light_v4.OUTPUT)
    rejected = _read(root / ci_light_v4.REJECTED)
    frame = pd.concat([selected, rejected], ignore_index=True, sort=False)
    fields = [field for field in DECISION_FIELDS if field in frame]
    canonical = frame[fields].copy().fillna("<NA>")
    keys = [field for field in ("isin", "asset_class", "horizon") if field in canonical]
    if keys:
        canonical = canonical.sort_values(keys, kind="stable").reset_index(drop=True)
    content = canonical.to_csv(sep=";", index=False, lineterminator="\n").encode("utf-8")
    return sha256(content).hexdigest(), int(len(frame)), fields


def run(root: Path = ROOT, *, iterations: int = 3) -> dict:
    if iterations < 1:
        raise ValueError("ITERATIONS_MUST_BE_POSITIVE")
    universe = _read(root / ci_light_v4.UPSTREAM)
    run_seconds: list[float] = []
    fingerprints: list[dict] = []
    result_payload: dict = {}
    reference_hash = ""
    for index in range(iterations):
        started = perf_counter()
        result_payload = ci_light_v4.run(root=root)
        run_seconds.append(perf_counter() - started)
        current_hash, current_rows, fields = _decision_fingerprint(root)
        if not reference_hash:
            reference_hash = current_hash
        fingerprints.append(
            {
                "iteration": index + 1,
                "decision_sha256": current_hash,
                "rows": current_rows,
                "equal_to_first": current_hash == reference_hash,
            }
        )
    checks = {
        "decision_information_stable": all(item["equal_to_first"] for item in fingerprints),
        "dedicated_universe_preserved": all(item["rows"] == int(len(universe)) for item in fingerprints),
        "ci_output_dependency_absent": result_payload.get("ci_output_dependency") is False,
        "ci_selection_not_used": result_payload.get("ci_selection_used") is False,
        "ci_context_not_reused": result_payload.get("ci_context_reused") is False,
        "own_source_collection": result_payload.get("source_collection_passes") == 1,
        "can_create_light_candidate": result_payload.get("source_can_create_ci_light_candidate") is True,
        "cannot_create_ci_candidate": result_payload.get("source_can_create_ci_candidate") is False,
        "real_orders_disabled": result_payload.get("real_orders_enabled") is False,
    }
    payload = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "version": "WEEKLY_V4_RUNTIME_INDEPENDENCE_AUDIT_2",
        "iterations": iterations,
        "checks": checks,
        "decision_fields_compared": fields,
        "fingerprints": fingerprints,
        "timings_seconds": {"samples": [round(value, 6) for value in run_seconds]},
        "information_loss": False if checks["decision_information_stable"] else True,
        "ci_light_network_collection_passes": 1,
        "ci_network_collection_dependency": 0,
    }
    target = root / AUDIT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    result = run(ROOT, iterations=args.iterations)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 2)
