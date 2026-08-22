from __future__ import annotations

from pathlib import Path
import argparse
import json

import pandas as pd

from v182.reporting import decision_brief as legacy

ROOT = Path(__file__).resolve().parents[3]
VERSION = "CI_DECISION_BRIEF_V21_16"
SOURCE_COLUMNS = [
    "asset_class", "horizon", "isin", "source_validation_state", "source_validation_reasons",
    "source_fully_validated", "ci_source_eligible", "boursorama_priority_ready", "boursorama_context_coverage_pct",
    "boursorama_latest_collected_at", "investing_required_timeframe", "investing_horizon_signal",
    "investing_daily_signal", "investing_weekly_signal", "investing_monthly_signal", "investing_age_hours",
    "investing_latest_collected_at",
]


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "oui"}


def _source_rows(root: Path) -> pd.DataFrame:
    decisions = _read(root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv")
    if decisions.empty:
        return pd.DataFrame()
    selected = decisions[decisions["decision"].astype(str).isin(legacy.SELECTED_DECISIONS)].copy()
    selected = selected[selected["asset_class"].astype(str).isin({"ACTION", "ETF"})]
    if selected.empty:
        return selected
    source_ok = selected.get("ci_source_eligible", pd.Series(False, index=selected.index)).map(_bool)
    selected["ci_final_status"] = "SURVEILLANCE_INTERNE"
    selected.loc[selected["decision"].astype(str).eq("BUY_CANDIDATE") & ~source_ok, "ci_final_status"] = "BUY_INTERNE_ATTENTE_SOURCES"
    selected.loc[selected["decision"].astype(str).eq("BUY_CANDIDATE") & source_ok, "ci_final_status"] = "RECOMMANDATION_TOTALEMENT_VALIDEE"
    return selected


def _append_markdown(path: Path, source: pd.DataFrame) -> None:
    lines = ["", "## 6. Validation finale Boursorama + Investing", ""]
    if source.empty:
        lines.append("- Aucune présélection Action/ETF à qualifier.")
    else:
        for status, title in (
            ("RECOMMANDATION_TOTALEMENT_VALIDEE", "Recommandations totalement validées"),
            ("BUY_INTERNE_ATTENTE_SOURCES", "BUY internes en attente de confirmation"),
            ("SURVEILLANCE_INTERNE", "Surveillance / review"),
        ):
            subset = source[source["ci_final_status"].astype(str).eq(status)]
            lines.extend([f"### {title}", ""])
            if subset.empty:
                lines.append("- Aucune ligne.")
            else:
                for _, row in subset.iterrows():
                    lines.append(
                        f"- **{row.get('asset_class')} {row.get('name') or row.get('isin')} [{row.get('horizon')}]** — "
                        f"gate `{row.get('source_validation_state', 'N/A')}` — Boursorama={'OK' if _bool(row.get('boursorama_priority_ready')) else 'INCOMPLET'} — "
                        f"Investing {row.get('investing_required_timeframe', 'N/A')}={row.get('investing_horizon_signal', 'N/A')} "
                        f"(jour={row.get('investing_daily_signal', 'N/A')}, semaine={row.get('investing_weekly_signal', 'N/A')}, mois={row.get('investing_monthly_signal', 'N/A')})."
                    )
                    reasons = row.get("source_validation_reasons")
                    if pd.notna(reasons) and str(reasons) != "OK":
                        lines.append(f"  - Attente : {reasons}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _append_word(path: Path, source: pd.DataFrame) -> None:
    from docx import Document

    document = Document(path)
    document.add_heading("6. Validation finale Boursorama + Investing", level=1)
    if source.empty:
        document.add_paragraph("Aucune présélection Action/ETF à qualifier.")
    else:
        for status, title in (
            ("RECOMMANDATION_TOTALEMENT_VALIDEE", "Recommandations totalement validées"),
            ("BUY_INTERNE_ATTENTE_SOURCES", "BUY internes en attente de confirmation"),
            ("SURVEILLANCE_INTERNE", "Surveillance / review"),
        ):
            document.add_heading(title, level=2)
            subset = source[source["ci_final_status"].astype(str).eq(status)]
            if subset.empty:
                document.add_paragraph("Aucune ligne.")
                continue
            for _, row in subset.iterrows():
                document.add_paragraph(
                    f"{row.get('asset_class')} — {row.get('name') or row.get('isin')} — {row.get('horizon')} : "
                    f"gate {row.get('source_validation_state', 'N/A')} ; Boursorama {'validé' if _bool(row.get('boursorama_priority_ready')) else 'incomplet'} ; "
                    f"Investing requis {row.get('investing_required_timeframe', 'N/A')}=STRONG_BUY, observé {row.get('investing_horizon_signal', 'N/A')} ; "
                    f"Daily {row.get('investing_daily_signal', 'N/A')} / Weekly {row.get('investing_weekly_signal', 'N/A')} / Monthly {row.get('investing_monthly_signal', 'N/A')}.",
                    style="List Bullet",
                )
                reasons = row.get("source_validation_reasons")
                if pd.notna(reasons) and str(reasons) != "OK":
                    document.add_paragraph(f"Attente : {reasons}")
    document.save(path)


def _enrich_matrix(path: Path, source: pd.DataFrame) -> None:
    matrix = _read(path)
    if matrix.empty or source.empty:
        return
    keys = ["asset_class", "horizon", "isin"]
    keep = keys + [c for c in SOURCE_COLUMNS if c not in keys and c in source] + ["ci_final_status"]
    merged = matrix.merge(source[keep].drop_duplicates(keys, keep="last"), on=keys, how="left")
    merged.to_csv(path, sep=";", encoding="utf-8-sig", index=False)


def _enrich_snapshot(root: Path, source: pd.DataFrame) -> None:
    path = root / "state" / "provenance" / "CI_DECISION_SNAPSHOT.csv"
    snapshot = _read(path)
    if snapshot.empty or source.empty:
        return
    keys = ["asset_class", "horizon", "isin"]
    keep = keys + [c for c in ("source_validation_state", "ci_source_eligible", "investing_horizon_signal", "investing_required_timeframe", "boursorama_priority_ready") if c in source]
    merged = snapshot.merge(source[keep].drop_duplicates(keys, keep="last"), on=keys, how="left")
    merged.to_csv(path, sep=";", encoding="utf-8-sig", index=False)


def run(root: Path = ROOT) -> dict:
    payload = legacy.run(root)
    source = _source_rows(root)
    validated = int(source["ci_final_status"].astype(str).eq("RECOMMANDATION_TOTALEMENT_VALIDEE").sum()) if not source.empty else 0
    pending = int(source["ci_final_status"].astype(str).eq("BUY_INTERNE_ATTENTE_SOURCES").sum()) if not source.empty else 0
    surveillance = int(source["ci_final_status"].astype(str).eq("SURVEILLANCE_INTERNE").sum()) if not source.empty else 0
    payload["version"] = VERSION
    payload["fully_validated_recommendations"] = validated
    payload["internal_buy_waiting_sources"] = pending
    payload["surveillance_rows"] = surveillance
    payload["source_gate_required_for_entry"] = True
    payload["source_gate_score_influence"] = 0.0
    payload["source_gate_decision_influence"] = 0.0
    if pending:
        warnings = list(payload.get("warnings") or [])
        warnings.append(f"{pending} BUY interne(s) restent en attente de validation Boursorama/Investing et ne sont pas des recommandations totalement validées.")
        payload["warnings"] = warnings
        if payload.get("decision_status") == "READY_FOR_REVIEW":
            payload["decision_status"] = "REVIEW_WITH_WARNINGS"

    outdir = root / "outputs" / "decision_brief"
    md_path = outdir / "DECISION_BRIEF.md"; json_path = outdir / "DECISION_BRIEF.json"; docx_path = outdir / "CI_DECISION_BRIEF_V3.docx"; matrix_path = outdir / "CI_DECISION_MATRIX_V3.csv"
    _append_markdown(md_path, source); _append_word(docx_path, source); _enrich_matrix(matrix_path, source); _enrich_snapshot(root, source)
    source.to_csv(outdir / "CI_DECISION_SOURCE_GATE_V21_16.csv", sep=";", encoding="utf-8-sig", index=False)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", default=str(ROOT)); args = parser.parse_args(); print(json.dumps(run(Path(args.root)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
