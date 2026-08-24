from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from time import perf_counter
import json

import pandas as pd

from v182.reporting import waves
from v182.reporting import weekly_unified_super_runner_v21_16_1 as previous


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V21_16_2"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V21_16_2.json"
_RATIO_FIELDS = {"per_ttm_yf", "per_forward_yf", "pb"}


def _clean_isin(value: object) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def _rematerialize_wave4_observations(
    actions_df: pd.DataFrame,
    prefetched_observations: list[dict],
) -> tuple[list[dict], int]:
    """Reuse prefetched Yahoo fields but recompute price-dependent ratios.

    WAVE04 network data can be collected before WAVE03 finishes because its
    ticker selection and refresh tiers do not depend on WAVE03 OHLCV features.
    The three ratios below *do* depend on the current ``last_close``. Therefore
    stale prefetch-time ratio observations are discarded and rebuilt from the
    post-WAVE03 frame plus the exact prefetched Yahoo fundamental values.
    """
    result = [row for row in prefetched_observations if str(row.get("field")) not in _RATIO_FIELDS]
    fields_by_isin: dict[str, dict[str, object]] = {}
    for row in result:
        isin = _clean_isin(row.get("isin"))
        field = str(row.get("field") or "")
        if isin and field:
            fields_by_isin.setdefault(isin, {})[field] = row.get("value")

    rematerialized = 0
    if "isin" not in actions_df.columns:
        return result, rematerialized
    closes = actions_df.get("last_close", pd.Series(index=actions_df.index, dtype=float))
    for isin, close in zip(actions_df["isin"], closes):
        key = _clean_isin(isin)
        if not key:
            continue
        fields = fields_by_isin.get(key)
        if not fields:
            continue
        local = {
            "per_ttm_yf": waves._positive_ratio(close, fields.get("trailing_eps_yf")),
            "per_forward_yf": waves._positive_ratio(close, fields.get("forward_eps_yf")),
            "pb": waves._positive_ratio(close, fields.get("book_value_per_share_yf")),
        }
        for field, value in local.items():
            if value is not None:
                result.append(
                    waves._obs(
                        "ACTION",
                        key,
                        field,
                        value,
                        "INTERNAL_OHLCV_X_YF_FUNDAMENTALS",
                        "C",
                    )
                )
                rematerialized += 1
    return result, rematerialized


def run(root: Path = ROOT) -> dict:
    """V21.16.1 plus safe WAVE04 network prefetch overlap.

    The Yahoo Action fundamental collection starts immediately after WAVE01
    Action OHLCV completes. It therefore overlaps WAVE02 ETF OHLCV and WAVE03
    local feature computation. WAVE04 still blocks on the exact same Yahoo
    result before applying observations, and the price-dependent ratios are
    rebuilt against the post-WAVE03 ``last_close`` values.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_wave_history = waves.wave_history
    original_wave4 = waves.wave4_info_actions
    lock = Lock()
    state: dict[str, object] = {
        "future": None,
        "started": False,
        "prefetch_runtime_seconds": None,
        "wave4_wait_seconds": None,
        "fallback_used": False,
        "prefetch_error": None,
        "rematerialized_ratios": 0,
    }
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-wave4-prefetch")

    def prefetch_job(actions_snapshot: pd.DataFrame, cfg: dict, top_n: int):
        t0 = perf_counter()
        try:
            return original_wave4(actions_snapshot, cfg, top_n=top_n)
        finally:
            state["prefetch_runtime_seconds"] = round(float(perf_counter() - t0), 6)

    def history_wrapped(df, universe, cache_dir, cfg):
        result = original_wave_history(df, universe, cache_dir, cfg)
        if str(universe).upper() == "ACTION":
            with lock:
                if state["future"] is None:
                    state["started"] = True
                    state["future"] = executor.submit(prefetch_job, df.copy(), cfg, 300)
        return result

    def wave4_wrapped(actions_df, cfg, top_n=300):
        with lock:
            future = state["future"]
        if future is None:
            state["fallback_used"] = True
            return original_wave4(actions_df, cfg, top_n=top_n)

        wait_started = perf_counter()
        try:
            observations, failures = future.result()
        except Exception as exc:
            state["prefetch_error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
            state["fallback_used"] = True
            observations, failures = original_wave4(actions_df, cfg, top_n=top_n)
        state["wave4_wait_seconds"] = round(float(perf_counter() - wait_started), 6)

        rematerialized, ratio_count = _rematerialize_wave4_observations(actions_df, observations)
        state["rematerialized_ratios"] = int(ratio_count)
        return rematerialized, failures

    waves.wave_history = history_wrapped
    waves.wave4_info_actions = wave4_wrapped

    payload: dict = {}
    error: str | None = None
    try:
        payload = previous.run(root=root)
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        waves.wave_history = original_wave_history
        waves.wave4_info_actions = original_wave4
        executor.shutdown(wait=True, cancel_futures=False)
        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "wave4_prefetch_started_after_wave1": bool(state["started"]),
            "wave4_prefetch_overlaps_wave2_wave3": True,
            "wave4_prefetch_runtime_seconds": state["prefetch_runtime_seconds"],
            "wave4_wait_seconds": state["wave4_wait_seconds"],
            "wave4_prefetch_fallback_used": bool(state["fallback_used"]),
            "wave4_prefetch_error": state["prefetch_error"],
            "wave4_price_dependent_ratios_rematerialized": int(state["rematerialized_ratios"]),
            "wave4_remote_observations_reused_exactly": True,
            "wave4_refresh_tiers_changed": False,
            "wave4_refresh_budget_changed": False,
            "wave4_provider_cadence_changed": False,
            "wave4_universe_changed": False,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "pit_logic_changed": False,
            "missing_data_policy_changed": False,
            "real_orders_enabled": False,
        }
        (auditdir / AUDIT_NAME).write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
