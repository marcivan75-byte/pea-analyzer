from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import math

import pandas as pd
import yfinance as yf

from v182.decision import ipo_outcomes_v1 as v1

ROOT = v1.ROOT
HISTORY_REL = v1.HISTORY_REL
OUTCOME_REL = v1.OUTCOME_REL
SUMMARY_REL = v1.SUMMARY_REL
CALIBRATION_REL = Path("outputs/ipo_radar/IPO_CALIBRATION_STATUS.json")


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _as_date(value: object) -> date | None:
    if value is None or str(value).strip().lower() in {"", "nan", "none", "n/a", "na"}:
        return None
    parsed = pd.to_datetime(str(value), errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed.date()


def _as_timestamp(value: object) -> pd.Timestamp | None:
    if value is None or str(value).strip().lower() in {"", "nan", "none", "n/a", "na"}:
        return None
    parsed = pd.to_datetime(str(value), errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _candidate_groups(history: pd.DataFrame, today: date) -> list[pd.DataFrame]:
    if history.empty or "identity_key" not in history.columns:
        return []
    data = history.copy()
    data["_expected_date"] = data.get("expected_date", pd.Series(index=data.index, dtype="object")).map(_as_date)
    data["_observed_ts"] = data.get("observed_at_utc", pd.Series(index=data.index, dtype="object")).map(_as_timestamp)
    data = data[data["_expected_date"].notna() & data["_observed_ts"].notna()]
    data = data[data["_expected_date"].map(lambda value: value < today)]
    groups: list[pd.DataFrame] = []
    for _, group in data.groupby("identity_key", dropna=False):
        if not group.empty:
            groups.append(group.sort_values("_observed_ts"))
    return groups


def _group_ticker(group: pd.DataFrame) -> str | None:
    for _, row in group.sort_values("_observed_ts", ascending=False).iterrows():
        ticker = v1.yahoo_ticker(row.get("symbol"), row.get("exchange"), row.get("euronext_location"))
        if ticker:
            return ticker
    return None


def _fetch_candidate_prices(ticker: str, group: pd.DataFrame, today: date) -> pd.DataFrame:
    expected = [value for value in group["_expected_date"].tolist() if isinstance(value, date)]
    if not expected:
        return pd.DataFrame()
    start = min(expected) - timedelta(days=7)
    end = min(today + timedelta(days=1), max(expected) + timedelta(days=150))
    if end <= start:
        return pd.DataFrame()
    return yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False, actions=False)


def _first_trade_date(prices: pd.DataFrame) -> date | None:
    if prices.empty or "Close" not in prices.columns:
        return None
    close = prices["Close"].dropna()
    if close.empty:
        return None
    index = close.index[0]
    if hasattr(index, "date"):
        return index.date()
    return _as_date(index)


def _strict_prelisting_snapshot(group: pd.DataFrame, actual_first_trade_date: date) -> dict | None:
    """Select evidence strictly before the first trading calendar date.

    Same-day observations are excluded because the history does not prove that they
    were captured before the opening print; this intentionally sacrifices some
    coverage to preserve anti-look-ahead governance.
    """
    eligible = group[group["_observed_ts"].map(lambda value: value.date() < actual_first_trade_date)]
    if eligible.empty:
        return None
    return eligible.sort_values("_observed_ts").iloc[-1].to_dict()


def _offer_price(snapshot: dict) -> tuple[float | None, str]:
    prospectus_price = _as_float(snapshot.get("sec_ipo_price"))
    if prospectus_price is not None and prospectus_price > 0:
        return prospectus_price, "SEC_PROSPECTUS_IPO_PRICE"
    midpoint = _as_float(snapshot.get("price_mid"))
    if midpoint is not None and midpoint > 0:
        return midpoint, "PRELISTING_PRICE_RANGE_MIDPOINT"
    return None, "UNAVAILABLE"


def _window_metrics(close: pd.Series, first: float, offer_price: float | None, label: str, index: int) -> dict:
    result: dict[str, object] = {
        f"{label}_close": None,
        f"ret_{label}_from_first_close_pct": None,
        f"ret_{label}_from_offer_pct": None,
        f"max_drawdown_{label}_from_first_close_pct": None,
        f"max_gain_{label}_from_first_close_pct": None,
        f"max_drawdown_{label}_from_offer_pct": None,
        f"max_gain_{label}_from_offer_pct": None,
    }
    if len(close) <= index:
        return result
    window = close.iloc[: index + 1].astype(float)
    price = float(window.iloc[-1])
    result[f"{label}_close"] = price
    result[f"ret_{label}_from_first_close_pct"] = round((price / first - 1.0) * 100.0, 2)
    result[f"max_drawdown_{label}_from_first_close_pct"] = round(float((window / first - 1.0).min() * 100.0), 2)
    result[f"max_gain_{label}_from_first_close_pct"] = round(float((window / first - 1.0).max() * 100.0), 2)
    if offer_price is not None and offer_price > 0:
        result[f"ret_{label}_from_offer_pct"] = round((price / offer_price - 1.0) * 100.0, 2)
        result[f"max_drawdown_{label}_from_offer_pct"] = round(float((window / offer_price - 1.0).min() * 100.0), 2)
        result[f"max_gain_{label}_from_offer_pct"] = round(float((window / offer_price - 1.0).max() * 100.0), 2)
    return result


def price_metrics_v1_2(prices: pd.DataFrame, offer_price: float | None) -> dict:
    if prices.empty or "Close" not in prices.columns:
        return {}
    close = prices["Close"].dropna()
    if close.empty:
        return {}
    first = float(close.iloc[0])
    first_date = _first_trade_date(prices)
    result: dict[str, object] = {
        "actual_first_trade_date": first_date.isoformat() if first_date else None,
        "first_close": first,
        "ret_first_close_vs_offer_pct": None if not offer_price or offer_price <= 0 else round((first / offer_price - 1.0) * 100.0, 2),
    }
    for label, index in (("d5", 4), ("d20", 19), ("d60", 59)):
        result.update(_window_metrics(close, first, offer_price, label, index))
    return result


def _merge_outcomes(existing: pd.DataFrame, records: list[dict]) -> pd.DataFrame:
    incoming = pd.DataFrame(records)
    if incoming.empty:
        return existing
    if existing.empty or "identity_key" not in existing.columns:
        return incoming
    combined = pd.concat([existing, incoming], ignore_index=True, sort=False)
    combined["_updated"] = combined.get("updated_at_utc", pd.Series(index=combined.index, dtype="object")).map(_as_timestamp)
    combined = combined.sort_values("_updated").groupby("identity_key", dropna=False).tail(1)
    return combined.drop(columns=["_updated"], errors="ignore").reset_index(drop=True)


def _bucket_stats(frame: pd.DataFrame, return_col: str) -> dict[str, dict]:
    scores = _numeric_series(frame, "net_ipo_score_pre_listing")
    returns = _numeric_series(frame, return_col)
    sample = pd.DataFrame({"score": scores, "return": returns}).dropna()
    if sample.empty:
        return {}
    buckets = (
        ("LT55", -float("inf"), 55.0),
        ("55_64_99", 55.0, 65.0),
        ("65_74_99", 65.0, 75.0),
        ("GE75", 75.0, float("inf")),
    )
    output: dict[str, dict] = {}
    for name, low, high in buckets:
        group = sample[(sample["score"] >= low) & (sample["score"] < high)]
        if group.empty:
            continue
        values = group["return"].astype(float)
        output[name] = {
            "n": int(len(group)),
            "positive_rate_pct": round(float((values > 0).mean() * 100.0), 2),
            "average_return_pct": round(float(values.mean()), 2),
            "median_return_pct": round(float(values.median()), 2),
        }
    return output


def _decision_stats(frame: pd.DataFrame, return_col: str) -> dict[str, dict]:
    returns = _numeric_series(frame, return_col)
    sample = frame.assign(_return=returns)
    sample = sample[sample["_return"].notna()] if "decision_pre_listing" in sample.columns else pd.DataFrame()
    output: dict[str, dict] = {}
    for decision, group in sample.groupby("decision_pre_listing"):
        values = group["_return"].astype(float)
        output[str(decision)] = {
            "n": int(len(group)),
            "positive_rate_pct": round(float((values > 0).mean() * 100.0), 2),
            "average_return_pct": round(float(values.mean()), 2),
            "median_return_pct": round(float(values.median()), 2),
        }
    return output


def _spearman_without_scipy(scores: pd.Series, returns: pd.Series) -> float | None:
    sample = pd.DataFrame({"score": scores, "return": returns}).dropna()
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
    d20_offer = _numeric_series(outcomes, "ret_d20_from_offer_pct")
    d20_first = _numeric_series(outcomes, "ret_d20_from_first_close_pct")
    d60_offer = _numeric_series(outcomes, "ret_d60_from_offer_pct")
    d60_first = _numeric_series(outcomes, "ret_d60_from_first_close_pct")
    d20 = d20_offer.where(d20_offer.notna(), d20_first)
    d60 = d60_offer.where(d60_offer.notna(), d60_first)
    scores = _numeric_series(outcomes, "net_ipo_score_pre_listing")
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
        "by_prelisting_decision_d20": _decision_stats(outcomes.assign(_calibration_return=d20), "_calibration_return"),
        "score_buckets_d20": _bucket_stats(outcomes.assign(_calibration_return=d20), "_calibration_return"),
        "score_buckets_d60": _bucket_stats(outcomes.assign(_calibration_return=d60), "_calibration_return"),
        "score_spearman_d20": spearman,
        "promotion_minimum_observation_target": 50,
        "calibration_status": "OBSERVATION_TARGET_MET_REQUIRES_DEDICATED_PIT_OOS" if d20_count >= 50 else "INSUFFICIENT_MATURED_SAMPLE",
        "promotion_ready": False,
        "reason": "Calibration is observational evidence only; no reweighting or promotion without a dedicated PIT/OOS audit.",
    }


