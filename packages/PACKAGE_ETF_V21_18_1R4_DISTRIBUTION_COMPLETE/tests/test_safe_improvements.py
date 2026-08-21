import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from etf_pack.cache import CacheKey, DeterministicJsonCache
from etf_pack.observability import JsonlMetricSink, JsonMetricSink, MetricEvent, measured_feature, summarize_metrics
from etf_pack.promotion import OosEvidence, PromotionThresholds, evaluate_shadow_promotion
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
        assert list((cache.quarantine_dir).glob("*.invalid_payload.json"))


def test_cache_bulk_round_trip():
    with TemporaryDirectory() as directory:
        now = datetime(2026, 8, 21, tzinfo=UTC)
        cache = DeterministicJsonCache(directory, ttl=timedelta(days=1))
        keys = [CacheKey("structure", f"ETF-{index}", "2026-08-21", "abc") for index in range(3)]
        cache.bulk_put([(key, {"value": index}) for index, key in enumerate(keys)], written_at=now)
        assert len(cache.bulk_get(keys, now=now)) == 3


def test_promotion_denied_without_oos_proof():
    ok, reasons = evaluate_shadow_promotion(OosEvidence(False, False, 0, None, None, None, True, False))
    assert not ok and "PIT_INCOMPLETE" in reasons and "NO_INDEPENDENT_HOLDOUT" in reasons


def test_promotion_enforces_quantitative_thresholds():
    evidence = OosEvidence(True, True, 12, -0.1, 1.2, -0.1, True, True)
    ok, reasons = evaluate_shadow_promotion(evidence, PromotionThresholds())
    assert not ok
    assert {"IR_BELOW_THRESHOLD", "TURNOVER_ABOVE_THRESHOLD", "DRAWDOWN_DELTA_BELOW_THRESHOLD"} <= set(reasons)


def test_promotion_accepts_complete_evidence():
    evidence = OosEvidence(True, True, 12, 0.5, 0.4, 0.1, True, True)
    assert evaluate_shadow_promotion(evidence) == (True, ())


def test_quality_score_and_states():
    assert composite_dqs(QualityInputs(100, 100, 100, 100, 100)) == 100
    assert quality_state(64.9) == "QUARANTINE_CONTEXT_ONLY"
    assert quality_state(70) == "SIGNAL_CONTEXT_ONLY"
    assert quality_state(85).endswith("NO_LIVE_PROMOTION")


def test_quality_rejects_invalid_weights():
    try:
        composite_dqs(QualityInputs(100, 100, 100, 100, 100), {"freshness": 1.0})
    except ValueError as exc:
        assert "weights" in str(exc)
    else:
        raise AssertionError("incomplete quality weights accepted")


def test_quality_latency_penalty_is_explicit():
    assert composite_dqs(QualityInputs(100, 100, 100, 100, 100, 0)) == 90


def test_observability_emits_structured_event():
    events = []
    with measured_feature(run_id="r1", feature="MT", budget_ms=1000, sink=JsonMetricSink(events.append)):
        pass
    event = json.loads(events[0])
    assert event["run_id"] == "r1" and event["feature"] == "MT" and event["status"] == "PASS"


def test_jsonl_metric_sink_and_summary():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "metrics.jsonl"
        sink = JsonlMetricSink(path)
        events = [MetricEvent("r1", "a", 10, 20, "PASS"), MetricEvent("r1", "b", 30, 20, "SLA_BREACH")]
        for event in events:
            sink.write(event)
        assert len(path.read_text(encoding="utf-8").splitlines()) == 2
        summary = summarize_metrics(events)
        assert summary["count"] == 2 and summary["breach_rate"] == 0.5
