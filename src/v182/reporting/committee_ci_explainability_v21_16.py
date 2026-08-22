from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.decision.committee_master import load_registry
from v182.decision.tct_baseline_v24_1_7 import SETUP_COMPONENT, WEIGHTS_V24_1_2
from v182.decision.tct_baseline_v24_1_8 import ACTIVE_WEIGHT
from v182.reporting import committee_ci_explainability as legacy

ROOT = Path(__file__).resolve().parents[3]
CI_VERSION = "CI_RESTITUTION_V21_16"


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "oui"}


def _tct_details(root: Path, selected: pd.DataFrame) -> pd.DataFrame:
    chosen = selected[
        selected["asset_class"].astype(str).eq("ACTION")
        & selected["horizon"].astype(str).eq("TCT")
    ]
    baseline = legacy._read(root / "outputs" / "committee_master" / "TCT_BASELINE_V24_1_8.csv")
    if chosen.empty or baseline.empty or "isin" not in baseline:
        return pd.DataFrame()
    baseline = baseline.drop_duplicates("isin", keep="last").copy()
    baseline["isin"] = baseline["isin"].astype(str)
    lookup = baseline.set_index("isin", drop=False)
    active_weights = {name: float(weight) for name, weight in WEIGHTS_V24_1_2.items() if name != SETUP_COMPONENT}
    rows: list[dict] = []
    for _, decision in chosen.iterrows():
        isin = str(decision.get("isin") or "")
        if isin not in lookup.index:
            continue
        source_row = lookup.loc[isin]
        if isinstance(source_row, pd.DataFrame):
            source_row = source_row.iloc[-1]
        observed = {
            name: pd.notna(pd.to_numeric(pd.Series([source_row.get(f"tct_baseline_component_{name}")]), errors="coerce").iloc[0])
            for name in active_weights
        }
        observed_weight = sum(weight for name, weight in active_weights.items() if observed[name])
        for name, weight in active_weights.items():
            raw = pd.to_numeric(pd.Series([source_row.get(f"tct_baseline_component_{name}")]), errors="coerce").iloc[0]
            available = bool(observed[name] and observed_weight > 0)
            normalized_theoretical = weight / ACTIVE_WEIGHT if ACTIVE_WEIGHT else 0.0
            effective = weight / observed_weight if available else 0.0
            rows.append(
                {
                    "asset_class": "ACTION",
                    "horizon": "TCT",
                    "isin": isin,
                    "name": decision.get("name"),
                    "decision": decision.get("decision"),
                    "final_score": decision.get("score"),
                    "criterion": f"TCT_BASELINE_{name.upper()}",
                    "criterion_status": "ACTIVE" if available else "MISSING",
                    "raw_value": float(raw) if pd.notna(raw) else None,
                    "direction": "HIGHER_BETTER",
                    "resolution": f"DIRECT:tct_baseline_component_{name}",
                    "source_field": f"tct_baseline_component_{name}",
                    "criterion_score_0_100": float(raw) if pd.notna(raw) else None,
                    "theoretical_weight_pct": normalized_theoretical * 100.0,
                    "effective_weight_pct": effective * 100.0,
                    "weighted_contribution_points": float(raw) * effective if available else None,
                    "contribution_scope": "FINAL_SCORE",
                    "source": "TCT_BASELINE_V24_1_8",
                    "as_of": decision.get("generated_at_utc"),
                    "evidence_level": "A_INTERNAL_GOVERNED",
                    "validation_status": "ACTIVE_AVAILABLE_PILLARS_RENORMALIZED_TO_100_SETUP_EXCLUDED",
                }
            )
    return pd.DataFrame(rows)


