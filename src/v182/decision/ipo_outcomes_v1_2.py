from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.decision import ipo_outcomes_v1 as v1

ROOT = v1.ROOT
_BASE_VALIDATION_SUMMARY = v1._validation_summary


def _as_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def price_metrics_v1_2(prices: pd.DataFrame, offer_mid: float | None) -> dict:
    if prices.empty or "Close" not in prices.columns:
        return {}
    close = prices["Close"].dropna()
    if close.empty:
        return {}
    first = float(close.iloc[0])
    first_index = close.index[0]
    actual_first_trade_date = first_index.date().isoformat() if hasattr(first_index, "date") else str(first_index)[:10]
    result: dict[str, object] = {
        "actual_first_trade_date": actual_first_trade_date,
        "first_close": first,
        "ret_first_close_vs_offer_pct": None if not offer_mid or offer_mid <= 0 else round((first / offer_mid - 1.0) * 100.0, 2),
    }
    for label, index in (("d5", 4), ("d20", 19), ("d60", 59)):
        if len(close) <= index:
            result[f"{label}_close"] = None
            result[f"ret_{label}_from_first_close_pct"] = None
            result[f"ret_{label}_from_offer_pct"] = None
            result[f"max_drawdown_{label}_from_first_close_pct"] = None
            result[f"max_gain_{label}_from_first_close_pct"] = None
            continue
        window = close.iloc[: index + 1].astype(float)
        price = float(window.iloc[-1])
        result[f"{label}_close"] = price
        result[f"ret_{label}_from_first_close_pct"] = round((price / first - 1.0) * 100.0, 2)
        result[f"ret_{label}_from_offer_pct"] = None if not offer_mid or offer_mid <= 0 else round((price / offer_mid - 1.0) * 100.0, 2)
        result[f"max_drawdown_{label}_from_first_close_pct"] = round(float((window / first - 1.0).min() * 100.0), 2)
        result[f"max_gain_{label}_from_first_close_pct"] = round(float((window / first - 1.0).max() * 100.0), 2)
    return result


def _bucket_stats(frame: pd.DataFrame, return_col: str) -> dict[str, dict]:
    if frame.empty:
        return {}
    scores = pd.to_numeric(frame.get("net_ipo_score_pre_listing"), errors="coerce")
    returns = pd.to_numeric(frame.get(return_col), errors="coerce")
    sample = pd.DataFrame({"score": scores, "return": returns}).dropna()
    if sample.empty:
        return {}
    buckets = (
        ("LT55", -float("inf"), 55.0),
        ("55_64_99", 55.0, 65.0),
        ("65_74_99", 65.0, 75.0),
        ("GE75", 75.0, float("inf")),
    )
    result: dict[str, dict] = {}
    for label, low, high in buckets:
        group = sample[(sample["score"] >= low) & (sample["score"] < high)]
        if group.empty:
            continue
        values = group["return"].astype(float)
        result[label] = {
            "n": int(len(group)),
            "positive_rate_pct": round(float((values > 0).mean() * 100.0), 2),
            "average_return_pct": round(float(values.mean()), 2),
            "median_return_pct": round(float(values.median()), 2),
        }
    return result


def validation_summary_v1_2(outcomes: pd.DataFrame, generated_at: str) -> dict:
    summary = _BASE_VALIDATION_SUMMARY(outcomes, generated_at)
    if outcomes.empty:
        summary.update(
            {
                "validation_layer": "IPO_OUTCOMES_V1.2",
                "d60_sample_count": 0,
                "score_spearman_d20": None,
                "score_buckets_d20": {},
                "score_buckets_d60": {},
                "calibration_status": "INSUFFICIENT_MATURED_SAMPLE",
            }
        )
        return summary

    d20 = pd.to_numeric(outcomes.get("ret_d20_from_offer_pct"), errors="coerce")
    if d20.isna().all():
        d20 = pd.to_numeric(outcomes.get("ret_d20_from_first_close_pct"), errors="coerce")
    d60 = pd.to_numeric(outcomes.get("ret_d60_from_offer_pct"), errors="coerce")
    if d60.isna().all():
        d60 = pd.to_numeric(outcomes.get("ret_d60_from_first_close_pct"), errors="coerce")
    scores = pd.to_numeric(outcomes.get("net_ipo_score_pre_listing"), errors="coerce")
    corr_frame = pd.DataFrame({"score": scores, "ret": d20}).dropna()
    spearman = None
    if len(corr_frame) >= 10:
        correlation = corr_frame["score"].corr(corr_frame["ret"], method="spearman")
        if pd.notna(correlation):
            spearman = round(float(correlation), 4)

    d20_count = int(d20.notna().sum())
    d60_count = int(d60.notna().sum())
    calibration_status = "OBSERVATION_TARGET_MET_REQUIRES_DEDICATED_PIT_OOS" if d20_count >= 50 else "INSUFFICIENT_MATURED_SAMPLE"
    summary.update(
        {
            "validation_layer": "IPO_OUTCOMES_V1.2",
            "return_reference_priority": "IPO_OFFER_PRICE_THEN_FIRST_CLOSE_FALLBACK",
            "d20_sample_count_offer_aware": d20_count,
            "d60_sample_count": d60_count,
            "score_spearman_d20": spearman,
            "score_buckets_d20": _bucket_stats(outcomes.assign(_v12_return=d20), "_v12_return"),
            "score_buckets_d60": _bucket_stats(outcomes.assign(_v12_return=d60), "_v12_return"),
            "calibration_status": calibration_status,
            "promotion_ready": False,
            "promotion_policy": "NO_REWEIGHTING_OR_PROMOTION_FROM_OBSERVATIONAL_BUCKETS_ALONE_DEDICATED_PIT_OOS_REQUIRED",
        }
    )
    return summary


def install_v1_2() -> None:
    v1._price_metrics = price_metrics_v1_2
    v1._validation_summary = validation_summary_v1_2


def run(root: Path = ROOT) -> dict:
    install_v1_2()
    summary = v1.run(root)
    output_path = root / "outputs" / "ipo_radar" / "IPO_CALIBRATION_STATUS.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary["calibration_output"] = "outputs/ipo_radar/IPO_CALIBRATION_STATUS.json"
    validation_path = root / "outputs" / "ipo_radar" / "IPO_VALIDATION_STATUS.json"
    validation_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
