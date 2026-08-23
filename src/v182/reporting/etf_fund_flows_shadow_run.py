from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import json
import time

import pandas as pd

from v182.features.etf_fund_flows_v1 import build_flow_computation, load_config
from v182.reporting.fund_flow_same_day_reuse import (
    load_same_day_reuse,
    merge_reuse_entries,
    successful_snapshot_entries,
    write_same_day_reuse_marker,
)
from v182.sources.etf_fund_flows import (
    build_pea_flow_universe,
    collect_current_snapshot,
    load_external_flow_universe,
    load_official_observations,
)


ROOT = Path(__file__).resolve().parents[3]


def _read_pea_master(root: Path) -> tuple[pd.DataFrame, str]:
    enriched = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    fallback = root / "inputs" / "V18.2_PEA_ETF_MASTER.csv"
    path = enriched if enriched.exists() else fallback
    if not path.exists():
        raise FileNotFoundError("PEA_ETF_MASTER_NOT_FOUND")
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False), str(path.relative_to(root))


def _append_observation_history(path: Path, current: pd.DataFrame) -> pd.DataFrame:
    prior = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False) if path.exists() else pd.DataFrame()
    frames = [frame for frame in (prior, current) if not frame.empty]
    if not frames:
        return pd.DataFrame()
    history = pd.concat(frames, ignore_index=True, sort=False)
    history["as_of"] = pd.to_datetime(history["as_of"], errors="coerce", utc=True)
    history = history[history["as_of"].notna()].copy()
    history["as_of"] = history["as_of"].dt.date.astype(str)
    source_priority = history["source_priority"] if "source_priority" in history.columns else pd.Series(0, index=history.index)
    history["source_priority"] = pd.to_numeric(source_priority, errors="coerce").fillna(0)
    confidence = history.get("confidence", pd.Series("", index=history.index)).astype(str).str.upper()
    history["_confidence_rank"] = confidence.map({"A": 4, "B": 3, "C": 2, "D": 1, "QUARANTINE": 0}).fillna(0)
    history = history.sort_values(
        ["instrument_id", "as_of", "source_priority", "_confidence_rank"],
        ascending=[True, True, False, False],
    )
    history = history.drop_duplicates(["instrument_id", "as_of"], keep="first").drop(columns="_confidence_rank")
    path.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    return history.reset_index(drop=True)


