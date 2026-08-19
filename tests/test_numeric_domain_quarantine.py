from pathlib import Path

import pandas as pd

from v182.core.data_domain import filter_numeric_series, validate_numeric_value
from v182.io.frames import apply_observations, is_missing


def _obs(field: str, value) -> dict:
    return {
        "universe": "ACTION",
        "isin": "FR0013412038",
        "field": field,
        "value": value,
        "source": "TEST",
        "evidence_level": "C",
        "as_of": "2026-08-19",
        "validation_status": "VALIDATED",
    }


def test_domain_rejects_known_impossible_or_unsafe_values() -> None:
    assert validate_numeric_value("perf_3y_pct", -141.36)[0] is False
    assert validate_numeric_value("dividend_yield_pct", 299.6)[0] is False
    assert validate_numeric_value("volatility_1y_pct", 682.98)[0] is False
    assert validate_numeric_value("perf_3y_pct", 141.36)[0] is True
    assert validate_numeric_value("dividend_yield_pct", 4.5)[0] is True


def test_ingestion_quarantines_out_of_domain_without_overwriting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PEA_PROVENANCE_PATH", str(tmp_path / "provenance.csv"))
    frame = pd.DataFrame([{"isin": "FR0013412038", "perf_3y_pct": pd.NA, "dividend_yield_pct": "4.5"}])
    enriched, quarantine = apply_observations(
        frame,
        [_obs("perf_3y_pct", -141.36), _obs("dividend_yield_pct", 299.6)],
    )
    assert is_missing(enriched.loc[0, "perf_3y_pct"])
    assert enriched.loc[0, "dividend_yield_pct"] == "4.5"
    assert len(quarantine) == 2
    assert all(str(item["reason"]).startswith("NUMERIC_DOMAIN:") for item in quarantine)


def test_ingestion_accepts_valid_bounded_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PEA_PROVENANCE_PATH", str(tmp_path / "provenance.csv"))
    frame = pd.DataFrame([{"isin": "FR0013412038", "perf_3y_pct": pd.NA}])
    enriched, quarantine = apply_observations(frame, [_obs("perf_3y_pct", 141.36)])
    assert quarantine == []
    assert float(enriched.loc[0, "perf_3y_pct"]) == 141.36


def test_series_filter_turns_invalid_cells_into_missing() -> None:
    series = pd.Series([10.0, -141.36, 80.0])
    filtered, invalid = filter_numeric_series(series, "perf_3y_pct")
    assert filtered.notna().tolist() == [True, False, True]
    assert invalid.tolist() == [False, True, False]
