from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

import pandas as pd

from v182.reporting import daily_tct_ct_runner as daily
from v182.reporting.selected_source_enrichment import attach_master_identity, enrich_selected_rows
from v182.risk.entry_exit_governance_v21_8 import STATE_RELATIVE_PATH, _load_temporal_state


ROOT = Path(__file__).resolve().parents[3]
VERSION = "FRIDAY_TACTICAL_REUSE_V21_15_3"
KEYS = ["asset_class", "horizon", "isin"]


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_INPUT_MISSING:{path}")
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _assert_current_snapshot(frame: pd.DataFrame, label: str, now: datetime) -> None:
    if frame.empty or "generated_at_utc" not in frame.columns:
        raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_STALE_OR_UNDATED:{label}")
    stamps = pd.to_datetime(frame["generated_at_utc"], errors="coerce", utc=True).dropna()
    if stamps.empty:
        raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_INVALID_TIMESTAMP:{label}")
    current_date = now.astimezone(timezone.utc).date()
    if stamps.max().date() != current_date:
        raise RuntimeError(
            f"FRIDAY_TACTICAL_REUSE_NOT_CURRENT_DAY:{label}:{stamps.max().date()}:{current_date}"
        )


def _scope(decisions: pd.DataFrame) -> pd.DataFrame:
    required = set(KEYS)
    missing = required - set(decisions.columns)
    if missing:
        raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_MISSING_KEYS:{','.join(sorted(missing))}")
    work = decisions.copy()
    work["asset_class"] = work["asset_class"].astype(str).str.upper()
    work["horizon"] = work["horizon"].astype(str).str.upper()
    work["isin"] = work["isin"].astype(str).str.upper().str.strip()
    mask = (
        (work["asset_class"].eq("ACTION") & work["horizon"].isin(["TCT", "CT"]))
        | (work["asset_class"].eq("ETF") & work["horizon"].eq("CT"))
    )
    scoped = work.loc[mask].copy()
    if scoped.duplicated(KEYS).any():
        duplicates = scoped.loc[scoped.duplicated(KEYS, keep=False), KEYS].astype(str)
        raise RuntimeError(
            "FRIDAY_TACTICAL_REUSE_DUPLICATE_DECISION_KEYS:"
            + "|".join(duplicates.head(10).agg(":".join, axis=1).tolist())
        )
    return scoped.reset_index(drop=True)


def _attach_existing_governance(decisions: pd.DataFrame, governed: pd.DataFrame) -> pd.DataFrame:
    missing = set(KEYS) - set(governed.columns)
    if missing:
        raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_GOVERNANCE_MISSING_KEYS:{','.join(sorted(missing))}")
    gov = governed.copy()
    gov["asset_class"] = gov["asset_class"].astype(str).str.upper()
    gov["horizon"] = gov["horizon"].astype(str).str.upper()
    gov["isin"] = gov["isin"].astype(str).str.upper().str.strip()
    gov_mask = (
        (gov["asset_class"].eq("ACTION") & gov["horizon"].isin(["TCT", "CT"]))
        | (gov["asset_class"].eq("ETF") & gov["horizon"].eq("CT"))
    )
    gov = gov.loc[gov_mask].copy()
    if gov.duplicated(KEYS).any():
        raise RuntimeError("FRIDAY_TACTICAL_REUSE_DUPLICATE_GOVERNANCE_KEYS")

    governance_columns = [
        column
        for column in gov.columns
        if column.startswith("v21_8_") or column == "previous_v21_8_position_state"
    ]
    if "v21_8_position_state" not in governance_columns or "v21_8_entry_state" not in governance_columns:
        raise RuntimeError("FRIDAY_TACTICAL_REUSE_GOVERNANCE_FIELDS_MISSING")

    left = decisions.drop(columns=[column for column in governance_columns if column in decisions.columns], errors="ignore")
    merged = left.merge(gov[KEYS + governance_columns], on=KEYS, how="left", validate="one_to_one")
    missing_governance = merged["v21_8_position_state"].isna() | merged["v21_8_entry_state"].isna()
    if missing_governance.any():
        missing_keys = merged.loc[missing_governance, KEYS].astype(str).head(10).agg(":".join, axis=1).tolist()
        raise RuntimeError("FRIDAY_TACTICAL_REUSE_GOVERNANCE_JOIN_MISSING:" + "|".join(missing_keys))
    return merged


def _copy_tct_outputs(root: Path, outdir: Path) -> None:
    committee = root / "outputs" / "committee_master"
    for name in ("TCT_BASELINE_V24_1_8.csv", "TCT_SHADOW_V24_1_7.csv"):
        source = committee / name
        if not source.exists():
            raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_TCT_OUTPUT_MISSING:{name}")
        shutil.copy2(source, outdir / name)


