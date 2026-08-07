import json
from pathlib import Path

import pandas as pd

from v182.audit.ohlcv_gaps import write_ohlcv_gap_audit


def test_release_version_matches_master_config():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    config = json.loads(Path("config/V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    assert 'version = "18.2.1"' in pyproject
    assert config["version"] == "18.2.1"


def test_readme_documents_marketbeat_and_seven_api_secrets():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "V18.2.1" in text
    assert "MARKETBEAT_API_KEY" in text
    assert "sept secrets API" in text
    assert "scenario_fallback" in text


def test_full_audit_is_durable_and_generates_exact_sha_evidence():
    text = Path(".github/workflows/V18.2_full_audit.yml").read_text(encoding="utf-8")
    assert "cancel-in-progress: false" in text
    assert "python -m v182.audit.ohlcv_gaps" in text
    assert "python -m v182.audit.release_evidence" in text
    assert 'V182_REQUIRE_RELEASE_READY: "1"' in text
    assert "V18.2_RELEASE_EVIDENCE.json" in text


def test_fast_ci_preserves_exact_sha_runs():
    text = Path(".github/workflows/marketbeat_integration_ci.yml").read_text(encoding="utf-8")
    assert "audit/v18-2-consensus-momentum-final" in text
    assert "cancel-in-progress: false" in text
    assert "python -m pip check" in text
    assert "python -m compileall -q src tests" in text
    assert "pytest -q" in text


def test_api_smoke_covers_all_seven_services_on_final_branch():
    text = Path(".github/workflows/V18.2_api_smoke.yml").read_text(encoding="utf-8")
    assert "audit/v18-2-consensus-momentum-final" in text
    assert "cancel-in-progress: false" in text
    for marker in (
        "OPENFIGI_SMOKE_OK",
        "MARKETSTACK_SMOKE_OK",
        "FINNHUB_SMOKE_OK",
        "ALPHA_VANTAGE_SMOKE_OK",
        "FRED_SMOKE_OK",
        "EIA_SMOKE_OK",
        "MARKETBEAT_SMOKE_OK",
    ):
        assert marker in text


def test_ohlcv_gap_audit_writes_actionable_lists(tmp_path):
    outputs = tmp_path / "outputs"
    (outputs / "audit").mkdir(parents=True)
    actions = pd.DataFrame([
        {"isin": "FR1", "name": "OK", "yahoo_ticker": "OK.PA", "last_close": "100", "score_brut": "50"},
        {"isin": "FR2", "name": "Gap", "yahoo_ticker": "GAP.PA", "last_close": "NON_OBSERVE", "score_brut": "90"},
    ])
    etf = pd.DataFrame([
        {"isin": "ETF1", "name": "ETF Gap", "yahoo_ticker": "ETF.PA", "last_close": ""},
    ])
    actions.to_csv(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")
    etf.to_csv(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")
    (outputs / "audit" / "V18.2_SOURCE_FALLBACK_METRICS.json").write_text(
        json.dumps({"wave01_actions": {"failed": 1}, "wave02_etf": {"failed": 1}}), encoding="utf-8"
    )

    metrics = write_ohlcv_gap_audit(tmp_path)
    action_gaps = pd.read_csv(outputs / "gaps" / "V18.2_OHLCV_ACTION_GAPS.csv", sep=";", encoding="utf-8-sig")

    assert metrics["actions_without_last_close"] == 1
    assert metrics["etf_without_last_close"] == 1
    assert action_gaps.iloc[0]["isin"] == "FR2"
    assert action_gaps.iloc[0]["gap_reason"] == "NO_USABLE_OHLCV_AFTER_FALLBACK_CHAIN"
