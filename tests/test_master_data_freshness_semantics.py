import pandas as pd

from v182.core.merge import decide
from v182.io.frames import _latest_as_of


def test_legacy_numeric_price_is_not_used_as_timestamp() -> None:
    frame = pd.DataFrame(
        [
            {
                "isin": "FR0013412038",
                "ta_as_of": "2026-08-17",
                "ohlcv_last": "64.14",
                "perf_as_of": "2026-08-18",
            }
        ]
    ).set_index("isin", drop=False)
    assert _latest_as_of(frame, "FR0013412038", ("ta_as_of", "ohlcv_last", "perf_as_of")) == "2026-08-18"


def test_valid_incoming_timestamp_replaces_legacy_numeric_freshness() -> None:
    decision = decide(
        {"value": "10", "evidence_level": "C", "as_of": "64.14"},
        {
            "value": "11",
            "evidence_level": "C",
            "as_of": "2026-08-19",
            "validation_status": "VALIDATED",
        },
    )
    assert decision.action == "REPLACE"
    assert decision.reason == "FRESHER_EQUAL_EVIDENCE"


def test_invalid_numeric_incoming_freshness_is_quarantined() -> None:
    decision = decide(
        {"value": "10", "evidence_level": "C", "as_of": "2026-08-18"},
        {
            "value": "11",
            "evidence_level": "C",
            "as_of": "73.44",
            "validation_status": "VALIDATED",
        },
    )
    assert decision.action == "QUARANTINE"
    assert decision.reason == "INVALID_FRESHNESS_TIMESTAMP"
