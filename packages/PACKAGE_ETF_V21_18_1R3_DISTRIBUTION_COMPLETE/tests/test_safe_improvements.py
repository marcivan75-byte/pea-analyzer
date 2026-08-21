import json
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory

from etf_pack.cache import CacheKey, DeterministicJsonCache
from etf_pack.observability import JsonMetricSink, measured_feature
from etf_pack.promotion import OosEvidence, evaluate_shadow_promotion
from etf_pack.quality import QualityInputs, composite_dqs, quality_state


def test_cache_hash_and_ttl_fail_closed():
    with TemporaryDirectory() as d:
        cache = DeterministicJsonCache(d, ttl=timedelta(days=1))
        now = datetime(2026, 8, 21, tzinfo=UTC)
        key = CacheKey("structure", "FR0000000001", "2026-08-21", "abc")
        cache.put(key, {"ter": 0.001}, written_at=now)
        assert cache.get(key, now=now) == {"ter": 0.001}
        assert cache.get(key, now=now + timedelta(days=2)) is None
        assert cache.get(CacheKey("structure", "FR0000000001", "2026-08-21", "changed"), now=now) is None


def test_cache_rejects_invalid_ttl_and_corrupt_payload():
    with TemporaryDirectory() as directory:
        try:
            DeterministicJsonCache(directory, ttl=timedelta(0))
        except ValueError as exc:
            assert "ttl" in str(exc)
        else:
            raise AssertionError("non-positive TTL must be rejected")

        cache = DeterministicJsonCache(directory, ttl=timedelta(days=1))
        key = CacheKey("structure", "FR0000000001", "2026-08-21", "abc")
        path = cache.put(key, {"ter": 0.001}, written_at=datetime(2026, 8, 21, tzinfo=UTC))
        path.write_text("not-json", encoding="utf-8")
        assert cache.get(key, now=datetime(2026, 8, 21, tzinfo=UTC)) is None


def test_promotion_denied_without_oos_proof():
    ok, reasons = evaluate_shadow_promotion(OosEvidence(False, False, 0, None, None, None, True, False))
    assert not ok and "PIT_INCOMPLETE" in reasons and "NO_INDEPENDENT_HOLDOUT" in reasons


def test_quality_score_and_states():
    assert composite_dqs(QualityInputs(100, 100, 100, 100, 100)) == 100
    assert quality_state(64.9) == "QUARANTINE_CONTEXT_ONLY"
    assert quality_state(70) == "SIGNAL_CONTEXT_ONLY"
    assert quality_state(85).endswith("NO_LIVE_PROMOTION")


def test_observability_emits_structured_event():
    events = []
    with measured_feature(run_id="r1", feature="MT", budget_ms=1000, sink=JsonMetricSink(events.append)):
        pass
    event = json.loads(events[0])
    assert event["run_id"] == "r1" and event["feature"] == "MT" and event["status"] == "PASS"