def _source_validation(root: Path, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    frame = selected.copy()
    entry = legacy._read(root / "outputs" / "committee_master" / "V21_8_ENTRY_EXIT_CHALLENGER.csv")
    keys = ["asset_class", "horizon", "isin"]
    if not entry.empty and all(key in entry for key in keys):
        keep = keys + [c for c in ("v21_8_entry_state", "v21_8_entry_reasons", "t2_confirmed", "tct_setup") if c in entry]
        frame = frame.merge(entry[keep].drop_duplicates(keys, keep="last"), on=keys, how="left", suffixes=("", "_entry"))
    decision = frame["decision"].astype(str)
    source_ok = frame.get("ci_source_eligible", pd.Series(False, index=frame.index)).map(_bool)
    frame["ci_final_status"] = "SURVEILLANCE_INTERNE"
    frame.loc[decision.eq("BUY_CANDIDATE") & ~source_ok, "ci_final_status"] = "BUY_INTERNE_ATTENTE_SOURCES"
    frame.loc[decision.eq("BUY_CANDIDATE") & source_ok, "ci_final_status"] = "RECOMMANDATION_TOTALEMENT_VALIDEE"
    columns = [
        "asset_class", "horizon", "name", "isin", "decision", "score", "coverage_pct", "ci_final_status",
        "source_validation_state", "source_validation_reasons", "source_fully_validated", "ci_source_eligible",
        "boursorama_priority_ready", "boursorama_context_coverage_pct", "boursorama_dynamic_age_hours",
        "boursorama_performance_age_hours", "boursorama_deep_age_hours", "boursorama_etf_dynamic_age_hours",
        "boursorama_etf_deep_age_hours", "boursorama_consensus", "boursorama_n_analysts", "boursorama_target_median",
        "boursorama_target_upside_pct", "boursorama_consensus_delta_7d", "boursorama_consensus_delta_1m",
        "boursorama_target_price_delta_7d_pct", "boursorama_target_price_delta_1m_pct", "boursorama_buy_ratio_delta_7d",
        "boursorama_buy_ratio_delta_1m", "boursorama_perf_1d_pct", "boursorama_perf_1w_pct", "boursorama_perf_1m_pct",
        "boursorama_perf_6m_pct", "boursorama_perf_1y_pct", "boursorama_estimated_per", "boursorama_estimated_yield_pct",
        "boursorama_eps_est_2026", "boursorama_eps_est_2027", "boursorama_revenue_m_est_2026", "boursorama_revenue_m_est_2027",
        "boursorama_etf_aum_eur_m", "boursorama_etf_morningstar_category", "boursorama_etf_replication",
        "boursorama_etf_management_fee_pct", "boursorama_etf_sri", "boursorama_etf_volatility_1y_pct", "boursorama_etf_beta_1y",
        "boursorama_source_urls", "boursorama_latest_collected_at",
        "investing_required_timeframe", "investing_required_state", "investing_horizon_signal", "investing_timing_confirmed",
        "investing_daily_signal", "investing_weekly_signal", "investing_monthly_signal", "investing_age_hours",
        "investing_source_urls", "investing_latest_collected_at", "v21_8_entry_state", "v21_8_entry_reasons", "t2_confirmed", "tct_setup",
    ]
    return frame[[c for c in columns if c in frame]].copy()


def _append_excel_source_sheets(path: Path, source: pd.DataFrame) -> list[str]:
    from openpyxl import load_workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter

    workbook = load_workbook(path)
    for name in ("Validation_sources", "Recommandations_validees", "Attente_sources", "Surveillance"):
        if name in workbook.sheetnames:
            del workbook[name]

    groups = [
        ("Validation_sources", source),
        ("Recommandations_validees", source[source.get("ci_final_status", pd.Series(dtype=str)).astype(str).eq("RECOMMANDATION_TOTALEMENT_VALIDEE")] if not source.empty else source),
        ("Attente_sources", source[source.get("ci_final_status", pd.Series(dtype=str)).astype(str).eq("BUY_INTERNE_ATTENTE_SOURCES")] if not source.empty else source),
        ("Surveillance", source[source.get("ci_final_status", pd.Series(dtype=str)).astype(str).eq("SURVEILLANCE_INTERNE")] if not source.empty else source),
    ]
    for sheet_name, frame in groups:
        sheet = workbook.create_sheet(sheet_name)
        if frame.empty:
            sheet.cell(1, 1, "Aucune ligne")
            continue
        for col_idx, column in enumerate(frame.columns, start=1):
            cell = sheet.cell(1, col_idx, str(column)); cell.font = Font(bold=True); cell.alignment = Alignment(wrap_text=True, vertical="top")
        for row_idx, values in enumerate(frame.itertuples(index=False, name=None), start=2):
            for col_idx, value in enumerate(values, start=1):
                sheet.cell(row_idx, col_idx, None if pd.isna(value) else value)
        sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions
        for idx, column in enumerate(frame.columns, start=1):
            sample = [len(str(column))] + [len(str(v)) for v in frame[column].dropna().astype(str).head(80)]
            sheet.column_dimensions[get_column_letter(idx)].width = min(max(sample) + 2, 46)
    workbook.save(path)
    return ["Referentiel_pondere", "Validation_sources", "Recommandations_validees", "Attente_sources", "Surveillance"]


def _append_word_source_section(path: Path, source: pd.DataFrame) -> None:
    from docx import Document
    from docx.enum.text import WD_BREAK

    document = Document(path)
    paragraph = document.add_paragraph(); paragraph.add_run().add_break(WD_BREAK.PAGE)
    document.add_heading("Validation finale Boursorama + Investing — V21.16", level=1)
    document.add_paragraph(
        "Cette section distingue strictement la sélection interne du modèle et la validation finale de timing. "
        "Une recommandation totalement validée exige un BUY_CANDIDATE interne, une fiche Boursorama prioritaire suffisamment complète et fraîche, "
        "et un signal Investing STRONG_BUY sur l'horizon requis (Daily pour TCT, Weekly pour CT, Monthly pour MT)."
    )
    if source.empty:
        document.add_paragraph("Aucune présélection Action/ETF à documenter.")
        document.save(path); return
    validated = source[source["ci_final_status"].astype(str).eq("RECOMMANDATION_TOTALEMENT_VALIDEE")]
    waiting = source[source["ci_final_status"].astype(str).eq("BUY_INTERNE_ATTENTE_SOURCES")]
    watch = source[source["ci_final_status"].astype(str).eq("SURVEILLANCE_INTERNE")]
    document.add_paragraph(f"Recommandations totalement validées : {len(validated)} | BUY internes en attente : {len(waiting)} | Surveillance : {len(watch)}")

    def add_group(title: str, frame: pd.DataFrame) -> None:
        document.add_heading(title, level=2)
        if frame.empty:
            document.add_paragraph("Aucune ligne."); return
        for _, row in frame.iterrows():
            name = str(row.get("name") or row.get("isin") or "N/A"); horizon = str(row.get("horizon") or "N/A"); asset = str(row.get("asset_class") or "N/A"); score = pd.to_numeric(pd.Series([row.get("score")]), errors="coerce").iloc[0]
            score_txt = "N/A" if pd.isna(score) else f"{float(score):.1f}"
            document.add_heading(f"{asset} — {name} — {horizon}", level=3)
            document.add_paragraph(f"Décision interne : {row.get('decision', 'N/A')} | score {score_txt} | statut final CI : {row.get('ci_final_status', 'N/A')}")
            b_ready = _bool(row.get("boursorama_priority_ready")); b_cov = pd.to_numeric(pd.Series([row.get("boursorama_context_coverage_pct")]), errors="coerce").iloc[0]
            b_cov_txt = "N/A" if pd.isna(b_cov) else f"{float(b_cov):.1f}%"
            document.add_paragraph(f"Boursorama : {'VALIDÉ' if b_ready else 'INCOMPLET'} | couverture contexte {b_cov_txt} | as-of {row.get('boursorama_latest_collected_at', 'N/A')}")
            consensus = row.get("boursorama_consensus"); analysts = row.get("boursorama_n_analysts"); target = row.get("boursorama_target_median"); upside = row.get("boursorama_target_upside_pct")
            if any(pd.notna(value) for value in (consensus, analysts, target, upside)):
                document.add_paragraph(f"Consensus Boursorama : {consensus if pd.notna(consensus) else 'N/A'} | analystes {analysts if pd.notna(analysts) else 'N/A'} | objectif médian {target if pd.notna(target) else 'N/A'} | potentiel {upside if pd.notna(upside) else 'N/A'}%")
            required = row.get("investing_required_timeframe", "N/A"); signal = row.get("investing_horizon_signal", "N/A")
            document.add_paragraph(f"Investing requis : {required}=STRONG_BUY | observé : {signal} | Daily={row.get('investing_daily_signal', 'N/A')} | Weekly={row.get('investing_weekly_signal', 'N/A')} | Monthly={row.get('investing_monthly_signal', 'N/A')} | as-of {row.get('investing_latest_collected_at', 'N/A')}")
            entry = row.get("v21_8_entry_state"); reasons = row.get("v21_8_entry_reasons")
            if pd.notna(entry): document.add_paragraph(f"Entrée V21.8 : {entry} | {reasons if pd.notna(reasons) else ''}")
            source_reasons = row.get("source_validation_reasons")
            if pd.notna(source_reasons) and str(source_reasons) != "OK": document.add_paragraph(f"Points restant à confirmer : {source_reasons}")
            b_urls = row.get("boursorama_source_urls"); i_urls = row.get("investing_source_urls")
            if pd.notna(b_urls): document.add_paragraph(f"Provenance Boursorama : {b_urls}")
            if pd.notna(i_urls): document.add_paragraph(f"Provenance Investing : {i_urls}")

    add_group("Recommandations totalement validées", validated)
    add_group("BUY internes en attente de confirmation", waiting)
    add_group("Surveillance / review", watch)
    document.save(path)


def _append_android_source_section(path: Path, source: pd.DataFrame) -> None:
    lines = ["", "## Validation finale Boursorama + Investing", ""]
    if source.empty:
        lines.append("Aucune présélection à qualifier.")
    else:
        ordered = source.copy(); ordered["_rank"] = ordered["ci_final_status"].map({"RECOMMANDATION_TOTALEMENT_VALIDEE": 0, "BUY_INTERNE_ATTENTE_SOURCES": 1, "SURVEILLANCE_INTERNE": 2}).fillna(3); ordered["_score"] = pd.to_numeric(ordered.get("score"), errors="coerce"); ordered = ordered.sort_values(["_rank", "_score"], ascending=[True, False])
        for _, row in ordered.iterrows():
            lines.append(f"- **{row.get('asset_class')} {row.get('name') or row.get('isin')} [{row.get('horizon')}]** — {row.get('ci_final_status')} — gate {row.get('source_validation_state')} — Investing {row.get('investing_required_timeframe')}={row.get('investing_horizon_signal')} — entrée {row.get('v21_8_entry_state', 'N/A')}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(root: Path = ROOT) -> dict:
    decisions = legacy._read(root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv")
    if decisions.empty:
        return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING", "real_orders_enabled": False}
    selected = decisions[decisions["decision"].astype(str).isin(legacy.SELECTED_CODES)].copy(); selected = selected[selected["asset_class"].astype(str).isin({"ACTION", "ETF"})]
    action_source = legacy._read(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"); etf_source = legacy._read(root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"); action_registry = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json"); etf_registry = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    parts = [
        legacy._generic_details(action_source, selected, action_registry, "ACTION", ["CT", "MT", "SHORT", "TOP_DOWN"]),
        _tct_details(root, selected),
        legacy._generic_details(etf_source, selected, etf_registry, "ETF", ["CT", "SHORT", "TOP_DOWN"]),
        legacy._etf_mt_details(root, selected),
    ]
    detail = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) else pd.DataFrame(); detail = legacy._attach_provenance(root, detail); context = legacy._join_context(root, selected)
    mobile_dir = root / "outputs" / "mobile"; committee_dir = root / "outputs" / "committee_master"; audit_dir = root / "outputs" / "audit"; mobile_dir.mkdir(parents=True, exist_ok=True); committee_dir.mkdir(parents=True, exist_ok=True); audit_dir.mkdir(parents=True, exist_ok=True)
    selected_keys = legacy._selection_keys(selected); detail_keys = legacy._selection_keys(detail); missing_reference = sorted(selected_keys - detail_keys)
    if missing_reference:
        blocked = {"status": "BLOCKED_CI_REFERENCE_INCOMPLETE", "version": CI_VERSION, "selected_rows": int(len(selected)), "missing_reference_keys": [list(key) for key in missing_reference], "score_or_decision_mutation": False, "weight_or_threshold_changes": False, "external_collection_calls": 0, "real_orders_enabled": False}; (audit_dir / "CI_EXPLAINABILITY_AUDIT.json").write_text(json.dumps(blocked, ensure_ascii=False, indent=2), encoding="utf-8"); raise RuntimeError(f"CI_REFERENCE_INCOMPLETE:{missing_reference[:10]}")

    android_path = mobile_dir / "ANDROID_CI_CONTROL_CENTER.md"; word_path = committee_dir / "CI_COMITE_INVESTISSEMENT.docx"; excel_path = committee_dir / "CI_REFERENTIEL_PONDERE.xlsx"
    android_path.write_text(legacy._android(context, detail), encoding="utf-8"); legacy._write_word_report(word_path, context, detail); legacy._write_excel_reference(excel_path, detail)
    source = _source_validation(root, selected); sheets = _append_excel_source_sheets(excel_path, source); _append_word_source_section(word_path, source); _append_android_source_section(android_path, source)
    reconstruction = legacy._reconstruction(selected, detail); validated = int(source.get("ci_final_status", pd.Series(dtype=str)).astype(str).eq("RECOMMANDATION_TOTALEMENT_VALIDEE").sum()) if not source.empty else 0; pending = int(source.get("ci_final_status", pd.Series(dtype=str)).astype(str).eq("BUY_INTERNE_ATTENTE_SOURCES").sum()) if not source.empty else 0; watch = int(source.get("ci_final_status", pd.Series(dtype=str)).astype(str).eq("SURVEILLANCE_INTERNE").sum()) if not source.empty else 0
    source.to_csv(committee_dir / "CI_VALIDATION_SOURCES.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = {"status": "SUCCESS", "version": CI_VERSION, "selected_rows": int(len(selected)), "fully_validated_recommendations": validated, "internal_buy_waiting_sources": pending, "surveillance_rows": watch, "criteria_detail_rows": int(len(detail)), "tct_weighted_reference_included": bool((detail.get("horizon", pd.Series(dtype=str)).astype(str) == "TCT").any()), "android_output": str(android_path.relative_to(root)), "word_output": str(word_path.relative_to(root)), "excel_output": str(excel_path.relative_to(root)), "source_validation_output": "outputs/committee_master/CI_VALIDATION_SOURCES.csv", "same_canonical_run_android_word_excel": True, "same_selected_set_word_excel": selected_keys == detail_keys, "reference_complete_for_selected": selected_keys <= detail_keys, "excel_visible_sheets": sheets, "score_or_decision_mutation": False, "weight_or_threshold_changes": False, "external_collection_calls": 0, "source_validation_read_only": True, "t1_t2_scope": "ACTION_TCT_ONLY", "real_orders_enabled": False, "reconstruction": reconstruction}
    (audit_dir / "CI_EXPLAINABILITY_AUDIT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
