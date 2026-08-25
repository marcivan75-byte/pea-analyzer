from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import time
from typing import Any

from .cache import DiskTTLCache
from .committee import committee_csv, committee_markdown, committee_payload
from .features import build_features
from .http import JsonHttpClient
from .io import json_text, load_json, write_json_atomic, write_text_atomic
from .scoring import score_universe
from .sources import CryptoCollector
from .timing import apply_timing_overlay
from .validation import load_configs, validate_loaded_configs


def run_pipeline(root: Path, *, snapshot_path: Path | None = None, as_of: str | None = None) -> dict[str, Any]:
    pipeline_started = time.perf_counter()
    phases: dict[str, float] = {}

    phase_started = time.perf_counter()
    governance, sources, universe, criteria = load_configs(root)
    validation = validate_loaded_configs(governance, sources, universe, criteria)
    phases["validation_seconds"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    if snapshot_path:
        snapshot = load_json(snapshot_path)
        if as_of and snapshot.get("as_of") != as_of:
            snapshot = {**snapshot, "as_of": as_of}
    else:
        cache = DiskTTLCache(root / "data" / "cache")
        client = JsonHttpClient(cache, timeout=governance["runtime"]["request_timeout_seconds"], retries=governance["runtime"]["retries"])
        collector = CryptoCollector(client, root, governance)
        resolved_universe, market_rows = collector.discover_top_market_cap(universe)
        snapshot = collector.collect(resolved_universe, as_of=as_of, preloaded_market_rows=market_rows)
        snapshot["universe_discovery"] = {
            "mode": universe["universe_mode"],
            "source": universe["ranking_source"],
            "target_count": universe["target_count"],
            "processed_count": len(resolved_universe),
            "minimum_rank": min(spec["market_cap_rank"] for spec in resolved_universe),
            "maximum_rank": max(spec["market_cap_rank"] for spec in resolved_universe),
            "discovered_ids": [spec["id"] for spec in resolved_universe],
        }
    phases["input_collection_seconds"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    snapshot_text = json_text(snapshot, pretty=False, sort_keys=True)
    fingerprint = sha256(snapshot_text.encode("utf-8")).hexdigest()
    phases["snapshot_canonicalization_seconds"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    features = build_features(snapshot, governance)
    phases["feature_engineering_seconds"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    rows = score_universe(features, governance)
    phases["scoring_seconds"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    rows, timing_audit = apply_timing_overlay(rows, snapshot, governance, root)
    phases["timing_overlay_seconds"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    asset_count = len(snapshot.get("assets", {}))
    universe_audit = snapshot.get("universe_discovery", {
        "mode": "SNAPSHOT_REPLAY",
        "source": "SNAPSHOT",
        "target_count": universe["target_count"],
        "processed_count": asset_count,
        "minimum_rank": 1 if asset_count else None,
        "maximum_rank": asset_count or None,
    })
    payload = committee_payload(
        rows,
        as_of=snapshot["as_of"],
        fingerprint=fingerprint,
        source_status=snapshot.get("source_status", {}),
        universe_audit=universe_audit,
    )
    payload["t1_t2"] = timing_audit
    phases["committee_seconds"] = time.perf_counter() - phase_started
    payload["runtime"] = {
        "collection_seconds": round(phases["input_collection_seconds"], 6),
        "feature_scoring_seconds": round(phases["feature_engineering_seconds"] + phases["scoring_seconds"], 6),
        "phase_seconds": {name: round(value, 6) for name, value in phases.items()},
    }
    payload["config_validation"] = validation

    phase_started = time.perf_counter()
    features_text = json_text(features, pretty=False)
    csv_text = committee_csv(rows)
    markdown_text = committee_markdown(payload)
    phases["core_serialization_seconds"] = time.perf_counter() - phase_started

    output = root / "outputs"
    core_artifacts = (
        (output / "snapshots" / "CRYPTO_SNAPSHOT_LATEST.json", snapshot_text + "\n"),
        (output / "features" / "CRYPTO_FEATURES_LATEST.json", features_text + "\n"),
        (output / "ci" / "CI_CRYPTO.csv", csv_text),
        (output / "ci" / "CI_CRYPTO.md", markdown_text),
    )
    phase_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(core_artifacts)) as executor:
        list(executor.map(lambda item: write_text_atomic(*item), core_artifacts))
    phases["core_persistence_seconds"] = time.perf_counter() - phase_started
    phases["pipeline_to_ci_ready_seconds"] = time.perf_counter() - pipeline_started
    payload["runtime"]["phase_seconds"] = {name: round(value, 6) for name, value in phases.items()}
    payload["runtime"]["total_to_ci_ready_seconds"] = round(phases["pipeline_to_ci_ready_seconds"], 6)

    run_audit = {
        "status": "PASS",
        "as_of": snapshot["as_of"],
        "snapshot_fingerprint": fingerprint,
        "rows": len(rows),
        "runtime": payload["runtime"],
        "real_orders_enabled": False,
        "automatic_weight_promotion": False,
        "t1_t2": timing_audit,
    }
    phase_started = time.perf_counter()
    final_artifacts = (
        (output / "ci" / "CI_CRYPTO.json", json_text(payload) + "\n"),
        (output / "audit" / "CRYPTO_RUN_AUDIT.json", json_text(run_audit) + "\n"),
    )
    with ThreadPoolExecutor(max_workers=len(final_artifacts)) as executor:
        list(executor.map(lambda item: write_text_atomic(*item), final_artifacts))
    phases["ci_finalization_seconds"] = time.perf_counter() - phase_started
    phases["total_measured_seconds"] = time.perf_counter() - pipeline_started
    payload["runtime"]["phase_seconds"] = {name: round(value, 6) for name, value in phases.items()}
    payload["runtime"]["total_seconds"] = round(phases["total_measured_seconds"], 6)
    write_json_atomic(output / "audit" / "CRYPTO_RUNTIME_PHASES.json", payload["runtime"])
    return payload
