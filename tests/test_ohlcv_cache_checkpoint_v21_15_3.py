from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CACHE_GATE = (
    "if: ${{ always() && hashFiles('outputs/audit/V18.2_QUALITY_GATES.json') != '' "
    "&& hashFiles('data/cache/actions/history_manifest.json') != '' "
    "&& hashFiles('data/cache/etf/history_manifest.json') != '' }}"
)


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_daily_ohlcv_cache_survives_only_after_validated_collection() -> None:
    workflow = _workflow("committee_tct_ct_daily.yml")
    assert EXPECTED_CACHE_GATE in workflow
    assert "if: ${{ success() && hashFiles('data/cache/**') != '' }}" not in workflow
    assert workflow.index("Daily collection and enrichment") < workflow.index("Save persistent OHLCV cache")


def test_weekly_ohlcv_cache_survives_only_after_validated_collection() -> None:
    workflow = _workflow("committee_master_daily.yml")
    assert EXPECTED_CACHE_GATE in workflow
    assert "if: ${{ success() && hashFiles('data/cache/**') != '' }}" not in workflow
    assert workflow.index("Run weekly unified Committee pipeline") < workflow.index("Save persistent OHLCV cache")


def test_collection_quality_gate_is_written_before_pipeline_returns() -> None:
    source = (ROOT / "src" / "v182" / "reporting" / "run.py").read_text(encoding="utf-8")
    quality_write = source.index('OUTPUTS / "audit" / "V18.2_QUALITY_GATES.json"')
    success_return = source.index('return {"status":"SUCCESS"', quality_write)
    assert quality_write < success_return
