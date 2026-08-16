from __future__ import annotations

import pandas as pd

from v182.decision import ipo_outcomes_v1_2 as core


def _spearman_without_scipy(score: pd.Series, returns: pd.Series) -> float | None:
    sample = pd.DataFrame({"score": score, "return": returns}).dropna()
    if len(sample) < 10:
        return None
    score_rank = sample["score"].rank(method="average")
    return_rank = sample["return"].rank(method="average")
    value = score_rank.corr(return_rank)
    return None if pd.isna(value) else round(float(value), 4)


def validation_summary_v1_2(outcomes: pd.DataFrame, generated_at: str) -> dict:
    if outcomes.empty:
        return {
            "generated_at_utc": generated_at,
            "validation_layer": "IPO_OUTCOMES_V1.2_PIT_SAFE",
            "sample_count": 0,
            "d20_sample_count": 0,
            "d60_sample_count": 0,
            "promotion_ready": False,
            "calibration_status": "INSUFFICIENT_MATURED_SAMPLE",
            "reason": "No matured PIT-safe IPO outcomes yet",
        }
    d20_offer = core._numeric_series(outcomes, "ret_d20_from_offer_pct")
    d20_first = core._numeric_series(outcomes, "ret_d20_from_first_close_pct")
    d60_offer = core._numeric_series(outcomes, "ret_d60_from_offer_pct")
    d60_first = core._numeric_series(outcomes, "ret_d60_from_first_close_pct")
    d20 = d20_offer.where(d20_offer.notna(), d20_first)
    d60 = d60_offer.where(d60_offer.notna(), d60_first)
    scores = core._numeric_series(outcomes, "net_ipo_score_pre_listing")
    spearman = _spearman_without_scipy(scores, d20)
    d20_count = int(d20.notna().sum())
    d60_count = int(d60.notna().sum())
    return {
        "generated_at_utc": generated_at,
        "validation_layer": "IPO_OUTCOMES_V1.2_PIT_SAFE",
        "sample_count": int(len(outcomes)),
        "d20_sample_count": d20_count,
        "d60_sample_count": d60_count,
        "return_reference_priority": "PROSPECTUS_IPO_PRICE_THEN_PRELISTING_RANGE_MIDPOINT_THEN_FIRST_CLOSE",
        "pit_snapshot_policy": "STRICTLY_BEFORE_ACTUAL_FIRST_TRADING_DATE",
        "by_prelisting_decision_d20": core._decision_stats(outcomes.assign(_calibration_return=d20), "_calibration_return"),
        "score_buckets_d20": core._bucket_stats(outcomes.assign(_calibration_return=d20), "_calibration_return"),
        "score_buckets_d60": core._bucket_stats(outcomes.assign(_calibration_return=d60), "_calibration_return"),
        "score_spearman_d20": spearman,
        "promotion_minimum_observation_target": 50,
        "calibration_status": "OBSERVATION_TARGET_MET_REQUIRES_DEDICATED_PIT_OOS" if d20_count >= 50 else "INSUFFICIENT_MATURED_SAMPLE",
        "promotion_ready": False,
        "reason": "Calibration is observational evidence only; no reweighting or promotion without a dedicated PIT/OOS audit.",
    }


def install_stabilization() -> None:
    core.validation_summary_v1_2 = validation_summary_v1_2


def run(root=core.ROOT) -> dict:
    install_stabilization()
    return core.run(root)


def main() -> None:
    import json

    print(json.dumps(run(core.ROOT), ensure_ascii=False, indent=2, default=str))


# Make direct imports of this module deterministic for tests and the unified runner.
install_stabilization()

_as_date = core._as_date
_as_timestamp = core._as_timestamp
_strict_prelisting_snapshot = core._strict_prelisting_snapshot
_offer_price = core._offer_price
price_metrics_v1_2 = core.price_metrics_v1_2

if __name__ == "__main__":
    main()
