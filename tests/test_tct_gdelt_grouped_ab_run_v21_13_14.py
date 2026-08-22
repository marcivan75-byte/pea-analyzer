from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd
import pytest

from v182.features import tct_catalyst_context_v24_4_2 as feature
from v182.reporting import tct_gdelt_grouped_ab_run_v21_13_14 as runner
from v182.sources.tct_catalyst_news_v24_4_2 import NewsBatch


ROOT = Path(__file__).resolve().parents[1]


def _write_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config/TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").write_text(
        json.dumps(
            {
                "state": {"catalyst_ledger_path": "state/tct_context/TCT_V24_4_2_CATALYST_LEDGER.csv"},
                "news": {"preopen_fetch_timespan": "7d", "postmarket_fetch_timespan": "1d"},
            }
        ),
        encoding="utf-8",
    )


def _row(*, generated: str, phase: str = "PREOPEN", isin: str = "FR1", name: str = "Alpha SA") -> dict:
    return {
        "version": feature.VERSION,
        "phase": phase,
        "isin": isin,
        "name": name,
        "snapshot_generated_at_utc": generated,
        "snapshot_window_start_utc": "2026-08-21T15:30:00+00:00",
        "snapshot_window_end_utc": "2026-08-22T06:40:00+00:00",
        "news_window_start_utc": "2026-08-21T15:30:00+00:00",
        "news_window_end_utc": "2026-08-22T06:40:00+00:00",
        "news_magnitude_score": 92.0,
        "news_direction_score": 95.0,
        "news_confidence": 0.8,
        "news_match_confidence": 0.75,
        "news_article_count": 1,
        "news_independent_sources": 1,
        "news_event_types": "GUIDANCE_RAISED",
        "news_top_headlines": "Alpha SA raises guidance",
        "news_source": "GDELT_WINDOWED_V24_4_2",
        "news_cache_hit": False,
        "news_error": None,
    }


def _write_ledger(root: Path, rows: list[dict]) -> Path:
    path = root / "state/tct_context/TCT_V24_4_2_CATALYST_LEDGER.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    return path


def test_latest_snapshot_selects_current_v2442_epoch_and_requested_phase():
    ledger = pd.DataFrame([
        _row(generated="2026-08-22T05:00:00+00:00"),
        _row(generated="2026-08-22T06:40:00+00:00", isin="FR2", name="Beta SE"),
        {**_row(generated="2026-08-22T07:00:00+00:00", isin="OLD"), "version": "OLD_VERSION"},
        _row(generated="2026-08-22T08:00:00+00:00", phase="POSTMARKET", isin="POST"),
    ])
    latest = runner._latest_snapshot(ledger, "PREOPEN")
    assert list(latest["isin"]) == ["FR2"]
    assert set(latest["version"]) == {feature.VERSION}


def test_baseline_news_treats_csv_nan_as_missing_and_falls_back_to_snapshot_window():
    row = pd.Series({
        **_row(generated="2026-08-22T06:40:00+00:00"),
        "news_window_start_utc": float("nan"),
        "news_window_end_utc": pd.NA,
        "news_error": float("nan"),
        "news_cache_hit": pd.NA,
    })
    news = runner._baseline_news(row)
    assert news.window_start_utc == "2026-08-21T15:30:00+00:00"
    assert news.window_end_utc == "2026-08-22T06:40:00+00:00"
    assert news.error is None
    assert news.cache_hit is False


def test_ab_run_uses_existing_pit_baseline_and_only_calls_grouped_fetch(monkeypatch, tmp_path):
    _write_config(tmp_path)
    rows = [
        _row(generated="2026-08-22T06:40:00+00:00", isin="FR1", name="Alpha SA"),
        {**_row(generated="2026-08-22T06:40:00+00:00", isin="FR2", name="Beta SE"),
         "news_magnitude_score": 88.0, "news_direction_score": -85.0,
         "news_event_types": "PROFIT_WARNING", "news_top_headlines": "Beta SE profit warning"},
    ]
    _write_ledger(tmp_path, rows)
    calls: list[dict] = []

    def grouped_fetch(candidates, *, start_utc, end_utc, phase, cfg, group_size):
        calls.append({"candidates": candidates, "start": start_utc, "end": end_utc, "phase": phase, "group_size": group_size})
        baseline = {row["isin"]: runner._baseline_news(pd.Series(row)) for row in rows}
        return NewsBatch(baseline, metrics={"grouped_request_count": 1, "mode": "TEST"})

    monkeypatch.setattr(runner.grouped_shadow, "fetch_candidate_news_grouped_shadow", grouped_fetch)
    payload = runner.run(tmp_path, phase="PREOPEN", group_size=5, now=datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc))
    assert len(calls) == 1
    assert calls[0]["phase"] == "PREOPEN"
    assert [item["isin"] for item in calls[0]["candidates"]] == ["FR1", "FR2"]
    assert payload["baseline_source"] == "EXISTING_V24_4_2_PIT_LEDGER_NO_NEW_INDIVIDUAL_REQUESTS"
    assert payload["new_individual_requests"] == 0
    assert payload["exact_equivalence_rate"] == 1.0
    assert payload["projected_request_reduction_pct"] == 50.0
    assert payload["production_activation"] is False
    assert payload["promotion_authority"] is False
    assert (tmp_path / "outputs/audit/GDELT_GROUPED_AB_V21_13_14.json").exists()
    assert (tmp_path / "outputs/audit/GDELT_GROUPED_AB_V21_13_14_ROWS.csv").exists()


def test_ab_run_fails_closed_before_network_when_snapshot_window_is_too_old(monkeypatch, tmp_path):
    _write_config(tmp_path)
    _write_ledger(tmp_path, [_row(generated="2026-08-22T06:40:00+00:00")])
    called = False

    def grouped_fetch(*args, **kwargs):
        nonlocal called
        called = True
        return NewsBatch()

    monkeypatch.setattr(runner.grouped_shadow, "fetch_candidate_news_grouped_shadow", grouped_fetch)
    with pytest.raises(RuntimeError, match="older than the configured GDELT timespan"):
        runner.run(tmp_path, phase="PREOPEN", now=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))
    assert called is False


def test_manual_ab_workflow_has_no_schedule_and_uses_minimal_dependencies():
    workflow = (ROOT / ".github/workflows/tct_gdelt_grouped_ab_manual.yml").read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "cron:" not in trigger
    assert "requirements-catalyst.txt" in workflow
    assert "pip install -e ." not in workflow
    assert "python -m v182.reporting.tct_gdelt_grouped_ab_run_v21_13_14" in workflow
    assert "state/tct_context/" in workflow
    assert "GDELT_GROUPED_AB_V21_13_14.json" in workflow
    assert "GDELT_GROUPED_AB_V21_13_14_ROWS.csv" in workflow


def test_ab_harness_is_not_imported_by_production_catalyst_runner():
    production = (ROOT / "src/v182/reporting/tct_next_session_catalyst_run_v24_4_2.py").read_text(encoding="utf-8")
    postmarket = (ROOT / "src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    needle = "tct_gdelt_grouped_ab_run_v21_13_14"
    assert needle not in production
    assert needle not in postmarket
