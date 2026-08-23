from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.decision.committee_master import load_registry
from v182.decision.tct_baseline_v24_1_7 import WEIGHTS_V24_1_2, SETUP_COMPONENT
from v182.reporting import committee_ci_explainability as ci


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CI_RESTITUTION_V21_15_7"


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _truth(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes", "oui", "pass"}


def _tct_details(root: Path, selected: pd.DataFrame) -> pd.DataFrame:
    chosen = selected[
        selected["asset_class"].astype(str).str.upper().eq("ACTION")
        & selected["horizon"].astype(str).str.upper().eq("TCT")
    ].copy()
    baseline = _read(root / "outputs" / "daily_tct_ct" / "TCT_BASELINE_V24_1_8.csv")
    if chosen.empty or baseline.empty or "isin" not in baseline.columns:
        return pd.DataFrame()

    baseline = baseline.drop_duplicates("isin", keep="last").set_index(baseline["isin"].astype(str))
    active_weights = {name: float(weight) for name, weight in WEIGHTS_V24_1_2.items() if name != SETUP_COMPONENT}
    rows: list[dict] = []
    for _, decision in chosen.iterrows():
        isin = str(decision.get("isin") or "")
        if isin not in baseline.index:
            continue
        source_row = baseline.loc[isin]
        observed: dict[str, bool] = {}
        for criterion in active_weights:
            observed_col = f"tct_baseline_component_{criterion}_observed"
            value_col = f"tct_baseline_component_{criterion}"
            value = pd.to_numeric(pd.Series([source_row.get(value_col)]), errors="coerce").iloc[0]
            observed[criterion] = bool(pd.notna(value) and (_truth(source_row.get(observed_col)) if observed_col in source_row.index else True))
        denom = sum(weight for criterion, weight in active_weights.items() if observed.get(criterion))

        for criterion, weight in active_weights.items():
            value_col = f"tct_baseline_component_{criterion}"
            raw = pd.to_numeric(pd.Series([source_row.get(value_col)]), errors="coerce").iloc[0]
            available = bool(observed.get(criterion) and pd.notna(raw) and denom > 0)
            effective = weight / denom if available else 0.0
            rows.append({
                "asset_class": "ACTION",
                "horizon": "TCT",
                "isin": isin,
                "name": decision.get("name"),
                "decision": decision.get("decision"),
                "final_score": decision.get("score"),
                "criterion": criterion,
                "criterion_status": "ACTIVE" if available else "MISSING",
                "raw_value": float(raw) if pd.notna(raw) else None,
                "direction": "HIGH",
                "resolution": f"DERIVED:{value_col}",
                "source_field": value_col,
                "criterion_score_0_100": float(raw) if pd.notna(raw) else None,
                "theoretical_weight_pct": weight * 100.0,
                "effective_weight_pct": effective * 100.0,
                "weighted_contribution_points": float(raw) * effective if available else None,
                "contribution_scope": "TCT_BASELINE_V24_1_8_FINAL_SCORE",
                "source": "INTERNAL_TCT_BASELINE_V24_1_8",
                "as_of": decision.get("generated_at_utc"),
                "evidence_level": "C",
                "validation_status": "AUTO_DERIVED",
            })
        for label, field in (("T1_TIMING_CONTEXT", "t1_quality_score"), ("T2_TIMING_CONTEXT", "t2_quality_score")):
            value = pd.to_numeric(pd.Series([decision.get(field)]), errors="coerce").iloc[0]
            rows.append({
                "asset_class": "ACTION",
                "horizon": "TCT",
                "isin": isin,
                "name": decision.get("name"),
                "decision": decision.get("decision"),
                "final_score": decision.get("score"),
                "criterion": label,
                "criterion_status": "CONTEXT_ONLY",
                "raw_value": float(value) if pd.notna(value) else None,
                "direction": "HIGH",
                "resolution": f"CONTEXT:{field}",
                "source_field": field,
                "criterion_score_0_100": float(value) if pd.notna(value) else None,
                "theoretical_weight_pct": 0.0,
                "effective_weight_pct": 0.0,
                "weighted_contribution_points": 0.0,
                "contribution_scope": "ZERO_SCORE_INFLUENCE_T1_T2_CONTEXT",
                "source": "TCT_EXACT_TIMING_V24_1_7",
                "as_of": decision.get("generated_at_utc"),
                "evidence_level": "C",
                "validation_status": "CONTEXT_ONLY",
            })
    return pd.DataFrame(rows)


def _preserve_internal_tct_provenance(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return detail
    attached = ci._attach_provenance(ROOT, detail)
    tct = attached["horizon"].astype(str).str.upper().eq("TCT")
    original = detail.reset_index(drop=True)
    attached = attached.reset_index(drop=True)
    for column in ("source", "as_of", "evidence_level", "validation_status"):
        missing = attached[column].isna() | attached[column].astype(str).str.strip().isin({"", "nan", "None"})
        attached.loc[tct & missing, column] = original.loc[tct & missing, column]
    return attached


def run(root: Path = ROOT) -> dict:
    daily_path = root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_V21_8.csv"
    decisions = _read(daily_path)
    if decisions.empty:
        raise RuntimeError("DAILY_CI_DECISIONS_MISSING")

    allowed_horizons = {("ACTION", "TCT"), ("ACTION", "CT"), ("ETF", "CT")}
    mask = [
        (str(asset).upper(), str(horizon).upper()) in allowed_horizons
        for asset, horizon in zip(decisions["asset_class"], decisions["horizon"])
    ]
    decisions = decisions.loc[mask].copy()
    committee_dir = root / "outputs" / "committee_master"
    audit_dir = root / "outputs" / "audit"
    mobile_dir = root / "outputs" / "mobile"
    committee_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    mobile_dir.mkdir(parents=True, exist_ok=True)
    decisions.to_csv(committee_dir / "COMMITTEE_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    decisions.to_csv(committee_dir / "CI_DAILY_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")

    selected = decisions[decisions["decision"].astype(str).isin(ci.SELECTED_CODES)].copy()
    selected = selected[selected["asset_class"].astype(str).isin({"ACTION", "ETF"})]
    action_source = _read(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etf_source = _read(root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_registry = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    etf_registry = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    parts = [
        ci._generic_details(action_source, selected, action_registry, "ACTION", ["CT"]),
        ci._generic_details(etf_source, selected, etf_registry, "ETF", ["CT"]),
        _tct_details(root, selected),
    ]
    detail = pd.concat([part for part in parts if not part.empty], ignore_index=True, sort=False) if any(not part.empty for part in parts) else pd.DataFrame()
    detail = _preserve_internal_tct_provenance(detail)
    context = ci._join_context(root, selected)

    selected_keys = ci._selection_keys(selected)
    detail_keys = ci._selection_keys(detail)
    missing_reference = sorted(selected_keys - detail_keys)
    if missing_reference:
        payload = {
            "status": "BLOCKED_CI_REFERENCE_INCOMPLETE",
            "version": VERSION,
            "missing_reference_keys": [list(key) for key in missing_reference],
            "external_collection_calls": 0,
            "model_reruns": 0,
        }
        (audit_dir / "DAILY_CI_RESTITUTION_V21_15_7.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"DAILY_CI_REFERENCE_INCOMPLETE:{missing_reference[:10]}")

    android_path = mobile_dir / "ANDROID_CI_CONTROL_CENTER.md"
    word_path = committee_dir / "CI_COMITE_INVESTISSEMENT.docx"
    excel_path = committee_dir / "CI_REFERENTIEL_PONDERE.xlsx"
    android_path.write_text(ci._android(context, detail), encoding="utf-8")
    ci._write_word_report(word_path, context, detail)
    ci._write_excel_reference(excel_path, detail)

    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_decisions": str(daily_path.relative_to(root)),
        "scope": ["ACTION_TCT", "ACTION_CT", "ETF_CT"],
        "decision_rows": int(len(decisions)),
        "selected_rows": int(len(selected)),
        "criteria_detail_rows": int(len(detail)),
        "reference_complete_for_selected": selected_keys <= detail_keys,
        "word_output": str(word_path.relative_to(root)),
        "excel_output": str(excel_path.relative_to(root)),
        "android_output": str(android_path.relative_to(root)),
        "external_collection_calls": 0,
        "model_reruns": 0,
        "score_or_decision_mutation": False,
        "weights_or_thresholds_changed": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "t1_t2_score_influence": 0.0,
        "real_orders_enabled": False,
        "reconstruction": ci._reconstruction(selected, detail),
    }
    (audit_dir / "DAILY_CI_RESTITUTION_V21_15_7.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (audit_dir / "CI_EXPLAINABILITY_AUDIT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