def _committee_summary(root: Path) -> dict:
    path = root / "outputs" / "committee_master" / "SUMMARY.json"
    if not path.exists():
        raise RuntimeError("FRIDAY_TACTICAL_REUSE_COMMITTEE_SUMMARY_MISSING")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def run(root: Path = ROOT, now: datetime | None = None) -> dict:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    outputs = root / "outputs"
    outdir = outputs / "daily_tct_ct"
    mobile = outputs / "mobile"
    auditdir = outputs / "audit"
    for directory in (outdir, mobile, auditdir):
        directory.mkdir(parents=True, exist_ok=True)

    committee_decisions = _read(outputs / "committee_master" / "COMMITTEE_DECISIONS.csv")
    committee_governed = _read(outputs / "committee_master" / "V21_8_ENTRY_EXIT_CHALLENGER.csv")
    _assert_current_snapshot(committee_decisions, "COMMITTEE_DECISIONS", current)
    _assert_current_snapshot(committee_governed, "V21_8_ENTRY_EXIT_CHALLENGER", current)

    decisions = _scope(committee_decisions)
    actions = _read(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etfs = _read(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")

    source_input = attach_master_identity(decisions, actions, etfs)
    enriched, source_context = enrich_selected_rows(source_input, root, profile="DAILY_TCT_CT")
    generated_at = current.isoformat()
    enriched["generated_at_utc"] = generated_at
    enriched["live_orders_enabled"] = False
    enriched["daily_tactical_scope"] = True
    enriched.to_csv(outdir / "DAILY_TCT_CT_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")

    governed = _attach_existing_governance(enriched, committee_governed)
    governed["generated_at_utc"] = generated_at
    governed["live_orders_enabled"] = False
    governed["daily_tactical_scope"] = True
    governed.to_csv(outdir / "DAILY_TCT_CT_V21_8.csv", sep=";", index=False, encoding="utf-8-sig")

    _copy_tct_outputs(root, outdir)
    android_path = mobile / "ANDROID_DAILY_TCT_CT.md"
    android_path.write_text(daily._android_summary(governed, generated_at), encoding="utf-8")

    committee_summary = _committee_summary(root)
    temporal_state = _load_temporal_state(root / STATE_RELATIVE_PATH)
    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "scope": ["ACTION_TCT", "ACTION_CT", "ETF_CT"],
        "rows": int(len(governed)),
        "rows_by_asset_horizon": governed.groupby(["asset_class", "horizon"], dropna=False).size().reset_index(name="count").to_dict("records"),
        "selected_source_context": source_context,
        "tct_baseline": committee_summary.get("tct_baseline", {}),
        "tct_exact": committee_summary.get("tct_exact_timing", {}),
        "entry_exit_v21_8": {
            "position_states": governed["v21_8_position_state"].value_counts(dropna=False).to_dict(),
            "entry_states": governed["v21_8_entry_state"].value_counts(dropna=False).to_dict(),
            "temporal_state_rows": int(len(temporal_state)),
            "reused_from_current_committee": True,
            "temporal_state_write_skipped": True,
            "same_session_second_confirmation_forbidden": True,
        },
        "friday_current_committee_reuse": True,
        "action_ct_score_recompute_skipped": True,
        "etf_ct_score_recompute_skipped": True,
        "tct_baseline_recompute_skipped": True,
        "tct_exact_recompute_skipped": True,
        "v21_8_reapply_skipped": True,
        "temporal_state_advanced_second_time": False,
        "network_scope": "SELECTED_SOURCE_ENRICHMENT_ONLY",
        "decision_logic_changed": False,
        "weights_unchanged": True,
        "selection_thresholds_unchanged": True,
        "holdout_opened": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "fixed_take_profit_enabled": False,
        "legacy_fixed_stop_enabled": False,
        "real_orders_enabled": False,
        "heavy_modules_executed": [],
        "outputs": {
            "decisions": "outputs/daily_tct_ct/DAILY_TCT_CT_DECISIONS.csv",
            "entry_exit": "outputs/daily_tct_ct/DAILY_TCT_CT_V21_8.csv",
            "tct_baseline": "outputs/daily_tct_ct/TCT_BASELINE_V24_1_8.csv",
            "tct_shadow": "outputs/daily_tct_ct/TCT_SHADOW_V24_1_7.csv",
            "android": "outputs/mobile/ANDROID_DAILY_TCT_CT.md",
            "source_context": "outputs/source_context/DAILY_TCT_CT_SOURCE_OBSERVATIONS.csv",
        },
    }
    (auditdir / "DAILY_TCT_CT_AUDIT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
