from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Mapping

import pandas as pd

from v182.decision.etf_mt_operational_gates import annotate_ranking


INSTRUCTION_FIELDS = (
    "job_horizon",
    "thesis_8_12_lines",
    "invalidation",
    "isin_ticker_index",
    "peers_rejected",
    "vehicle_ter_aum_replication",
    "lookthrough_top10",
    "overlap",
    "precision_score",
    "gate_status",
    "sizing",
    "review_date",
)


def _cell(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        if name in row and pd.notna(row[name]) and str(row[name]).strip():
            return str(row[name]).strip()
    return ""


def _number(row: Mapping[str, Any], *names: str):
    for name in names:
        if name not in row or pd.isna(row[name]):
            continue
        try:
            value = float(row[name])
        except (TypeError, ValueError):
            continue
        if value == value:
            return value
    return None


def build_instruction_fiche(row: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
    sizing = config["books"]["THESIS_MT"]["sizing"]
    aum = _number(row, "fund_total_assets_eur_m", "aum_m", "aum_eur_m")
    ter = _number(row, "ter_pct")
    score = _number(row, "score_final")
    isin = _cell(row, "instrument_id", "isin")
    return {
        "status": "DRAFT_AWAITING_THESIS",
        "book": "THESIS_MT",
        "live_orders_enabled": False,
        "decision_influence": 0.0,
        "prefilled": {
            "isin": isin,
            "name": _cell(row, "name"),
            "ticker": _cell(row, "yahoo_ticker", "ticker_yahoo_final"),
            "category": _cell(row, "category"),
            "benchmark": _cell(row, "official_benchmark"),
            "precision_decision": _cell(row, "decision"),
            "precision_score": None if score is None else round(score, 4),
            "ter_pct": None if ter is None else round(ter, 4),
            "aum_eur_m": None if aum is None else round(aum, 4),
            "risk_indicator": _cell(row, "risk_indicator"),
            "staleness_days": _number(row, "staleness_days"),
            "gate_status": _cell(row, "v21_gate_status"),
            "gate_reasons": _cell(row, "v21_gate_reasons"),
            "gate_warnings": _cell(row, "v21_gate_warnings"),
        },
        "to_complete": {
            "job_horizon": "",
            "thesis_8_12_lines": "",
            "invalidation": "",
            "peers_rejected": "",
            "lookthrough_top10": "",
            "overlap": "",
            "replication": "",
            "review_date": "",
            "sizing_pct": sizing["line_pct_default"],
        },
        "required_instruction_fields": list(config.get("required_instruction_fields", INSTRUCTION_FIELDS)),
        "promotion_allowed": False,
    }


def render_fiche_markdown(fiche: dict) -> str:
    pre = fiche["prefilled"]
    todo = fiche["to_complete"]
    lines = [
        f"# Fiche THESIS_MT — {pre.get('isin') or 'ISIN'}",
        "",
        f"Statut : `{fiche['status']}`  ",
        "Livre : THESIS_MT  ",
        "Ordres réels : non  ",
        "Influence décision : 0",
        "",
        "## Prérempli (snapshot courant)",
        "",
        f"- ISIN : {pre.get('isin') or '—'}",
        f"- Nom : {pre.get('name') or '—'}",
        f"- Ticker : {pre.get('ticker') or '—'}",
        f"- Catégorie / indice : {pre.get('category') or '—'} / {pre.get('benchmark') or '—'}",
        f"- Décision PRECISION : {pre.get('precision_decision') or '—'}",
        f"- Score V20.8.1 : {pre.get('precision_score') if pre.get('precision_score') is not None else '—'}",
        f"- TER % : {pre.get('ter_pct') if pre.get('ter_pct') is not None else '—'}",
        f"- AUM M€ : {pre.get('aum_eur_m') if pre.get('aum_eur_m') is not None else '—'}",
        f"- SRI : {pre.get('risk_indicator') or '—'}",
        f"- Stale jours : {pre.get('staleness_days') if pre.get('staleness_days') is not None else '—'}",
        f"- Gate : {pre.get('gate_status') or '—'} | {pre.get('gate_warnings') or '—'}",
        "",
        "## À compléter avant entrée THESIS_MT",
        "",
        f"- Horizon (18–60 mois) : {todo['job_horizon'] or '_à rédiger_'}",
        f"- Thèse 8–12 lignes : {todo['thesis_8_12_lines'] or '_à rédiger_'}",
        f"- Invalidation : {todo['invalidation'] or '_à rédiger_'}",
        f"- Pairs écartés (≥3) : {todo['peers_rejected'] or '_à rédiger_'}",
        f"- Look-through top 10 : {todo['lookthrough_top10'] or '_si disponible, sinon NON_OBSERVE_'}",
        f"- Overlap cœur : {todo['overlap'] or '_à estimer_'}",
        f"- Réplication : {todo['replication'] or '_physique / swap_'}",
        f"- Date de revue : {todo['review_date'] or '_+63 séances_'}",
        f"- Sizing % : {todo['sizing_pct']}",
        "",
        "Sans thèse et invalidation écrites, la fiche reste DRAFT. Elle ne crée pas d'ordre.",
        "",
    ]
    return "\n".join(lines)


def write_instruction_fiches(annotated: pd.DataFrame, root: Path, config: Mapping[str, Any]) -> dict:
    outputs = root / "outputs" / "etf_mt_v2081"
    fiche_dir = outputs / "fiches_thesis_mt"
    fiche_dir.mkdir(parents=True, exist_ok=True)
    eligible = annotated[annotated["v21_thesis_eligible"].astype(str).str.upper() == "YES"].copy()
    fiches: list[dict] = []
    markdown_parts = ["# Fiches THESIS_MT préremplies", "", "Uniquement les lignes `v21_thesis_eligible=YES`.", ""]
    for _, row in eligible.iterrows():
        fiche = build_instruction_fiche(row.to_dict(), config)
        fiches.append(fiche)
        isin = fiche["prefilled"]["isin"] or f"ROW_{len(fiches)}"
        (fiche_dir / f"{isin}.json").write_text(json.dumps(fiche, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_parts.append(render_fiche_markdown(fiche))
        markdown_parts.append("---")
        markdown_parts.append("")
    pack_json = outputs / "V21_ETF_MT_INSTRUCTION_FICHES.json"
    pack_md = outputs / "V21_ETF_MT_INSTRUCTION_FICHES.md"
    pack_json.write_text(json.dumps(fiches, ensure_ascii=False, indent=2), encoding="utf-8")
    pack_md.write_text("\n".join(markdown_parts), encoding="utf-8")
    return {
        "eligible_rows": int(len(eligible)),
        "fiches_written": int(len(fiches)),
        "outputs": {
            "fiches_dir": str(fiche_dir),
            "pack_json": str(pack_json),
            "pack_md": str(pack_md),
        },
        "score_influence": 0.0,
        "live_orders_enabled": False,
    }


def write_gate_sidecar(snapshot, root: Path, stem: str = "V21_ETF_MT_RANKING_GATES") -> dict:
    """Write a read-only gate sidecar next to V20.8.1 outputs.

    The reference ranking CSV is not rewritten.
    """
    config_path = root / "config" / "ETF_MT_PROCESS_V21.json"
    outputs = root / "outputs" / "etf_mt_v2081"
    outputs.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    annotated, summary = annotate_ranking(snapshot, config)
    csv_path = outputs / f"{stem}.csv"
    json_path = outputs / f"{stem}.json"
    keep = [
        column
        for column in (
            "instrument_id",
            "name",
            "decision",
            "selected",
            "score_final",
            "staleness_days",
            "ter_pct",
            "aum_m",
            "fund_total_assets_eur_m",
            "category",
            "risk_indicator",
            "v21_gate_status",
            "v21_gate_reasons",
            "v21_gate_warnings",
            "v21_thesis_eligible",
        )
        if column in annotated.columns
    ]
    annotated[keep].to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")
    fiche_summary = write_instruction_fiches(annotated, root, config)
    summary["instruction_fiches"] = fiche_summary
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary["outputs"] = {
        "gates_csv": str(csv_path),
        "gates_json": str(json_path),
        **fiche_summary.get("outputs", {}),
    }
    return summary
