from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAVE_GATE = "if: ${{ always() && hashFiles('state/OHLCV_CACHE_VALIDATED') != '' }}"


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _assert_validated_checkpoint(workflow: str, collection_step: str) -> None:
    assert "- name: Validate OHLCV cache checkpoint" in workflow
    assert "rm -f state/OHLCV_CACHE_VALIDATED" in workflow
    assert "outputs/audit/V18.2_QUALITY_GATES.json" in workflow
    assert "grep -Eq '\"passed\"[[:space:]]*:[[:space:]]*true'" in workflow
    assert "[ -f data/cache/actions/history_manifest.json ]" in workflow
    assert "[ -f data/cache/etf/history_manifest.json ]" in workflow
    assert "printf 'validated\\n' > state/OHLCV_CACHE_VALIDATED" in workflow
    assert SAVE_GATE in workflow
    assert "if: ${{ success() && hashFiles('data/cache/**') != '' }}" not in workflow
    assert workflow.index(collection_step) < workflow.index("Validate OHLCV cache checkpoint")
    assert workflow.index("Validate OHLCV cache checkpoint") < workflow.index("Save persistent OHLCV cache")


def test_daily_ohlcv_cache_survives_downstream_failure_only_after_validated_collection() -> None:
    _assert_validated_checkpoint(
        _workflow("committee_tct_ct_daily.yml"),
        "Daily consolidated optimized V21.15.7",
    )


def test_weekly_ohlcv_cache_survives_downstream_failure_only_after_validated_collection() -> None:
    _assert_validated_checkpoint(
        _workflow("committee_master_daily.yml"),
        "Run optimized weekly unified Committee DAG V21.16.2",
    )
