from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/committee_master_daily.yml"


def test_ohlcv_cache_excludes_yfinance_and_quality_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ohlcv-v4-${{ github.run_id }}" in text
    assert "ohlcv-v3-" in text
    assert "data/cache/actions/" in text
    assert "data/cache/etf/" in text
    restore = text.split("- name: Restore persistent OHLCV cache", 1)[1].split("- name:", 1)[0]
    assert "data/cache/" not in restore.replace("data/cache/actions/", "").replace("data/cache/etf/", "")
    assert ".yfinance-runtime" not in restore
    checkpoint = text.split("- name: Validate OHLCV cache checkpoint", 1)[1].split("- name:", 1)[0]
    assert "V18.2_QUALITY_GATES.json" not in checkpoint
    assert "history_manifest.json" in checkpoint
    assert "OHLCV_CACHE_CHECKPOINT.json" in checkpoint
    assert "OHLCV_CACHE_RESTORE.json" in text
