from datetime import datetime, timezone
import json

from v182.sources.finnhub_recommendation_history_audit73 import (
    append_capture,
    load_strict_pit_observations,
    normalize_recommendation_series,
)

PAYLOAD = [
    {"period":"2026-08-01","strongBuy":5,"buy":5,"hold":2,"sell":1,"strongSell":0},
    {"period":"2026-07-01","strongBuy":4,"buy":4,"hold":3,"sell":1,"strongSell":0},
    {"period":"2026-06-01","strongBuy":3,"buy":4,"hold":4,"sell":1,"strongSell":0},
    {"period":"2026-05-01","strongBuy":2,"buy":4,"hold":4,"sell":2,"strongSell":0},
]
CAPTURE = datetime(2026, 8, 24, 18, 30, tzinfo=timezone.utc)


def test_preserves_every_month_returned_by_finnhub():
    rows = normalize_recommendation_series(PAYLOAD, captured_at=CAPTURE)
    assert len(rows) == 4
    assert [row["provider_period"] for row in rows] == ["2026-08-01", "2026-07-01", "2026-06-01", "2026-05-01"]


def test_provider_period_is_never_promoted_to_pit_availability():
    rows = normalize_recommendation_series(PAYLOAD, captured_at=CAPTURE)
    assert {row["available_at"] for row in rows} == {"2026-08-24T18:30:00+00:00"}
    assert all(row["provider_period_is_knowledge_timestamp"] is False for row in rows)
    assert rows[-1]["provider_period"] == "2026-05-01"
    assert rows[-1]["available_at"] != "2026-05-01"


def test_append_is_capture_level_and_lossless(tmp_path):
    history = tmp_path / "finnhub.json"
    rows = normalize_recommendation_series(PAYLOAD, captured_at=CAPTURE)
    first = append_capture(history, ticker="TTE.PA", rows=rows, captured_at=CAPTURE, payload_sha256="abc")
    second = append_capture(history, ticker="TTE.PA", rows=rows, captured_at=CAPTURE, payload_sha256="abc")
    assert first["captures_appended"] == 1
    assert first["rows_appended"] == 4
    assert second["status"] == "DUPLICATE_CAPTURE"
    payload = json.loads(history.read_text(encoding="utf-8"))
    assert len(payload["entries"]["TTE.PA"]) == 1
    assert len(payload["entries"]["TTE.PA"][0]["rows"]) == 4


def test_same_payload_on_later_real_retrieval_is_new_knowledge_event(tmp_path):
    history = tmp_path / "finnhub.json"
    rows1 = normalize_recommendation_series(PAYLOAD, captured_at=CAPTURE)
    later = datetime(2026, 8, 25, 18, 30, tzinfo=timezone.utc)
    rows2 = normalize_recommendation_series(PAYLOAD, captured_at=later)
    append_capture(history, ticker="TTE.PA", rows=rows1, captured_at=CAPTURE, payload_sha256="abc")
    append_capture(history, ticker="TTE.PA", rows=rows2, captured_at=later, payload_sha256="abc")
    payload = json.loads(history.read_text(encoding="utf-8"))
    assert len(payload["entries"]["TTE.PA"]) == 2


def test_flattened_strict_pit_rows_all_use_capture_time(tmp_path):
    history = tmp_path / "finnhub.json"
    rows = normalize_recommendation_series(PAYLOAD, captured_at=CAPTURE)
    append_capture(history, ticker="TTE.PA", rows=rows, captured_at=CAPTURE, payload_sha256="abc")
    flat = load_strict_pit_observations(history)
    assert len(flat) == 4
    assert {row["available_at"] for row in flat} == {"2026-08-24T18:30:00+00:00"}
    assert {row["provider_period"] for row in flat} == {"2026-08-01", "2026-07-01", "2026-06-01", "2026-05-01"}
    assert all(row["provider_period_is_knowledge_timestamp"] is False for row in flat)
