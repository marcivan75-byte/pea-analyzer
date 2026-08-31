from tools.v22_1_data.validate_readiness import _compute_gates


def test_technical_core_can_be_authorized_without_nonprice_pit():
    report = {
        "ohlcv_ticker_coverage": 1.0,
        "technical_pit_isin_coverage": 1.0,
        "sector_history": False,
        "quality_roe_debt_history": False,
    }
    gates = _compute_gates(report)
    assert gates["ohlcv_coverage_ge_90pct"] is True
    assert gates["technical_pit_isin_coverage_ge_90pct"] is True
    assert gates["sector_history_validated"] is False
    assert gates["quality_roe_debt_history_validated"] is False


def test_price_or_pit_coverage_remains_fail_closed():
    report = {
        "ohlcv_ticker_coverage": 0.89,
        "technical_pit_isin_coverage": 0.89,
        "sector_history": True,
        "quality_roe_debt_history": True,
    }
    gates = _compute_gates(report)
    assert gates["ohlcv_coverage_ge_90pct"] is False
    assert gates["technical_pit_isin_coverage_ge_90pct"] is False
    assert gates["sector_history_validated"] is True
    assert gates["quality_roe_debt_history_validated"] is True
