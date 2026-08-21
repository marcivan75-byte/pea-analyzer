from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import math

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[3]
HISTORY_REL = Path("state/ipo_radar/IPO_HISTORY.csv")
OUTCOME_REL = Path("state/ipo_radar/IPO_OUTCOMES.csv")
SUMMARY_REL = Path("outputs/ipo_radar/IPO_VALIDATION_STATUS.json")


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value))
        return None if math.isnan(number) else number
    except Exception:
        return None


def _as_date(value: object) -> date | None:
    if value is None or str(value).strip() in {"", "nan", "None"}:
        return None
    parsed = pd.to_datetime(str(value), errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def yahoo_ticker(symbol: object, exchange: object, location: object = None) -> str | None:
    symbol_text = "" if symbol is None else str(symbol).strip().upper()
    if not symbol_text:
        return None
    exchange_text = "" if exchange is None else str(exchange).upper()
    location_text = "" if location is None else str(location).upper()
    if "NASDAQ" in exchange_text or "NYSE" in exchange_text or exchange_text in {"AMEX", "NYSEAMERICAN"}:
        return symbol_text
    suffixes = {
        "PARIS": ".PA", "AMSTERDAM": ".AS", "BRUSSELS": ".BR", "MILAN": ".MI", "LISBON": ".LS",
        "OSLO": ".OL", "DUBLIN": ".IR",
    }
    suffix = suffixes.get(location_text)
    return f"{symbol_text}{suffix}" if suffix else None


def _last_prelisting_snapshots(history: pd.DataFrame, today: date) -> list[dict]:
    if history.empty or "identity_key" not in history.columns:
        return []
    data = history.copy()
    data["_listing_date"] = data["expected_date"].map(_as_date)
    data["_observed_date"] = data["observed_at_utc"].map(_as_date)
    data = data[data["_listing_date"].notna() & data["_observed_date"].notna()]
    data = data[data["_listing_date"].map(lambda value: value < today)]
    data = data[data.apply(lambda row: row["_observed_date"] <= row["_listing_date"], axis=1)]
    if data.empty:
        return []
    data = data.sort_values("observed_at_utc").groupby("identity_key", dropna=False).tail(1)
    return [row.to_dict() for _, row in data.iterrows()]


def _price_metrics(prices: pd.DataFrame, offer_mid: float | None) -> dict:
    if prices.empty or "Close" not in prices.columns:
        return {}
    close = prices["Close"].dropna()
    if close.empty:
        return {}
    first = float(close.iloc[0])
    result = {
        "first_close": first,
        "ret_first_close_vs_offer_pct": None if not offer_mid or offer_mid <= 0 else round((first / offer_mid - 1.0) * 100.0, 2),
    }
    for label, index in (("d5", 4), ("d20", 19), ("d60", 59)):
        if len(close) > index:
            price = float(close.iloc[index])
            result[f"{label}_close"] = price
            result[f"ret_{label}_from_first_close_pct"] = round((price / first - 1.0) * 100.0, 2)
        else:
            result[f"{label}_close"] = None
            result[f"ret_{label}_from_first_close_pct"] = None
    return result


def _fetch_prices(ticker: str, listing_date: date, today: date) -> pd.DataFrame:
    end = min(today + timedelta(days=1), listing_date + timedelta(days=120))
    return yf.Ticker(ticker).history(start=listing_date.isoformat(), end=end.isoformat(), auto_adjust=False, actions=False)


def _merge_outcomes(existing: pd.DataFrame, records: list[dict]) -> pd.DataFrame:
    incoming = pd.DataFrame(records)
    if incoming.empty:
        return existing
    if existing.empty or "identity_key" not in existing.columns:
        return incoming
    combined = pd.concat([existing, incoming], ignore_index=True)
    combined = combined.sort_values("updated_at_utc").groupby("identity_key", dropna=False).tail(1)
    return combined.reset_index(drop=True)


def _validation_summary(outcomes: pd.DataFrame, generated_at: str) -> dict:
    sample = outcomes.copy()
    if sample.empty:
        return {
            "generated_at_utc": generated_at,
            "sample_count": 0,
            "d20_sample_count": 0,
            "promotion_ready": False,
            "reason": "No matured IPO outcomes yet",
        }
    sample["ret_d20_from_first_close_pct"] = pd.to_numeric(sample.get("ret_d20_from_first_close_pct"), errors="coerce")
    matured = sample[sample["ret_d20_from_first_close_pct"].notna()]
    by_decision: dict[str, dict] = {}
    for decision, group in matured.groupby("decision_pre_listing"):
        returns = group["ret_d20_from_first_close_pct"].astype(float)
        by_decision[str(decision)] = {
            "n": int(len(group)),
            "positive_rate_pct": round(float((returns > 0).mean() * 100.0), 2),
            "average_return_pct": round(float(returns.mean()), 2),
            "median_return_pct": round(float(returns.median()), 2),
        }
    n = int(len(matured))
    return {
        "generated_at_utc": generated_at,
        "sample_count": int(len(sample)),
        "d20_sample_count": n,
        "by_prelisting_decision": by_decision,
        "promotion_ready": False,
        "promotion_minimum_observation_target": 50,
        "reason": "Shadow outcomes are evidence only; promotion still requires a dedicated PIT/OOS audit even after the observation target is reached.",
    }


def run(root: Path = ROOT) -> dict:
    history_path = root / HISTORY_REL
    outcome_path = root / OUTCOME_REL
    summary_path = root / SUMMARY_REL
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    if not history_path.exists():
        summary = _validation_summary(pd.DataFrame(), generated_at)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    history = pd.read_csv(history_path, dtype=str, low_memory=False)
    existing = pd.read_csv(outcome_path, dtype=str, low_memory=False) if outcome_path.exists() else pd.DataFrame()
    today = datetime.now(timezone.utc).date()
    records: list[dict] = []
    for snapshot in _last_prelisting_snapshots(history, today):
        listing_date = _as_date(snapshot.get("expected_date"))
        if listing_date is None:
            continue
        ticker = yahoo_ticker(snapshot.get("symbol"), snapshot.get("exchange"), snapshot.get("euronext_location"))
        if not ticker:
            continue
        try:
            prices = _fetch_prices(ticker, listing_date, today)
        except Exception:
            continue
        metrics = _price_metrics(prices, _as_float(snapshot.get("price_mid")))
        if not metrics:
            continue
        records.append(
            {
                "identity_key": snapshot.get("identity_key"),
                "candidate_id": snapshot.get("candidate_id"),
                "name": snapshot.get("name"),
                "symbol": snapshot.get("symbol"),
                "yahoo_ticker": ticker,
                "listing_date": listing_date.isoformat(),
                "decision_pre_listing": snapshot.get("decision"),
                "opportunity_score_pre_listing": snapshot.get("opportunity_score"),
                "risk_score_pre_listing": snapshot.get("risk_score"),
                "net_ipo_score_pre_listing": snapshot.get("net_ipo_score"),
                "market_readiness_pre_listing": snapshot.get("market_readiness_score"),
                "offer_mid": snapshot.get("price_mid"),
                **metrics,
                "updated_at_utc": generated_at,
            }
        )
    outcomes = _merge_outcomes(existing, records)
    if not outcomes.empty:
        outcomes.to_csv(outcome_path, index=False)
    summary = _validation_summary(outcomes, generated_at)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
