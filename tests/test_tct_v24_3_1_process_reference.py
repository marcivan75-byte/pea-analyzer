from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2431_integration_note_supersedes_v2430_runtime_scope():
    note = (ROOT / "docs" / "TCT_V24_3_1_ROBUST_DAILY_WEEKLY_INTEGRATION.md").read_text(encoding="utf-8")
    assert "complète et supersède" in note
    assert "V24.3.1 reste SHADOW_RESEARCH_ONLY" in note
    assert "Il ne s'agit pas de day trading" in note
    assert "OHLCV daily uniquement" in note
    assert "aucun 1m/5m" in note
    assert "réutilisation exclusive de `data/cache/actions`" in note
    assert "Influence décision/score/sizing/stop/CT = 0" in note
    assert "Holdout final fermé" in note


def test_v2431_documented_gates_match_intent():
    note = (ROOT / "docs" / "TCT_V24_3_1_ROBUST_DAILY_WEEKLY_INTEGRATION.md").read_text(encoding="utf-8")
    assert "ENTRY_READY_SHADOW` exige au moins 2 confirmations" in note
    assert "ENTRY_STRONG_SHADOW` exige au moins 3 confirmations" in note
    assert "failed breakout" in note
    assert "weekly fortement adverse" in note
    assert "invalidation structurelle à plus de 7 %" in note
    assert "clôture sous le plus bas de la veille" in note
    assert "bougie quotidienne du jour" in note
