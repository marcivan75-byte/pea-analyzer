from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_daily_and_weekly_share_same_persistent_ohlcv_cache_namespace():
    daily = _workflow("committee_tct_ct_daily.yml")
    weekly = _workflow("committee_master_daily.yml")
    for source in (daily, weekly):
        assert "Restore persistent OHLCV cache" in source
        assert "Save persistent OHLCV cache" in source
        assert "path: data/cache/" in source
        assert "ohlcv-v3-" in source


def test_daily_artifact_keeps_only_cache_manifests_not_full_ohlcv_payload():
    daily = _workflow("committee_tct_ct_daily.yml")
    upload = daily.split("Upload compact daily tactical and CI artifact", 1)[1]
    assert "data/cache/actions/history_manifest.json" in upload
    assert "data/cache/etf/history_manifest.json" in upload
    assert "data/cache/\n" not in upload
