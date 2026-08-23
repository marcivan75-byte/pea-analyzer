from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from v182.features.ohlcv_features import calculate as calculate_features
from v182.reporting import waves


VERSION = "WAVE3_CPU_BUDGET_V21_15_4"


def _action_jobs(frames: list[pd.DataFrame], ticker_isin_map: dict[str, str]) -> list[tuple[str, str, pd.DataFrame]]:
    jobs: list[tuple[str, str, pd.DataFrame]] = []
    for frame in frames:
        if not hasattr(frame.columns, "levels"):
            continue
        for ticker in frame.columns.get_level_values(0).unique():
            isin = ticker_isin_map.get(ticker)
            if isin is None:
                continue
            jobs.append((str(ticker), str(isin), frame[ticker]))
    return jobs


def _compute(job: tuple[str, str, pd.DataFrame]) -> tuple[str, str, dict]:
    ticker, isin, frame = job
    return ticker, isin, calculate_features(frame)


def _action_derived_parallel(
    frames: list[pd.DataFrame],
    ticker_isin_map: dict[str, str],
    *,
    workers: int = 2,
) -> list[dict]:
    """Exact wave3_derived_features semantics with ordered two-worker calculation."""
    jobs = _action_jobs(frames, ticker_isin_map)
    if not jobs:
        return []
    workers = max(1, min(2, int(workers), len(jobs)))
    if workers == 1:
        calculated = [_compute(job) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wave3-action") as pool:
            calculated = list(pool.map(_compute, jobs))

    per_ticker_perf_1y: dict[str, float] = {}
    per_ticker_perf_10d: dict[str, float] = {}
    per_ticker_indicators: dict[str, dict] = {}
    ordered_isins: list[str] = []
    for _ticker, isin, indicators in calculated:
        if not indicators:
            continue
        if isin not in per_ticker_indicators:
            ordered_isins.append(isin)
        per_ticker_indicators[isin] = indicators
        if indicators.get("perf_1y_pct") is not None:
            per_ticker_perf_1y[isin] = indicators["perf_1y_pct"]
        if indicators.get("perf_10d_pct") is not None:
            per_ticker_perf_10d[isin] = indicators["perf_10d_pct"]

    median_1y = pd.Series(per_ticker_perf_1y).median() if per_ticker_perf_1y else 0.0
    median_10d = pd.Series(per_ticker_perf_10d).median() if per_ticker_perf_10d else 0.0
    observations: list[dict] = []
    for isin in ordered_isins:
        indicators = per_ticker_indicators[isin]
        for field, value in indicators.items():
            if value is not None:
                observations.append(waves._obs("ACTION", isin, field, value, "INTERNAL_FROM_OHLCV", "C"))
        if indicators.get("perf_1y_pct") is not None:
            observations.append(
                waves._obs(
                    "ACTION",
                    isin,
                    "relative_strength",
                    round(indicators["perf_1y_pct"] - median_1y, 4),
                    "INTERNAL_FROM_OHLCV",
                    "C",
                )
            )
        if indicators.get("perf_10d_pct") is not None:
            observations.append(
                waves._obs(
                    "ACTION",
                    isin,
                    "relative_strength_10d",
                    round(indicators["perf_10d_pct"] - median_10d, 4),
                    "INTERNAL_FROM_OHLCV",
                    "C",
                )
            )
    return observations


def wave3_local_features(
    actions_cache_dir: str,
    actions_ticker_isin_map: dict[str, str],
    etf_cache_dir: str,
    etf_ticker_isin_map: dict[str, str],
    *,
    max_workers: int = 2,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Allocate the 2-vCPU budget to the dominant 1,829-Action compute stage.

    Parquet files are still read exactly once per universe. ETF (102 names) is
    completed first, then the much larger Action feature calculation gets both
    workers. Executor.map preserves input order. All feature functions and
    cross-sectional relative-strength formulas are unchanged.
    """
    etf_frames = waves._history_frames(etf_cache_dir)
    obs_etf = waves.wave3_derived_features(
        etf_cache_dir,
        etf_ticker_isin_map,
        "ETF",
        history_frames=etf_frames,
    )
    obs_beta = waves.wave3_etf_beta3y(
        etf_cache_dir,
        etf_ticker_isin_map,
        history_frames=etf_frames,
    )

    action_frames = waves._history_frames(actions_cache_dir)
    obs_actions = _action_derived_parallel(
        action_frames,
        actions_ticker_isin_map,
        workers=max_workers,
    )
    return obs_actions, obs_etf, obs_beta


def audit_contract() -> dict:
    return {
        "version": VERSION,
        "action_compute_workers_max": 2,
        "etf_rows_expected": 102,
        "action_rows_expected": 1829,
        "executor_map_order_preserved": True,
        "feature_formula_changed": False,
        "relative_strength_formula_changed": False,
        "parquet_read_count_increased": False,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }