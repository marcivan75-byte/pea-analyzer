import numpy as np
import pandas as pd

from v182.backtest.sector_rotation_v2_backtest import evaluate_signals, threshold_study
from v182.reporting.sector_rotation_v2_compare import compare_v1_v2


def test_compare_v1_v2_preserves_baseline_and_reports_rank_changes():
    v1 = pd.DataFrame(
        {
            "sector": ["A", "B", "C"],
            "sector_rotation_score": [80.0, 60.0, 40.0],
        }
    )
    v2 = pd.DataFrame(
        {
            "sector": ["A", "B", "C"],
            "RARS": [55.0, 85.0, 45.0],
            "RLS": [70.0, 90.0, 50.0],
            "rank": [2, 1, 3],
            "warnings": [[], ["PROMISING_BUT_OVERVALUED"], []],
        }
    )
    out, summary = compare_v1_v2(v1, v2)
    assert summary["status"] == "OK"
    assert summary["matched_sectors"] == 3
    assert "B" in summary["promising_but_overvalued"]
    row_b = out.set_index("sector").loc["B"]
    assert row_b["v1_rank"] == 2
    assert row_b["rank"] == 1
    assert row_b["rank_delta_v2_minus_v1"] == -1


def _prices(sector: str, start: float, daily_step: float, n: int = 300):
    dates = pd.bdate_range("2025-01-02", periods=n, tz="UTC")
    return pd.DataFrame(
        {
            "sector": sector,
            "date": dates,
            "price": start + np.arange(n) * daily_step,
        }
    )


def test_pit_evaluator_uses_future_prices_only_after_signal_date():
    signals = pd.DataFrame(
        [
            {
                "sector": "A",
                "as_of": "2025-02-03",
                "model_version": "V2",
                "RLS": 82,
                "RARS": 75,
                "AVCR": 30,
                "DQS": 90,
                "state": "EARLY_ROTATION",
                "warnings": [],
                "correction_alert": False,
            },
            {
                "sector": "B",
                "as_of": "2025-02-03",
                "model_version": "V2",
                "RLS": 85,
                "RARS": 58,
                "AVCR": 80,
                "DQS": 90,
                "state": "LEADERSHIP",
                "warnings": ["PROMISING_BUT_OVERVALUED"],
                "correction_alert": False,
            },
        ]
    )
    prices = pd.concat([_prices("A", 100, 0.2), _prices("B", 100, -0.1)], ignore_index=True)
    benchmark = _prices("MARKET", 100, 0.05)
    result = evaluate_signals(signals, prices, benchmark_prices=benchmark, horizons=(5, 20, 60))
    assert result.summary["status"] == "OK"
    assert result.summary["matched_rows"] == 2
    obs = result.observations.set_index("sector")
    assert obs.loc["A", "forward_return_pct_20d"] > 0
    assert obs.loc["B", "forward_return_pct_20d"] < 0
    assert obs.loc["A", "excess_return_pct_20d"] > 0
    assert result.summary["warning_study"]["promising_but_overvalued"]["flagged_n"] == 1


def test_threshold_study_measures_missed_upside_and_drawdown_tradeoff():
    observations = pd.DataFrame(
        {
            "AVCR": [40, 62, 70, 78, 90],
            "forward_return_pct_60d": [12, 9, 4, -8, -15],
            "mae_pct_60d": [-3, -4, -6, -14, -22],
        }
    )
    study = threshold_study(observations, avcr_thresholds=(65, 75, 85), horizon=60)
    assert len(study) == 3
    assert study.loc[study["AVCR_threshold"].eq(75), "flagged_n"].iloc[0] == 2
    assert study["missed_upside_pct"].notna().all()


def test_evaluator_rejects_duplicate_pit_snapshots():
    signal = {
        "sector": "A",
        "as_of": "2025-02-03",
        "model_version": "V2",
    }
    signals = pd.DataFrame([signal, signal])
    prices = _prices("A", 100, 0.1)
    try:
        evaluate_signals(signals, prices, horizons=(5,))
    except ValueError as exc:
        assert "DUPLICATE_SIGNAL_SNAPSHOT" in str(exc)
    else:
        raise AssertionError("duplicate PIT snapshots must be rejected")
