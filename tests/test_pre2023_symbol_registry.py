from pathlib import Path
import pandas as pd
import pytest

from v182.data.pre2023_symbol_registry import validate_registry


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "registry.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _base_rows():
    return [
        {
            "instrument_id": "FR0000120073-XPAR",
            "ticker": "AI.PA",
            "eodhd_symbol": "AI.PA",
            "isin": "FR0000120073",
            "exchange": "XPAR",
            "listing_start": "1990-01-01",
            "listing_end": "",
            "status_2022_12_31": "active",
            "source_evidence": "fixture-active",
        },
        {
            "instrument_id": "TEST-DELISTED-XPAR",
            "ticker": "OLD.PA",
            "eodhd_symbol": "OLD.PA",
            "isin": "",
            "exchange": "XPAR",
            "listing_start": "2005-01-01",
            "listing_end": "2018-06-30",
            "status_2022_12_31": "delisted",
            "source_evidence": "fixture-delisted",
        },
    ]


def test_registry_accepts_active_and_historical_delisted(tmp_path):
    df = validate_registry(_write(tmp_path, _base_rows()))
    assert len(df) == 2
    assert set(df["status_2022_12_31"]) == {"active", "delisted"}


def test_registry_rejects_survivors_only(tmp_path):
    rows = [_base_rows()[0]]
    with pytest.raises(ValueError, match="SURVIVORSHIP"):
        validate_registry(_write(tmp_path, rows))


def test_registry_rejects_holdout_dates(tmp_path):
    rows = _base_rows()
    rows[1]["listing_end"] = "2023-01-02"
    with pytest.raises(ValueError, match="HOLDOUT"):
        validate_registry(_write(tmp_path, rows))


def test_registry_rejects_duplicate_source_symbol(tmp_path):
    rows = _base_rows()
    rows[1]["eodhd_symbol"] = rows[0]["eodhd_symbol"]
    with pytest.raises(ValueError, match="duplicate eodhd_symbol"):
        validate_registry(_write(tmp_path, rows))


def test_registry_requires_source_evidence(tmp_path):
    rows = _base_rows()
    rows[1]["source_evidence"] = ""
    with pytest.raises(ValueError, match="blank mandatory"):
        validate_registry(_write(tmp_path, rows))
