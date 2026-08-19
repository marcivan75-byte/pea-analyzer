from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_process_reference_records_daily_weekly_scope_not_day_trading():
    process = (ROOT / "docs" / "PROCESS_REFERENCE_V21_8_1_FINAL.md").read_text(encoding="utf-8")
    assert "V24.3.0 Daily/Weekly Trader Tools SHADOW" in process
    assert "L'objectif n'est **pas de faire du day trading**" in process
    assert "Données intraday, 5 minutes, quasi temps réel" in process
    assert "EXCLUS du chantier TCT" in process
    assert "aucun cache intraday TCT actif" in process
    assert "CT reste gelé" in process
    assert "Production V21.8.1" in process or "production V21.8.1" in process


def test_process_reference_retires_v242_from_active_runtime():
    process = (ROOT / "docs" / "PROCESS_REFERENCE_V21_8_1_FINAL.md").read_text(encoding="utf-8")
    assert "V24.2.x Intraday/Scalping est **ABANDONNÉE et retirée du runtime actif**" in process
    assert "Aucun résultat V24.2.x" in process or "ses résultats éventuels ne doivent jamais être utilisés" in process


def test_scope_correction_forbids_extra_market_data_costs():
    scope = (ROOT / "docs" / "TCT_V24_3_0_SCOPE_CORRECTION_2026-08-19.md").read_text(encoding="utf-8")
    assert "Il ne s'agit pas de faire du day trading" in scope
    assert "ne nécessite aucun abonnement ou flux supplémentaire" in scope
    assert "réutilise exclusivement le cache OHLCV quotidien" in scope
    assert "Influence décision/score/sizing/stop/CT = 0" in scope
    assert "Holdout final fermé" in scope
