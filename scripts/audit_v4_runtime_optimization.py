from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter
import argparse
import json

import pandas as pd

from v182.reporting import ci_light_v4, ci_selection_gate_v4


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
    gate = ci_selection_gate_v4.run(root=root, ensure_upstream=False)
    if gate.get("status") != "SUCCESS":
        raise RuntimeError(f"V4_GATE_FAILED:{gate.get('status')}")
    standalone_seconds: list[float] = []
    reuse_seconds: list[float] = []
    fingerprints: list[dict] = []
    reuse_payload: dict = {}
    standalone_payload: dict = {}
    for index in range(iterations):
        started = perf_counter()
        standalone_payload = ci_light_v4.run(root=root, reuse_selection_context=False)
        standalone_seconds.append(perf_counter() - started)
        standalone_hash, standalone_rows, fields = _decision_fingerprint(root)

        started = perf_counter()
        reuse_payload = ci_light_v4.run(root=root, reuse_selection_context=True)
        reuse_seconds.append(perf_counter() - started)
        reuse_hash, reuse_rows, _ = _decision_fingerprint(root)
        fingerprints.append(
            {
                "iteration": index + 1,
                "standalone_sha256": standalone_hash,
                "reuse_sha256": reuse_hash,
                "standalone_rows": standalone_rows,
                "reuse_rows": reuse_rows,
                "equal": standalone_hash == reuse_hash and standalone_rows == reuse_rows,
            }
        )
    standalone_median = median(standalone_seconds)
    reuse_median = median(reuse_seconds)
    speedup_pct = 100.0 * (standalone_median - reuse_median) / standalone_median if standalone_median else 0.0
    checks = {
        "decision_information_identical": all(item["equal"] for item in fingerprints),
        "candidate_population_preserved": all(item["reuse_rows"] == int(gate["input_candidates"]) for item in fingerprints),
        "selection_context_reused": reuse_payload.get("source_context_reused") is True,
        "second_source_collection_eliminated": reuse_payload.get("source_collection_passes") == 0,
        "standalone_contract_preserved": standalone_payload.get("source_collection_passes") == 1,
        "reuse_phase_faster_than_standalone": reuse_median < standalone_median,
        "source_cannot_create_candidate": reuse_payload.get("source_can_create_candidate") is False,
        "real_orders_disabled": reuse_payload.get("real_orders_enabled") is False,
    }
    payload = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "version": "WEEKLY_V4_RUNTIME_OPTIMIZATION_AUDIT_1",
        "iterations": iterations,
        "checks": checks,
        "decision_fields_compared": fields,
        "fingerprints": fingerprints,
        "timings_seconds": {
            "standalone_samples": [round(value, 6) for value in standalone_seconds],
            "reuse_samples": [round(value, 6) for value in reuse_seconds],
            "standalone_median": round(standalone_median, 6),
            "reuse_median": round(reuse_median, 6),
            "ci_light_speedup_pct": round(speedup_pct, 2),
        },
        "information_loss": False if checks["decision_information_identical"] else True,
        "network_collection_passes_before": 2,
        "network_collection_passes_after": 1,
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
