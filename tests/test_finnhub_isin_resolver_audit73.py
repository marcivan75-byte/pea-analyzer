from v182.mapping.finnhub_isin_resolver_audit73 import (
    assert_no_unsafe_mapping,
    certify_from_stock_symbol_rows,
    certify_universe,
    sanitize_detail,
)


def test_unique_exact_isin_equity_is_certified():
    rows = [{"isin":"FR0000120271","symbol":"TTE.PA","type":"Common Stock","description":"TOTALENERGIES SE"}]
    decision = certify_from_stock_symbol_rows("FR0000120271", rows, observed_at_utc="2026-09-01T20:00:00+00:00")
    assert decision.status == "CERTIFIED"
    assert decision.finnhub_symbol == "TTE.PA"


def test_name_or_symbol_without_exact_isin_is_never_enough():
    rows = [{"isin":"FR9999999999","symbol":"TTE.PA","type":"Common Stock","description":"TOTALENERGIES SE"}]
    decision = certify_from_stock_symbol_rows("FR0000120271", rows)
    assert decision.status == "UNAVAILABLE"
    assert decision.finnhub_symbol == ""


def test_non_equity_exact_isin_is_rejected():
    rows = [{"isin":"LU1615090864","symbol":"ETF.PA","type":"ETF"}]
    decision = certify_from_stock_symbol_rows("LU1615090864", rows)
    assert decision.status == "UNAVAILABLE"


def test_multiple_symbols_for_exact_isin_fail_closed():
    rows = [
        {"isin":"DE000BASF111","symbol":"BAS.DE","type":"Common Stock"},
        {"isin":"DE000BASF111","symbol":"BASF.F","type":"Common Stock"},
    ]
    decision = certify_from_stock_symbol_rows("DE000BASF111", rows)
    assert decision.status == "BLOCKED"
    assert decision.finnhub_symbol == ""


def test_duplicate_evidence_same_symbol_is_allowed():
    rows = [
        {"isin":"DE000BASF111","symbol":"BAS.DE","type":"Common Stock"},
        {"isin":"DE000BASF111","symbol":"BAS.DE","type":"Equity"},
    ]
    decision = certify_from_stock_symbol_rows("DE000BASF111", rows)
    assert decision.status == "CERTIFIED"
    assert decision.evidence_count == 2


def test_universe_maps_actions_only_and_comments_unavailable():
    universe = [
        {"isin":"DE000BASF111","name":"BASF SE","asset_class":"ACTION"},
        {"isin":"LU1615090864","name":"ETF","asset_class":"ETF"},
        {"isin":"ES0173516115","name":"REPSOL SA","asset_class":"ACTION"},
    ]
    evidence = [{"isin":"DE000BASF111","symbol":"BAS.DE","type":"Common Stock"}]
    output = certify_universe(universe, evidence)
    assert len(output) == 2
    by_isin = {row["isin"]: row for row in output}
    assert by_isin["DE000BASF111"]["status"] == "CERTIFIED"
    assert by_isin["ES0173516115"]["status"] == "UNAVAILABLE"
    assert by_isin["ES0173516115"]["criterion_policy"].endswith("COMMENT_UNAVAILABLE")


def test_token_is_redacted():
    text = sanitize_detail("https://finnhub.io/api/v1/stock/symbol?exchange=FR&token=supersecret boom")
    assert "supersecret" not in text
    assert "<REDACTED>" in text


def test_unsafe_noncertified_symbol_is_blocked():
    try:
        assert_no_unsafe_mapping([{"status":"UNAVAILABLE","finnhub_symbol":"BAD.PA"}])
    except ValueError as exc:
        assert "BLOCK_UNSAFE_FINNHUB_MAPPING" in str(exc)
    else:
        raise AssertionError("unsafe mapping should fail closed")
