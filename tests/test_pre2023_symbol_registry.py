from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pandas as pd
import pytest

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "v182" / "data" / "pre2023_symbol_registry.py"
_spec = spec_from_file_location("pea_pre2023_symbol_registry", REGISTRY_PATH)
assert _spec is not None and _spec.loader is not None
_registry = module_from_spec(_spec)
_spec.loader.exec_module(_registry)
validate_registry = _registry.validate_registry
export_collector_mapping = _registry.export_collector_mapping


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "registry.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _row(**overrides):
    row = {
        "instrument_id": "FR0000120073-XPAR",
        "ticker": "AI.PA",
        "eodhd_symbol": "AI.PA",
        "isin": "FR0000120073",
        "exchange": "XPAR",
        "listing_start": "1990-01-01",
        "listing_end": "",
        "eligibility_start": "2010-01-01",
        "eligibility_end": "",
        "status_2022_12_31": "active",
        "universe_method": "provider_active_plus_delisted",
        "source_provider": "EODHD",
        "source_retrieved_at": "2026-09-01T00:00:00Z",
        "source_evidence": "fixture-active",
        "eligibility_evidence": "fixture-historical-pea-eligibility",
    }
    row.update(overrides)
    return row


def _base_rows():
    return [
        _row(),
        _row(
            instrument_id="TEST-DELISTED-XPAR",
            ticker="OLD.PA",
            eodhd_symbol="OLD_old.PA",
            isin="",
            listing_start="2005-01-01",
            listing_end="2018-06-30",
            eligibility_start="2010-01-01",
            eligibility_end="2018-06-30",
            status_2022_12_31="delisted",
            source_evidence="fixture-delisted",
        ),
    ]


def test_registry_accepts_active_and_historical_delisted(tmp_path):
    df = validate_registry(_write(tmp_path, _base_rows()))
    assert len(df) == 2
    assert set(df["status_2022_12_31"]) == {"active", "delisted"}


def test_registry_rejects_survivors_only(tmp_path):
    with pytest.raises(ValueError, match="SURVIVORSHIP"):
        validate_registry(_write(tmp_path, [_base_rows()[0]]))


def test_registry_rejects_current_snapshot_backfill(tmp_path):
    rows = _base_rows()
    for row in rows:
        row["universe_method"] = "current_snapshot_backfill"
    with pytest.raises(ValueError, match="SURVIVORSHIP"):
        validate_registry(_write(tmp_path, rows))


def test_registry_rejects_holdout_dates(tmp_path):
    rows = _base_rows()
    rows[1]["listing_end"] = "2023-01-02"
    with pytest.raises(ValueError, match="HOLDOUT"):
        validate_registry(_write(tmp_path, rows))


def test_registry_rejects_eligibility_before_listing(tmp_path):
    rows = _base_rows()
    rows[1]["listing_start"] = "2012-01-01"
    rows[1]["eligibility_start"] = "2010-01-01"
    with pytest.raises(ValueError, match="ELIGIBILITY"):
        validate_registry(_write(tmp_path, rows))


def test_registry_rejects_inactive_without_listing_end(tmp_path):
    rows = _base_rows()
    rows[1]["listing_end"] = ""
    with pytest.raises(ValueError, match="STATUS"):
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


def test_same_display_ticker_can_be_reused_by_distinct_instruments(tmp_path):
    rows = _base_rows()
    rows[1]["ticker"] = rows[0]["ticker"]
    registry = validate_registry(_write(tmp_path, rows))
    mapping_path = export_collector_mapping(registry, tmp_path / "mapping.csv")
    mapping = pd.read_csv(mapping_path, dtype=str)
    assert set(mapping["ticker"]) == {"FR0000120073-XPAR", "TEST-DELISTED-XPAR"}
    assert not mapping["ticker"].duplicated().any()
