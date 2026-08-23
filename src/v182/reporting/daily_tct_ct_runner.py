from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.decision.committee_master import decisions_from_scores, load_registry, tct_adapter
from v182.decision.source_confirmation_gate_v21_16 import apply_source_confirmation_gate, source_gate_summary
from v182.decision.tct_baseline_v24_1_8 import build_tct_baseline, NORMALIZATION_POLICY
from v182.decision.tct_timing_exact_v24_1_7 import build_exact_timing_snapshot
from v182.decision.tct_v24_1_7 import load_tct_config
from v182.reporting.selected_source_enrichment import _read_contract, attach_master_identity, enrich_selected_rows
from v182.risk.entry_exit_governance_v21_8 import STATE_RELATIVE_PATH, _attach_temporal_state, _load_temporal_state, _persist_temporal_state, apply_governance

ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_TCT_CT_V1_SOURCE_CONTRACT_V21_16_4_IN_MEMORY"
TACTICAL_DISPLAY_CODES = {
    "BUY_CANDIDATE",
    "WATCH",
    "REVIEW",
    "T1_STARTER_25_SHADOW",
    "T1_WATCH_SHADOW",
    "T2_CONFIRM_75_SHADOW",
}
TACTICAL_PRIORITY = {
    "T2_CONFIRM_75_SHADOW": 0,
    "BUY_CANDIDATE": 1,
    "T1_STARTER_25_SHADOW": 2,
    "WATCH": 3,
    "T1_WATCH_SHADOW": 4,
    "REVIEW": 5,
}


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"DAILY_TACTICAL_INPUT_MISSING:{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _daily_exact_scope(actions_with_tct: pd.DataFrame, tct_cfg: dict) -> pd.DataFrame:
    """Apply the existing baseline gate before the expensive exact T1/T2 calculation."""
    if actions_with_tct.empty:
        return actions_with_tct.copy()
    rank = pd.to_numeric(actions_with_tct.get("tct_baseline_rank"), errors="coerce")
    coverage = pd.to_numeric(actions_with_tct.get("tct_baseline_coverage"), errors="coerce")
    top_n = int(tct_cfg.get("scope", {}).get("baseline_top_n", 20))
    min_coverage = float(tct_cfg.get("scope", {}).get("baseline_min_coverage", 0.60))
    eligible = rank.notna() & rank.le(top_n) & coverage.notna() & coverage.ge(min_coverage)
    return actions_with_tct.loc[eligible].copy().reset_index(drop=True)


def _compact_tct_baseline(frame: pd.DataFrame) -> pd.DataFrame:
    """Retain every TCT baseline reconstruction field without serializing the full master."""
    identity = [
        c for c in ("isin", "name", "yahoo_ticker", "sector_yf", "sector_v21", "sector")
        if c in frame.columns
    ]
    baseline = [c for c in frame.columns if c.startswith("tct_baseline_")]
    columns = list(dict.fromkeys(identity + baseline))
    return frame[columns].copy() if columns else frame.iloc[:, 0:0].copy()


