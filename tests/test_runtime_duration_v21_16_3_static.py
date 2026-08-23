from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting.daily_tct_ct_runner import _daily_exact_scope


ROOT = Path(__file__).resolve().parents[1]


def _requirements(path: str) -> set[str]:
    rows = set()
    for raw in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==", 1)[0].split(">=", 1)[0].split("<=", 1)[0].split("~=", 1)[0].strip().lower()
        rows.add(name)
    return rows


def test_daily_dependency_profile_is_strict_subset_without_document_browser_stack() -> None:
    full = _requirements("requirements-runtime.txt")
    daily = _requirements("requirements-daily-fast.txt")
    constraints = _requirements("constraints-ci.txt")
    assert daily < full
    assert daily <= constraints
    assert {"pypdf", "playwright", "openpyxl", "python-docx"}.isdisjoint(daily)
    assert {"pandas", "numpy", "pyarrow", "yfinance", "requests", "beautifulsoup4", "lxml", "ta"} <= daily


def test_weekly_dependency_profile_retains_ci_document_builders_only() -> None:
    full = _requirements("requirements-runtime.txt")
    weekly = _requirements("requirements-market-runtime.txt")
    constraints = _requirements("constraints-ci.txt")
    assert weekly < full
    assert weekly <= constraints
    assert {"pypdf", "playwright"}.isdisjoint(weekly)
    assert {"openpyxl", "python-docx"} <= weekly


def test_workflows_use_lean_dependency_profiles() -> None:
    daily = (ROOT / ".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    weekly = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    assert "requirements-daily-fast.txt" in daily
    assert "requirements-runtime.txt" not in daily
    assert "requirements-market-runtime.txt" in weekly
    assert "requirements-runtime.txt" not in weekly


def test_daily_t1_t2_exact_scope_preserves_full_baseline_gate_and_caps_to_top20() -> None:
    frame = pd.DataFrame(
        {
            "isin": [f"X{i:04d}" for i in range(1, 41)],
            "tct_baseline_rank": list(range(1, 41)),
            "tct_baseline_coverage": [0.9] * 19 + [0.59] + [0.9] * 20,
        }
    )
    cfg = {"scope": {"baseline_top_n": 20, "baseline_min_coverage": 0.60}}
    scoped = _daily_exact_scope(frame, cfg)
    assert len(scoped) == 19
    assert scoped["tct_baseline_rank"].max() == 19
    assert set(scoped["isin"]) == set(frame.loc[:18, "isin"])


def test_daily_workflow_keeps_full_screening_and_defers_only_research_validation() -> None:
    collector = (ROOT / "src/v182/reporting/daily_fast_collection.py").read_text(encoding="utf-8")
    postmarket = (ROOT / "src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    assert '"universe_reduced": False' in collector
    assert "WAVE_04_ACTION_FUNDAMENTALS" in collector
    assert "heavy_slow_source_waves_skipped" in collector
    assert 'deferred = ["PIT_LINEAGE_V24.4.2", "PIT_VALIDATOR_V24.4.2"]' in postmarket
    assert 'daily_deferred_steps_have_decision_influence": False' in postmarket


def test_catalyst_overlaps_independent_news_and_global_market_only() -> None:
    source = (ROOT / "src/v182/reporting/tct_next_session_catalyst_engine.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor(max_workers=2" in source
    assert "news_future = pool.submit" in source
    assert "market_future = pool.submit" in source
    assert '"provider_start_policy_changed": False' in source


def test_weekly_topdown_hides_only_local_nondependent_stages() -> None:
    source = (ROOT / "src/v182/reporting/weekly_enrichment_fast_v21_16.py").read_text(encoding="utf-8")
    assert 'pending["rotation"] = pool.submit(original_rotation' in source
    assert 'pending["action_enhancements"] = pool.submit(original_enhancements' in source
    assert '"application_order_changed": False' in source
    assert '"relevant_input_fields_changed": False' in source
    assert "provider_start_policy_changed" in source


def test_friday_does_not_rescore_daily_tactical_after_weekly_committee() -> None:
    workflow = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    bundle = (ROOT / "src/v182/reporting/weekly_full_bundle_v21_16.py").read_text(encoding="utf-8")
    reuse = (ROOT / "src/v182/reporting/friday_tactical_reuse_v21_16.py").read_text(encoding="utf-8")
    assert "python -m v182.reporting.daily_tct_ct_runner" not in workflow
    assert "friday_tactical_reuse" in bundle
    assert '"rescoring_performed": False' in reuse
    assert '"network_calls": 0' in reuse
