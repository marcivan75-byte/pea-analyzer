from pathlib import Path

import pandas as pd


def test_etf_ticker_map_keeps_102_unique_isins_and_dax_yahoo_symbol():
    path = Path("config/V18.2_ETF_TICKER_MAP.csv")
    mapping = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)

    assert len(mapping) == 102
    assert mapping["isin"].nunique() == 102

    row = mapping.loc[mapping["isin"].eq("LU0252633754")]
    assert len(row) == 1
    record = row.iloc[0]
    assert record["ticker_primary"] == "DAX"
    assert record["mic"] == "XPAR"
    assert record["yahoo_ticker"] == "DAX.PA"
    assert record["validation_status"] == "FINAL_VALIDATED"
    assert record["source_url"] == "https://fr.finance.yahoo.com/quote/DAX.PA/"
    assert record["as_of"] == "2026-08-20"


def test_legacy_lyxdax_yahoo_symbol_is_not_present():
    mapping = pd.read_csv(
        Path("config/V18.2_ETF_TICKER_MAP.csv"),
        sep=";",
        encoding="utf-8-sig",
        dtype=str,
    )
    assert "LYXDAX.PA" not in set(mapping["yahoo_ticker"].dropna())
