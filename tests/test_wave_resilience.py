import numpy as np
import pandas as pd


def test_wave5_missing_yahoo_consensus_is_not_invented():
    from v182.reporting.waves import _rating_from_yf
    row = pd.Series({"recommendation_key_yf": np.nan, "recommendation_mean_yf": np.nan})
    assert _rating_from_yf(row) == (None, None)


def test_deterministic_yahoo_candidate_only_repairs_bare_supported_symbols():
    from v182.sources.history_orchestrator import _deterministic_yahoo_candidate

    assert _deterministic_yahoo_candidate("ABC", "IT0005466294") == "ABC.MI"
    assert _deterministic_yahoo_candidate("MLAIG", "ES0105744009") == "MLAIG.MC"
    assert _deterministic_yahoo_candidate("OBSRV", "NO0013457952") == "OBSRV.OL"
    assert _deterministic_yahoo_candidate("BKT.MC", "ES0113679I37") == ""
    assert _deterministic_yahoo_candidate("UNKNOWN", "CY0000000000") == ""


def test_yfinance_serial_rescue_recovers_after_threaded_failure(monkeypatch, tmp_path):
    import yfinance as yf
    from v182.sources.yfinance_bulk import download_history

    calls = []

    def fake_download(*, tickers, period, interval, group_by, auto_adjust, threads, progress, timeout):
        calls.append(bool(threads))
        if threads:
            raise RuntimeError("simulated Yahoo bulk throttle")
        index = pd.date_range("2026-01-01", periods=25, freq="D")
        return pd.DataFrame({
            "Open": np.arange(25, dtype=float) + 10,
            "High": np.arange(25, dtype=float) + 11,
            "Low": np.arange(25, dtype=float) + 9,
            "Close": np.arange(25, dtype=float) + 10.5,
            "Volume": np.arange(25, dtype=float) + 100,
        }, index=index)

    monkeypatch.setattr(yf, "download", fake_download)
    monkeypatch.setattr("v182.sources.yfinance_bulk.time.sleep", lambda _: None)

    result = download_history(
        ["TEST.PA"],
        str(tmp_path),
        batch_size=1,
        retry_count=0,
        batch_delay_seconds=0,
        serial_rescue_attempts=1,
        serial_rescue_backoff_seconds=0,
        serial_rescue_batch_size=1,
        serial_rescue_batch_delay_seconds=0,
        min_rows=20,
    )

    assert calls == [True, False]
    assert result.successful == ["TEST.PA"]
    assert result.failed == []
    assert result.diagnostics["serial_rescue_attempted_symbols"] == 1
    assert result.diagnostics["serial_rescue_successful_symbols"] == 1
