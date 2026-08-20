from __future__ import annotations

from v182.sources.etf_structural_data import _source_date


def test_generic_future_product_date_cannot_become_structural_as_of():
    text = "ISIN IE00B910VR50 TER 0.08% prochaine distribution 30/11/2026"
    assert _source_date(text, "2026-08-20") == "2026-08-20"


def test_future_generic_date_is_skipped_for_eligible_past_date():
    text = "prochaine distribution 30/11/2026 document du 31/07/2026"
    assert _source_date(text, "2026-08-20") == "2026-07-31"


def test_labelled_observation_date_remains_preferred_when_eligible():
    text = "Date de VL et d'actif géré 30/06/2026 prochaine distribution 30/11/2026"
    assert _source_date(text, "2026-08-20") == "2026-06-30"
