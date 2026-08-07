import json
from pathlib import Path

from v182.audit.release_evidence import build_release_evidence


def put(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_release_evidence_ready(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="pea-v182"\nversion="18.2.1"\n', encoding="utf-8")
    put(tmp_path / "config/V18.2_MASTER_CONFIG.json", {"version": "18.2.1"})
    put(tmp_path / "outputs/audit/V18.2_QUALITY_GATES.json", {"passed": True})
    put(tmp_path / "outputs/audit/V18.2_SOURCE_FALLBACK_METRICS.json", {
        "wave01_actions": {"requested": 1486, "successful": 1410},
        "wave02_etf": {"requested": 102, "successful": 100},
        "openfigi": {"coverage_pct": 90},
        "wave04_yfinance": {"available_pct": 95},
        "wave05_finnhub": {"available_pct": 96},
        "macro_fred": {"success": True},
        "energy_eia": {"success": True},
    })
    put(tmp_path / "outputs/audit/V18.2_OHLCV_GAP_METRICS.json", {
        "actions_total": 1486, "etf_total": 102,
        "actions_last_close_coverage_pct": 94.9, "etf_last_close_coverage_pct": 98,
    })
    put(tmp_path / "outputs/audit/V18.2_SCENARIO_METRICS.json", {"scenario_isins": 300})
    put(tmp_path / "outputs/audit/V18.2_ANALYST_MOMENTUM_METRICS.json", {
        "execution_gate": "SHADOW_BLOCKED",
        "marketbeat": {"success": True, "selected": 3, "successful": 2, "observations": 30, "quarantined": 0},
        "marketbeat_overlay": {"rows": 2},
    })
    put(tmp_path / "outputs/context/V18.2_MACRO_CONTEXT.json", {"source": "FRED"})
    put(tmp_path / "outputs/context/V18.2_ENERGY_CONTEXT.json", {"source": "EIA"})
    monkeypatch.setenv("GITHUB_SHA", "abc123final")
    evidence = build_release_evidence(tmp_path)
    assert evidence["ready_for_integration"] is True
    assert evidence["tested_commit_sha"] == "abc123final"
    assert all(evidence["checks"].values())
    assert (tmp_path / "outputs/release/V18.2_TRACKED_FILES_SHA256.txt").stat().st_size > 0
