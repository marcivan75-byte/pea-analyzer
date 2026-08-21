from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from v182.audit.etf_benchmark_tracking_v21_18 import classify_master, load_config, load_price_map
from v182.sources.etf_structural_data import _benchmark_after_label, _observations_from_text
from v182.state.etf_structure_state import load_state_config

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_benchmark_label_is_collected_with_exact_isin_provenance():
    text = """
    Amundi Index MSCI World SRI PAB UCITS ETF
    ISIN FR0011869353
    Indice de référence MSCI World SRI Filtered PAB Index
    TER 0,18%
    """
    rows = _observations_from_text(
        "FR0011869353",
        text,
        source="issuer factsheet",
        source_url="https://issuer.example/factsheet.pdf",
        evidence="A",
        fallback_as_of="2026-07-31",
    )
    benchmark = [row for row in rows if row["field"] == "official_benchmark"]
    assert len(benchmark) == 1
    assert benchmark[0]["value"] == "MSCI World SRI Filtered PAB Index"
    assert benchmark[0]["evidence_level"] == "A"
    assert benchmark[0]["validation_status"] == "EXACT_ISIN_SOURCE_MATCH"


def test_benchmark_is_never_inferred_from_etf_name_or_category_words():
    text = "ISIN FR0011869353 Amundi MSCI World UCITS ETF category World Equities TER 0.18%"
    assert _benchmark_after_label(text) is None
    rows = _observations_from_text(
        "FR0011869353",
        text,
        source="secondary",
        source_url="https://example.test",
        evidence="B",
        fallback_as_of="2026-08-21",
    )
    assert all(row["field"] != "official_benchmark" for row in rows)


def test_generic_or_overlong_benchmark_labels_are_rejected():
    assert _benchmark_after_label("ISIN XX Benchmark Index TER 0.20%") is None
    huge = " ".join(["VeryLong"] * 30)
    assert _benchmark_after_label(f"Benchmark {huge} TER 0.20%") is None


def test_exact_isin_still_required_for_benchmark_evidence():
    rows = _observations_from_text(
        "FR0011869353",
        "ISIN FR0099999999 Benchmark MSCI World Index TER 0.18%",
        source="secondary",
        source_url="https://example.test",
        evidence="B",
        fallback_as_of="2026-08-21",
    )
    assert rows == []


def test_structural_state_governs_official_benchmark_without_new_cache():
    cfg = load_state_config(ROOT / "config" / "ETF_STRUCTURE_STATE_V21_15.json")
    assert cfg["fields"]["official_benchmark"]["max_age_days"] == 365
    governance = cfg["governance"]
    assert governance["new_cache_family_created"] is False
    assert governance["official_benchmark_requires_explicit_exact_isin_source_label"] is True
    assert governance["benchmark_price_symbol_inference"] is False
    assert governance["tracking_error_activation"] is False


def test_tracking_readiness_blocks_unverified_benchmark_price_mapping():
    cfg = load_config(ROOT / "config" / "ETF_BENCHMARK_TRACKING_V21_18.json")
    master = pd.DataFrame([
        {
            "isin": "FR0011869353",
            "name": "ETF A",
            "official_benchmark": "MSCI World Index",
            "tracking_error_1y_pct": pd.NA,
            "tracking_error_3y_pct": pd.NA,
            "tracking_error_5y_pct": pd.NA,
        }
    ])
    rows, summary = classify_master(master, {}, cfg)
    assert rows.loc[0, "tracking_readiness"] == "BENCHMARK_PRICE_MAPPING_BLOCKED"
    assert bool(rows.loc[0, "tracking_error_computation_enabled"]) is False
    assert summary["verified_benchmark_price_mapping_rows"] == 0
    assert summary["decision_influence"] == 0.0


def test_vendor_tracking_error_remains_context_only_without_verified_price_map():
    cfg = load_config(ROOT / "config" / "ETF_BENCHMARK_TRACKING_V21_18.json")
    master = pd.DataFrame([
        {
            "isin": "FR0011869353",
            "name": "ETF A",
            "official_benchmark": "MSCI World Index",
            "tracking_error_1y_pct": 0.12,
            "tracking_error_3y_pct": pd.NA,
            "tracking_error_5y_pct": pd.NA,
        }
    ])
    rows, summary = classify_master(master, {}, cfg)
    assert rows.loc[0, "vendor_tracking_field_count"] == 1
    assert rows.loc[0, "tracking_readiness"] == "BENCHMARK_PRICE_MAPPING_BLOCKED"
    assert summary["vendor_tracking_any_rows"] == 1
    assert summary["tracking_error_computation_enabled"] is False


def test_price_map_requires_exact_verified_sourced_mapping(tmp_path: Path):
    cfg = load_config(ROOT / "config" / "ETF_BENCHMARK_TRACKING_V21_18.json")
    path = tmp_path / "map.csv"
    pd.DataFrame([
        {
            "official_benchmark": "MSCI World Index",
            "benchmark_price_symbol": "^TEST",
            "provider": "TEST",
            "source": "official index provider",
            "source_url": "https://provider.example/index",
            "evidence_level": "A",
            "validated_as_of": "2026-08-21",
            "status": "VERIFIED",
        }
    ]).to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    mapping, diag = load_price_map(path, cfg)
    assert mapping["MSCI World Index"]["benchmark_price_symbol"] == "^TEST"
    assert diag["verified_rows"] == 1


def test_price_map_duplicate_exact_names_fail_closed(tmp_path: Path):
    cfg = load_config(ROOT / "config" / "ETF_BENCHMARK_TRACKING_V21_18.json")
    path = tmp_path / "map.csv"
    rows = [
        {
            "official_benchmark": "MSCI World Index",
            "benchmark_price_symbol": "A",
            "provider": "P",
            "source": "S",
            "source_url": "https://example.test/a",
            "evidence_level": "A",
            "validated_as_of": "2026-08-21",
            "status": "VERIFIED",
        },
        {
            "official_benchmark": "MSCI World Index",
            "benchmark_price_symbol": "B",
            "provider": "P",
            "source": "S",
            "source_url": "https://example.test/b",
            "evidence_level": "A",
            "validated_as_of": "2026-08-21",
            "status": "VERIFIED",
        },
    ]
    pd.DataFrame(rows).to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="DUPLICATE_EXACT_NAME"):
        load_price_map(path, cfg)


def test_v21_18_governance_forbids_model_or_holdout_change():
    raw = json.loads((ROOT / "config" / "ETF_BENCHMARK_TRACKING_V21_18.json").read_text(encoding="utf-8"))
    governance = raw["governance"]
    assert governance["benchmark_name_inference"] is False
    assert governance["benchmark_price_symbol_inference"] is False
    assert governance["tracking_error_computation_enabled"] is False
    assert governance["decision_influence"] == 0.0
    assert governance["live_orders_enabled"] is False
    assert governance["weights_changed"] is False
    assert governance["thresholds_changed"] is False
    assert governance["t1_t2_scope_changed"] is False
    assert governance["holdout_opened"] is False