def _android_summary(governed: pd.DataFrame, generated_at: str) -> str:
    rows = governed.copy()
    rows["_score"] = pd.to_numeric(rows.get("score"), errors="coerce")
    rows["_priority"] = rows["decision"].astype(str).map(TACTICAL_PRIORITY).fillna(9)
    priority = rows[rows["decision"].astype(str).isin(TACTICAL_DISPLAY_CODES)].copy().sort_values(
        ["horizon", "_priority", "_score"], ascending=[True, True, False]
    )
    lines = [
        "# Comité tactique quotidien — TCT / CT",
        "",
        f"Généré UTC : {generated_at}",
        "Scoring interne d'abord ; Boursorama + Investing interviennent ensuite comme gate de confirmation.",
        "TCT exige Investing Daily STRONG_BUY ; CT exige Weekly STRONG_BUY. Le score et la décision interne ne sont jamais modifiés par ce gate.",
        "Pour TCT : T1 reste surveillance ; T2 exact + sources validées peut produire ACTION dans la couche de décision-support SHADOW. Aucun TCT n'est requalifié en BUY de production.",
        "Aucun ordre réel.",
        "",
    ]
    if priority.empty:
        lines.append("Aucune priorité BUY/WATCH/T1/T2/REVIEW sur ce run.")
        return "\n".join(lines) + "\n"
    for horizon in ("TCT", "CT"):
        subset = priority[priority["horizon"].astype(str).str.upper() == horizon].head(12)
        if subset.empty:
            continue
        lines.extend([f"## {horizon}", ""])
        for _, row in subset.iterrows():
            name = str(row.get("name") or row.get("isin") or "N/A")
            asset = str(row.get("asset_class") or "")
            score = row.get("_score")
            score_txt = "N/A" if pd.isna(score) else f"{float(score):.1f}"
            decision = str(row.get("decision") or "")
            entry = str(row.get("v21_8_entry_state") or "")
            position = str(row.get("v21_8_position_state") or "")
            source_state = str(row.get("source_validation_state") or "SOURCES_INCOMPLETE")
            timing = str(row.get("investing_horizon_signal") or "N/A")
            expected = str(row.get("investing_required_timeframe") or "N/A")
            b_ready = bool(row.get("boursorama_priority_ready")) if pd.notna(row.get("boursorama_priority_ready")) else False
            lines.append(f"- **{asset} {name}** — score {score_txt} — {decision} — entrée {entry} — position {position}")
            lines.append(f"  - Gate sources : {source_state} | Boursorama={'OK' if b_ready else 'INCOMPLET'} | Investing {expected}={timing}")
            if horizon == "TCT":
                setup = str(row.get("setup") or "N/A")
                t1_event = str(row.get("t1_source_event_id") or "N/A")
                lines.append(f"  - TCT exact : setup={setup} | événement T1={t1_event} | décision-support seulement")
            daily = str(row.get("investing_daily_signal") or "N/A")
            weekly = str(row.get("investing_weekly_signal") or "N/A")
            monthly = str(row.get("investing_monthly_signal") or "N/A")
            lines.append(f"  - Investing multi-horizon : jour={daily} | semaine={weekly} | mois={monthly}")
            b_consensus = str(row.get("boursorama_consensus") or "").strip()
            b_target = pd.to_numeric(pd.Series([row.get("boursorama_target_median")]), errors="coerce").iloc[0]
            b_upside = pd.to_numeric(pd.Series([row.get("boursorama_target_upside_pct")]), errors="coerce").iloc[0]
            if b_consensus or pd.notna(b_target) or pd.notna(b_upside):
                lines.append(
                    f"  - Boursorama : consensus={b_consensus or 'N/A'} | objectif médian="
                    f"{'N/A' if pd.isna(b_target) else f'{float(b_target):.2f}'} | potentiel="
                    f"{'N/A' if pd.isna(b_upside) else f'{float(b_upside):.1f}%'}"
                )
            reasons = str(row.get("source_validation_reasons") or "").strip()
            if reasons and reasons != "OK":
                lines.append(f"  - Attente sources : {reasons}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run(
    root: Path = ROOT,
    *,
    actions: pd.DataFrame | None = None,
    etfs: pd.DataFrame | None = None,
    persist_full_baseline: bool = True,
) -> dict:
    outputs = root / "outputs"
    outdir = outputs / "daily_tct_ct"
    mobile = outputs / "mobile"
    auditdir = outputs / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    in_memory_inputs = actions is not None and etfs is not None
    if actions is None:
        actions = _read(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    else:
        actions = actions.copy()
    if etfs is None:
        etfs = _read(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    else:
        etfs = etfs.copy()

    action_ref = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    etf_ref = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    tct_cfg = load_tct_config(root / "config" / "TCT_V24_1_7_SHADOW.json")
    v21_cfg = json.loads((root / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))
    parts = [
        decisions_from_scores(actions, action_ref, "ACTION", ["CT"]),
        decisions_from_scores(etfs, etf_ref, "ETF", ["CT"]),
    ]
    actions_with_tct, baseline = build_tct_baseline(actions, tct_cfg)
    baseline_export = actions_with_tct if persist_full_baseline else _compact_tct_baseline(actions_with_tct)
    baseline_export.to_csv(outdir / "TCT_BASELINE_V24_1_8.csv", sep=";", index=False, encoding="utf-8-sig")
    exact_scope = _daily_exact_scope(actions_with_tct, tct_cfg)
    tct_state_path = root / str(tct_cfg.get("state", {}).get("path", "state/TCT_V24_1_7_T1_STATE.json"))
    tct_shadow, exact = build_exact_timing_snapshot(exact_scope, root / "data" / "cache" / "actions", tct_state_path, tct_cfg)
    tct_shadow.to_csv(outdir / "TCT_SHADOW_V24_1_7.csv", sep=";", index=False, encoding="utf-8-sig")
    parts.append(tct_adapter(tct_shadow))
    decisions = pd.concat([part for part in parts if part is not None and not part.empty], ignore_index=True, sort=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    decisions["generated_at_utc"] = generated_at
    decisions["live_orders_enabled"] = False
    decisions["daily_tactical_scope"] = True
    source_input = attach_master_identity(decisions, actions, etfs)
    decisions, source_context = enrich_selected_rows(source_input, root, profile="DAILY_TCT_CT", network_policy="LIVE_IF_DUE", persist_outputs=True)
    decisions = apply_source_confirmation_gate(decisions, _read_contract(root))
    gate = source_gate_summary(decisions)
    decisions.to_csv(outdir / "DAILY_TCT_CT_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    state_path = root / STATE_RELATIVE_PATH
    previous = _load_temporal_state(state_path)
    governed = apply_governance(_attach_temporal_state(decisions, previous), v21_cfg)
    state_rows = _persist_temporal_state(governed, state_path)
    governed.to_csv(outdir / "DAILY_TCT_CT_V21_8.csv", sep=";", index=False, encoding="utf-8-sig")
    android_path = mobile / "ANDROID_DAILY_TCT_CT.md"
    android_path.write_text(_android_summary(governed, generated_at), encoding="utf-8")
    tct_actions = governed[
        governed["horizon"].astype(str).str.upper().eq("TCT")
        & governed["v21_8_entry_state"].astype(str).eq("ACTION")
    ]
    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "scope": ["ACTION_TCT", "ACTION_CT", "ETF_CT"],
        "rows": int(len(governed)),
        "rows_by_asset_horizon": governed.groupby(["asset_class", "horizon"], dropna=False).size().reset_index(name="count").to_dict("records"),
        "in_memory_master_handoff": bool(in_memory_inputs),
        "full_master_csv_read_avoided": bool(in_memory_inputs),
        "tct_baseline_export_mode": "FULL_MASTER_PLUS_BASELINE" if persist_full_baseline else "COMPACT_RECONSTRUCTABLE_BASELINE_ONLY",
        "selected_source_context": source_context,
        "source_confirmation_gate": gate,
        "source_network_policy": "LIVE_IF_DUE",
        "tct_baseline": {
            "universe_rows": baseline.universe_rows,
            "ranked_rows": baseline.ranked_rows,
            "top20_rows": baseline.top20_rows,
            "normalization_policy": NORMALIZATION_POLICY,
            "export_rows": int(len(baseline_export)),
            "export_columns": int(len(baseline_export.columns)),
            "all_baseline_component_fields_retained": True,
        },
        "tct_exact": {
            "daily_exact_scope_rows": int(len(exact_scope)),
            "daily_exact_scope_policy": "BASELINE_TOP_N_AND_MIN_COVERAGE_PRE_GATE",
            "weekly_exhaustive_research_unchanged": True,
            "histories_found": exact.histories_found,
            "histories_usable": exact.histories_usable,
            "t1_detected_raw": exact.t1_detected_raw,
            "t2_confirmed": exact.t2_confirmed,
            "source_confirmed_entry_support_actions": int(len(tct_actions)),
        },
        "entry_exit_v21_8": {
            "position_states": governed["v21_8_position_state"].value_counts(dropna=False).to_dict(),
            "entry_states": governed["v21_8_entry_state"].value_counts(dropna=False).to_dict(),
            "temporal_state_rows": state_rows,
        },
        "weights_unchanged": True,
        "selection_thresholds_unchanged": True,
        "source_gate_changes_internal_decision": False,
        "holdout_opened": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "tct_t2_remains_shadow_decision_support": True,
        "fixed_take_profit_enabled": False,
        "legacy_fixed_stop_enabled": False,
        "real_orders_enabled": False,
        "heavy_modules_executed": [],
        "outputs": {
            "decisions": "outputs/daily_tct_ct/DAILY_TCT_CT_DECISIONS.csv",
            "entry_exit": "outputs/daily_tct_ct/DAILY_TCT_CT_V21_8.csv",
            "android": "outputs/mobile/ANDROID_DAILY_TCT_CT.md",
            "source_context": "outputs/source_context/DAILY_TCT_CT_SOURCE_OBSERVATIONS.csv",
            "tct_baseline": "outputs/daily_tct_ct/TCT_BASELINE_V24_1_8.csv",
        },
    }
    (auditdir / "DAILY_TCT_CT_AUDIT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
