from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.features.etf_fund_flows_v1 import build_flow_computation, load_config
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


def _load_weekly_crypto_control(path: Path) -> dict:
    base = {
        "status": "NO_DATA",
        "source_role": "WEEKLY_EXTERNAL_CONTROL_ONLY",
        "added_to_primary_flows": False,
        "decision_influence": 0.0,
        "live_orders_enabled": False,
    }
    if not path.exists() or path.stat().st_size == 0:
        return base
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    if frame.empty:
        return base
    required = {"week_end", "asset", "flow_usd_m", "source", "source_url", "confidence", "as_of"}
    missing = required - set(frame.columns)
    if missing:
        return {**base, "status": "INVALID_INPUT", "reason": f"MISSING_COLUMNS:{','.join(sorted(missing))}"}
    frame["week_end"] = pd.to_datetime(frame["week_end"], errors="coerce", utc=True)
    frame["as_of"] = pd.to_datetime(frame["as_of"], errors="coerce", utc=True)
    frame["flow_usd_m"] = pd.to_numeric(frame["flow_usd_m"], errors="coerce")
    frame["confidence"] = frame["confidence"].astype(str).str.upper()
    now = pd.Timestamp.now(tz="UTC")
    valid = (
        frame["week_end"].notna()
        & frame["as_of"].notna()
        & frame["flow_usd_m"].notna()
        & frame["week_end"].le(now)
        & frame["as_of"].le(now)
        & frame["confidence"].isin(["A", "B", "C"])
        & frame["source_url"].fillna("").astype(str).str.startswith(("https://", "http://"))
    )
    rejected = int((~valid).sum())
    frame = frame[valid].copy()
    if frame.empty:
        return {**base, "status": "NO_VALID_ROWS", "rejected_rows": rejected}
    latest_week = frame["week_end"].max()
    latest = frame[frame["week_end"].eq(latest_week)].copy()
    by_asset = latest.groupby("asset", dropna=False)["flow_usd_m"].sum().round(6).to_dict()
    return {
        **base,
        "status": "SUCCESS",
        "week_end": latest_week.date().isoformat(),
        "rows": int(len(latest)),
        "rejected_rows": rejected,
        "flow_usd_m_by_asset": {str(key): float(value) for key, value in by_asset.items()},
        "control_total_usd_m": float(latest["flow_usd_m"].sum()),
        "sources": sorted(set(latest["source"].astype(str))),
    }


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
    gold_crypto = diagnostics.get("gold_crypto", {})
    weekly_control = diagnostics.get("crypto_weekly_external_control", {})
    lines.extend(
        [
            "",
            "## Or & crypto",
            "",
            f"```json\n{json.dumps(gold_crypto, ensure_ascii=False, indent=2)}\n```",
            "",
            "## Contrôle crypto hebdomadaire externe",
            "",
            "Ce contrôle n'est jamais additionné aux flux ETF/ETP primaires.",
            "",
            f"```json\n{json.dumps(weekly_control, ensure_ascii=False, indent=2)}\n```",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    cfg = load_config(root / "config" / "ETF_FUND_FLOW_V1_SHADOW.json")
    master, master_source = _read_pea_master(root)
    pea_universe = build_pea_flow_universe(master)
    external = load_external_flow_universe(root / "config" / "ETF_FUND_FLOW_EXTERNAL_UNIVERSE_V1.csv")
    universe = pd.concat([pea_universe, external], ignore_index=True, sort=False)
    if universe["instrument_id"].duplicated().any():
        duplicates = sorted(universe.loc[universe["instrument_id"].duplicated(keep=False), "instrument_id"].astype(str).unique())
        raise RuntimeError(f"ETF_FLOW_DUPLICATE_INSTRUMENT_ID:{','.join(duplicates[:20])}")

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
    snapshot, failures = collect_current_snapshot(universe, official_input=official)
    failure_frames = [frame for frame in (official_failures, failures) if not frame.empty]
    failures = pd.concat(failure_frames, ignore_index=True, sort=False) if failure_frames else pd.DataFrame()

    state_dir = root / "state" / "etf_fund_flows"
    out_dir = root / "outputs" / "etf_fund_flows"
    audit_dir = root / "outputs" / "audit"
    gaps_dir = root / "outputs" / "gaps"
    for directory in (state_dir, out_dir, audit_dir, gaps_dir):
        directory.mkdir(parents=True, exist_ok=True)

    weekly_control = _load_weekly_crypto_control(root / "inputs" / "CRYPTO_FUND_FLOW_WEEKLY_CONTROL.csv")
    weekly_control_path = out_dir / "CRYPTO_WEEKLY_EXTERNAL_CONTROL.json"
    weekly_control_path.write_text(json.dumps(weekly_control, ensure_ascii=False, indent=2), encoding="utf-8")

    history_path = state_dir / "ETF_FUND_FLOW_OBSERVATIONS.csv"
    history = _append_observation_history(history_path, snapshot)
    generated = datetime.now(timezone.utc).isoformat()

    if history.empty:
        payload = {
            "status": "NO_DATA",
            "version": cfg["version"],
            "generated_at_utc": generated,
            "master_source": master_source,
            "universe_count": int(len(universe)),
            "current_snapshot_rows": 0,
            "crypto_weekly_external_control": weekly_control,
            "decision_influence": 0.0,
            "live_orders_enabled": False,
        }
        (audit_dir / "ETF_FUND_FLOW_V1_SHADOW.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not failures.empty:
            failures.to_csv(gaps_dir / "ETF_FUND_FLOW_COLLECTION_FAILURES.csv", sep=";", index=False, encoding="utf-8-sig")
        return payload

    result = build_flow_computation(history, cfg)
    result.diagnostics["crypto_weekly_external_control"] = weekly_control
    instruments_path = out_dir / "ETF_FLOW_INSTRUMENTS_SHADOW.csv"
    families_path = out_dir / "ETF_FLOW_FAMILIES_SHADOW.csv"
    rotations_path = out_dir / "SECTOR_ROTATION_FLOW_OVERLAY_V1.csv"
    pea_path = out_dir / "TOP_PEA_FLOW_SHADOW.csv"
    outflows_path = out_dir / "TOP_OUTFLOWS_SHADOW.csv"
    gold_crypto_path = out_dir / "GOLD_CRYPTO_FLOWS_SHADOW.json"
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
    gold_crypto_path.write_text(
        json.dumps(result.diagnostics.get("gold_crypto", {}), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _write_markdown(result.instruments, result.rotations, result.diagnostics, mobile_path)

    if not failures.empty:
        failures.to_csv(gaps_dir / "ETF_FUND_FLOW_COLLECTION_FAILURES.csv", sep=";", index=False, encoding="utf-8-sig")

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
            "collection_failures": int(len(failures)),
            "state_history_path": str(history_path.relative_to(root)),
            "instrument_output": str(instruments_path.relative_to(root)),
            "family_output": str(families_path.relative_to(root)),
            "sector_rotation_overlay_output": str(rotations_path.relative_to(root)),
            "pea_top_output": str(pea_path.relative_to(root)),
            "top_outflows_output": str(outflows_path.relative_to(root)),
            "gold_crypto_output": str(gold_crypto_path.relative_to(root)),
            "crypto_weekly_external_control_output": str(weekly_control_path.relative_to(root)),
            "mobile_output": str(mobile_path.relative_to(root)),
            "governance": cfg["governance"],
            "weights_pre_registered_not_promoted": True,
            "sector_rotation_v2_locked_model_unchanged": True,
            "etf_mt_38_pit_core_unchanged": True,
            "one_day_flow_never_standalone_decision": True,
            "historical_backfill_from_current_snapshot": False,
            "coinshares_control_added_to_primary_flows": False,
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