def _write_markdown(instruments: pd.DataFrame, rotations: pd.DataFrame, diagnostics: dict, path: Path) -> None:
    lines = [
        "# ETF Fund Flows V1 — SHADOW",
        "",
        f"- Version: `{diagnostics.get('version')}`",
        f"- Instruments observés: **{diagnostics.get('instruments', 0)}**",
        f"- Instruments scorables: **{diagnostics.get('scorable_instruments', 0)}**",
        f"- ETF PEA observés: **{diagnostics.get('pea_instruments', 0)}**",
        "- Influence décisionnelle: **0**",
        "- Ordres réels: **désactivés**",
        "",
        "## ETF PEA — accumulation",
        "",
    ]
    pea = instruments[instruments["is_pea"].fillna(False).astype(bool)].copy() if not instruments.empty else pd.DataFrame()
    if not pea.empty:
        pea = pea.sort_values("pea_flow_overlay_shadow", ascending=False, na_position="last").head(10)
        for _, row in pea.iterrows():
            score = row.get("pea_flow_overlay_shadow")
            score_text = "n/a" if pd.isna(score) else f"{float(score):.1f}"
            lines.append(
                f"- {row.get('name', row.get('instrument_id'))}: overlay {score_text} — "
                f"{row.get('flow_price_state', 'n/a')} — {row.get('efs_readiness', 'n/a')}"
            )
    else:
        lines.append("- Historique insuffisant ou collecte indisponible.")
    lines.extend(["", "## Rotation secteurs / thèmes", ""])
    if not rotations.empty:
        for _, row in rotations.head(10).iterrows():
            score = row.get("srfs_shadow")
            score_text = "n/a" if pd.isna(score) else f"{float(score):.1f}"
            lines.append(f"- {row.get('sector_or_theme')}: SRFS {score_text} — {row.get('flow_price_state', 'n/a')}")
    else:
        lines.append("- Historique insuffisant.")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _collect_snapshot_parallel(
    universe: pd.DataFrame,
    official: pd.DataFrame,
    *,
    max_workers: int,
    chunk_size: int,
    delay_seconds: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Run the existing validated collector in bounded independent chunks."""
    started = time.perf_counter()
    if universe.empty:
        return pd.DataFrame(), pd.DataFrame(), {"mode": "EMPTY", "runtime_seconds": 0.0, "chunks": 0, "workers": 0}
    size = max(1, int(chunk_size))
    chunks = [universe.iloc[start : start + size].copy() for start in range(0, len(universe), size)]
    workers = max(1, min(int(max_workers), len(chunks)))
    snapshots = []
    failures = []

    def _run_chunk(chunk: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        ids = set(chunk["instrument_id"].astype(str))
        official_chunk = (
            official[official["instrument_id"].astype(str).isin(ids)].copy() if not official.empty else pd.DataFrame()
        )
        return collect_current_snapshot(chunk, official_input=official_chunk, delay_seconds=delay_seconds)

    if workers == 1:
        for chunk in chunks:
            snap, fail = _run_chunk(chunk)
            snapshots.append(snap)
            failures.append(fail)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_run_chunk, chunk) for chunk in chunks]
            for future in as_completed(futures):
                snap, fail = future.result()
                snapshots.append(snap)
                failures.append(fail)
    snapshot_frames = [frame for frame in snapshots if frame is not None and not frame.empty]
    failure_frames = [frame for frame in failures if frame is not None and not frame.empty]
    snapshot = pd.concat(snapshot_frames, ignore_index=True, sort=False) if snapshot_frames else pd.DataFrame()
    failed = pd.concat(failure_frames, ignore_index=True, sort=False) if failure_frames else pd.DataFrame()
    elapsed = round(time.perf_counter() - started, 3)
    metrics = {
        "mode": "BOUNDED_PARALLEL_CHUNKS" if workers > 1 else "SERIAL_FALLBACK",
        "runtime_seconds": elapsed,
        "universe_count": int(len(universe)),
        "chunks": int(len(chunks)),
        "chunk_size": size,
        "workers": workers,
        "request_start_delay_seconds_per_worker": float(delay_seconds),
        "snapshot_rows": int(len(snapshot)),
        "collection_failures": int(len(failed)),
        "history_rebuilt": False,
        "decision_logic_changed": False,
    }
    return snapshot, failed, metrics


def run(root: Path = ROOT) -> dict:
    run_now = datetime.now(timezone.utc)
    cfg = load_config(root / "config" / "ETF_FUND_FLOW_V1_SHADOW.json")
    try:
        master_cfg = json.loads((root / "config" / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    except Exception:
        master_cfg = {}
    runtime_opt = master_cfg.get("runtime_optimization", {}).get("etf_fund_flows", {})
    master, master_source = _read_pea_master(root)
    pea_universe = build_pea_flow_universe(master)
    external = load_external_flow_universe(root / "config" / "ETF_FUND_FLOW_EXTERNAL_UNIVERSE_V1.csv")
    universe = pd.concat([pea_universe, external], ignore_index=True, sort=False)
    if universe["instrument_id"].duplicated().any():
        duplicates = sorted(
            universe.loc[universe["instrument_id"].duplicated(keep=False), "instrument_id"].astype(str).unique()
        )
        raise RuntimeError(f"ETF_FLOW_DUPLICATE_INSTRUMENT_ID:{','.join(duplicates[:20])}")

    state_dir = root / "state" / "etf_fund_flows"
    out_dir = root / "outputs" / "etf_fund_flows"
    audit_dir = root / "outputs" / "audit"
    gaps_dir = root / "outputs" / "gaps"
    for directory in (state_dir, out_dir, audit_dir, gaps_dir):
        directory.mkdir(parents=True, exist_ok=True)
    history_path = state_dir / "ETF_FUND_FLOW_OBSERVATIONS.csv"
    reuse_marker_path = state_dir / "ETF_FUND_FLOW_SAME_DAY_REUSE_V1.json"

    official = load_official_observations(root / "inputs" / "ETF_FUND_FLOW_OFFICIAL_OBSERVATIONS.csv")
    known_ids = set(universe["instrument_id"].astype(str))
    official_failures = pd.DataFrame()
    if not official.empty:
        unknown_mask = ~official["instrument_id"].astype(str).isin(known_ids)
        if unknown_mask.any():
            official_failures = official.loc[unknown_mask, ["instrument_id"]].copy()
            official_failures["stage"] = "OFFICIAL_INPUT"
            official_failures["reason"] = "UNKNOWN_INSTRUMENT_ID"
            official = official.loc[~unknown_mask].copy()

    reuse_enabled = bool(runtime_opt.get("reuse_previous_snapshot", False))
    reusable_ids, prior_reuse_entries, reuse_metrics = load_same_day_reuse(
        reuse_marker_path,
        history_path,
        universe,
        official,
        enabled=reuse_enabled,
        now=run_now,
    )
    collection_universe = universe[~universe["instrument_id"].astype(str).isin(reusable_ids)].copy()
    collection_official = (
        official[official["instrument_id"].astype(str).isin(set(collection_universe["instrument_id"].astype(str)))].copy()
        if not official.empty
        else pd.DataFrame()
    )
    snapshot, failures, collection_metrics = _collect_snapshot_parallel(
        collection_universe,
        collection_official,
        max_workers=int(runtime_opt.get("max_workers", 8)),
        chunk_size=int(runtime_opt.get("chunk_size", 18)),
        delay_seconds=float(runtime_opt.get("request_start_delay_seconds", 0.08)),
    )
    network_universe_count = int(collection_metrics.get("universe_count", len(collection_universe)))
    collection_metrics.update(reuse_metrics)
    collection_metrics["universe_count"] = int(len(universe))
    collection_metrics["network_universe_count"] = network_universe_count
    collection_metrics["network_instruments_avoided"] = int(len(reusable_ids))
    if reusable_ids and collection_universe.empty:
        collection_metrics["mode"] = "SAME_DAY_FULL_REUSE"
    elif reusable_ids:
        collection_metrics["mode"] = "SAME_DAY_PARTIAL_REUSE"

    failure_frames = [frame for frame in (official_failures, failures) if not frame.empty]
    failures = pd.concat(failure_frames, ignore_index=True, sort=False) if failure_frames else pd.DataFrame()
    collection_metrics["collection_failures_total"] = int(len(failures))
    (audit_dir / "ETF_FUND_FLOW_COLLECTION_RUNTIME_V21_13_2.json").write_text(
        json.dumps(collection_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    history = _append_observation_history(history_path, snapshot)
    generated = datetime.now(timezone.utc).isoformat()
    current_success_entries = successful_snapshot_entries(snapshot, failures)
    reusable_entries = merge_reuse_entries(prior_reuse_entries, current_success_entries)

    if history.empty:
        payload = {
            "status": "NO_DATA",
            "version": cfg["version"],
            "generated_at_utc": generated,
            "master_source": master_source,
            "universe_count": int(len(universe)),
            "current_snapshot_rows": 0,
            "same_day_reused_instruments": int(len(reusable_ids)),
            "same_day_reused_snapshot_rows": int(len(prior_reuse_entries)),
            "network_collection_universe_count": int(len(collection_universe)),
            "collection_runtime": collection_metrics,
            "decision_influence": 0.0,
            "live_orders_enabled": False,
        }
        (audit_dir / "ETF_FUND_FLOW_V1_SHADOW.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not failures.empty:
            failures.to_csv(
                gaps_dir / "ETF_FUND_FLOW_COLLECTION_FAILURES.csv", sep=";", index=False, encoding="utf-8-sig"
            )
        return payload

    result = build_flow_computation(history, cfg)
    result.diagnostics["collection_runtime"] = collection_metrics
    instruments_path = out_dir / "ETF_FLOW_INSTRUMENTS_SHADOW.csv"
    families_path = out_dir / "ETF_FLOW_FAMILIES_SHADOW.csv"
    rotations_path = out_dir / "SECTOR_ROTATION_FLOW_OVERLAY_V1.csv"
    pea_path = out_dir / "TOP_PEA_FLOW_SHADOW.csv"
    outflows_path = out_dir / "TOP_OUTFLOWS_SHADOW.csv"
    mobile_path = root / "outputs" / "mobile" / "ETF_FUND_FLOWS_SHADOW.md"

    result.instruments.to_csv(instruments_path, sep=";", index=False, encoding="utf-8-sig")
    result.families.to_csv(families_path, sep=";", index=False, encoding="utf-8-sig")
    result.rotations.to_csv(rotations_path, sep=";", index=False, encoding="utf-8-sig")
    pea = result.instruments[result.instruments["is_pea"].fillna(False).astype(bool)].copy()
    pea.sort_values("pea_flow_overlay_shadow", ascending=False, na_position="last").head(25).to_csv(
        pea_path, sep=";", index=False, encoding="utf-8-sig"
    )
    result.instruments.sort_values("efs_shadow", ascending=True, na_position="last").head(25).to_csv(
        outflows_path, sep=";", index=False, encoding="utf-8-sig"
    )
    _write_markdown(result.instruments, result.rotations, result.diagnostics, mobile_path)

    if not failures.empty:
        failures.to_csv(
            gaps_dir / "ETF_FUND_FLOW_COLLECTION_FAILURES.csv", sep=";", index=False, encoding="utf-8-sig"
        )

    marker_written = False
    if reuse_enabled:
        write_same_day_reuse_marker(
            reuse_marker_path,
            universe,
            official,
            reusable_entries,
            now=run_now,
        )
        marker_written = True

    payload = dict(result.diagnostics)
    payload.update(
        {
            "status": "SUCCESS",
            "generated_at_utc": generated,
            "master_source": master_source,
            "universe_count": int(len(universe)),
            "pea_universe_count": int(len(pea_universe)),
            "external_universe_count": int(len(external)),
            "current_snapshot_rows": int(len(snapshot)),
            "same_day_reused_instruments": int(len(reusable_ids)),
            "same_day_reused_snapshot_rows": int(len(prior_reuse_entries)),
            "effective_snapshot_rows": int(len(snapshot) + len(prior_reuse_entries)),
            "network_collection_universe_count": int(len(collection_universe)),
            "same_day_reuse_marker": str(reuse_marker_path.relative_to(root)),
            "same_day_reuse_marker_written": marker_written,
            "collection_failures": int(len(failures)),
            "collection_runtime": collection_metrics,
            "state_history_path": str(history_path.relative_to(root)),
            "instrument_output": str(instruments_path.relative_to(root)),
            "family_output": str(families_path.relative_to(root)),
            "sector_rotation_overlay_output": str(rotations_path.relative_to(root)),
            "pea_top_output": str(pea_path.relative_to(root)),
            "top_outflows_output": str(outflows_path.relative_to(root)),
            "mobile_output": str(mobile_path.relative_to(root)),
            "governance": cfg["governance"],
            "weights_pre_registered_not_promoted": True,
            "sector_rotation_v2_locked_model_unchanged": True,
            "etf_mt_38_pit_core_unchanged": True,
            "one_day_flow_never_standalone_decision": True,
            "historical_backfill_from_current_snapshot": False,
            "decision_influence": 0.0,
            "live_orders_enabled": False,
        }
    )
    (audit_dir / "ETF_FUND_FLOW_V1_SHADOW.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
