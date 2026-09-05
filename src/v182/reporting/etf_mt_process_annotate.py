from __future__ import annotations

from pathlib import Path
import json

from v182.decision.etf_mt_operational_gates import annotate_ranking


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
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["outputs"] = {"gates_csv": str(csv_path), "gates_json": str(json_path)}
    return summary