def run(root: Path = ROOT) -> dict:
    history_path = root / HISTORY_REL
    outcome_path = root / OUTCOME_REL
    summary_path = root / SUMMARY_REL
    calibration_path = root / CALIBRATION_REL
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    if not history_path.exists():
        summary = validation_summary_v1_2(pd.DataFrame(), generated_at)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        calibration_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    history = pd.read_csv(history_path, dtype=str, low_memory=False)
    existing = pd.read_csv(outcome_path, dtype=str, low_memory=False) if outcome_path.exists() else pd.DataFrame()
    today = datetime.now(timezone.utc).date()
    records: list[dict] = []
    for group in _candidate_groups(history, today):
        ticker = _group_ticker(group)
        if not ticker:
            continue
        try:
            prices = _fetch_candidate_prices(ticker, group, today)
        except Exception:
            continue
        actual_first_trade_date = _first_trade_date(prices)
        if actual_first_trade_date is None or actual_first_trade_date >= today:
            continue
        snapshot = _strict_prelisting_snapshot(group, actual_first_trade_date)
        if snapshot is None:
            continue
        offer_price, offer_source = _offer_price(snapshot)
        metrics = price_metrics_v1_2(prices, offer_price)
        if not metrics:
            continue
        records.append(
            {
                "identity_key": snapshot.get("identity_key"),
                "candidate_id": snapshot.get("candidate_id"),
                "name": snapshot.get("name"),
                "symbol": snapshot.get("symbol"),
                "yahoo_ticker": ticker,
                "expected_listing_date_at_snapshot": snapshot.get("expected_date"),
                "actual_first_trade_date": metrics.get("actual_first_trade_date"),
                "prelisting_snapshot_at_utc": snapshot.get("observed_at_utc"),
                "decision_pre_listing": snapshot.get("decision"),
                "opportunity_score_pre_listing": snapshot.get("opportunity_score"),
                "risk_score_pre_listing": snapshot.get("risk_score"),
                "net_ipo_score_pre_listing": snapshot.get("net_ipo_score"),
                "market_readiness_pre_listing": snapshot.get("market_readiness_score"),
                "opportunity_coverage_pre_listing": snapshot.get("opportunity_coverage_pct"),
                "risk_coverage_pre_listing": snapshot.get("risk_coverage_pct"),
                "offer_price_reference": offer_price,
                "offer_price_source": offer_source,
                "offer_mid_legacy": snapshot.get("price_mid"),
                **{key: value for key, value in metrics.items() if key != "actual_first_trade_date"},
                "pit_safe": True,
                "updated_at_utc": generated_at,
            }
        )

    outcomes = _merge_outcomes(existing, records)
    if not outcomes.empty:
        outcomes.to_csv(outcome_path, index=False)
    summary = validation_summary_v1_2(outcomes, generated_at)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    calibration_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
