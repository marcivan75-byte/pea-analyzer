from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_process_reference_records_v242_as_shadow_only():
    process = (ROOT / "docs" / "PROCESS_REFERENCE_V21_8_1_FINAL.md").read_text(encoding="utf-8")
    assert "TCT V24.2.0 Intraday/Scalping" in process
    assert "V24.2.1 Analytics" in process
    assert "SHADOW_RESEARCH_ONLY" in process
    assert "influence décision, score, sizing, stop et CT = **0**" in process
    assert "J+1 au plus tôt" in process
    assert "CT reste gelé" in process
    assert "Production V21.8.1" in process or "production V21.8.1" in process


def test_process_reference_does_not_claim_statistical_validation():
    process = (ROOT / "docs" / "PROCESS_REFERENCE_V21_8_1_FINAL.md").read_text(encoding="utf-8")
    assert "NON VALIDÉS" in process
    assert "ACCUMULATING_EARLY" in process
    assert "READY_FOR_PRE_REGISTERED_REVIEW_NOT_PROMOTION" in process
    assert "holdout" in process.lower()


def test_tct_v242_audit_status_keeps_chantier_open():
    audit = (ROOT / "docs" / "TCT_V24_2_X_AUDIT_STATUS_2026-08-18.md").read_text(encoding="utf-8")
    assert "TECHNIQUE SHADOW : VALIDÉ EN NON-RÉGRESSION" in audit
    assert "STATISTIQUE / PROMOTION : NON VALIDÉ" in audit
    assert "Aucun **run représentatif post-intégration sur données réelles V24.2.x**" in audit
    assert "Le CT reste gelé" in audit
    assert "holdout final : fermé" in audit
    assert "WIP : 1" in audit
