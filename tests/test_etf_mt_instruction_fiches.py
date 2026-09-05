from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision.etf_mt_operational_gates import annotate_ranking
from v182.reporting.etf_mt_process_annotate import build_instruction_fiche, write_gate_sidecar


def _config() -> dict:
    return json.loads(Path("config/ETF_MT_PROCESS_V21.json").read_text(encoding="utf-8"))


def test_fiche_only_for_eligible_and_stays_draft(tmp_path: Path):
    cfg = _config()
    ranking = pd.DataFrame(
        [
            {
                "instrument_id": "IE00ELIG",
                "name": "World Core",
                "score_final": 88.4,
                "selected": True,
                "decision": "BUY_CANDIDATE",
                "fund_total_assets_eur_m": 900.0,
                "ter_pct": 0.12,
                "category": "WORLD",
                "staleness_days": 1,
            },
            {
                "instrument_id": "FR00BLOCK",
                "name": "Tiny Theme",
                "score_final": 84.1,
                "selected": True,
                "decision": "BUY_CANDIDATE",
                "fund_total_assets_eur_m": 12.0,
                "ter_pct": 0.40,
                "category": "WORLD",
                "staleness_days": 1,
            },
        ]
    )
    annotated, _ = annotate_ranking(ranking, cfg)
    assert annotated.loc[0, "v21_thesis_eligible"] == "YES"
    assert annotated.loc[1, "v21_thesis_eligible"] == "NO"
    fiche = build_instruction_fiche(annotated.iloc[0].to_dict(), cfg)
    assert fiche["status"] == "DRAFT_AWAITING_THESIS"
    assert fiche["prefilled"]["isin"] == "IE00ELIG"
    assert fiche["prefilled"]["precision_score"] == 88.4
    assert fiche["to_complete"]["thesis_8_12_lines"] == ""
    assert fiche["live_orders_enabled"] is False

    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "ETF_MT_PROCESS_V21.json").write_text(json.dumps(cfg), encoding="utf-8")
    summary = write_gate_sidecar(ranking, root)
    assert summary["instruction_fiches"]["fiches_written"] == 1
    pack = json.loads(Path(summary["outputs"]["pack_json"]).read_text(encoding="utf-8"))
    assert len(pack) == 1
    assert pack[0]["prefilled"]["isin"] == "IE00ELIG"
    md = Path(summary["outputs"]["pack_md"]).read_text(encoding="utf-8")
    assert "IE00ELIG" in md
    assert "FR00BLOCK" not in md
    assert "DRAFT_AWAITING_THESIS" in md
