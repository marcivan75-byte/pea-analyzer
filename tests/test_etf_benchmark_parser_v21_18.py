from __future__ import annotations

from v182.sources.etf_structural_data import _benchmark_after_label


def test_french_structural_label_terminates_benchmark_value():
    text = (
        "ISIN FR0011869353 Indice de référence MSCI World SRI Filtered PAB Index "
        "Actif géré 500,00 millions EUR Frais courants 0,18%"
    )
    assert _benchmark_after_label(text) == "MSCI World SRI Filtered PAB Index"


def test_management_fee_label_terminates_benchmark_value():
    text = (
        "ISIN FR0011869353 Benchmark MSCI World Index "
        "Frais de gestion et autres coûts administratifs 0,18%"
    )
    assert _benchmark_after_label(text) == "MSCI World Index"
