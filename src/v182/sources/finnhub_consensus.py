from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from v182.sources.rate_limit import StartRateLimiter

FINNHUB_BASE = "https://finnhub.io/api/v1"
_SCORE_WEIGHTS = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}


def _label_from_score(score: float) -> str:
    if score >= 4.5:
        return "STRONG_BUY"
    if score >= 3.5:
        return "BUY"
    if score >= 2.5:
        return "HOLD"
    if score >= 1.5:
        return "SELL"
    return "STRONG_SELL"


def _counts(row: dict) -> dict[str, int]:
    return {k: int(row.get(k, 0) or 0) for k in _SCORE_WEIGHTS}


def _score_from_counts(counts: dict[str, int]) -> float | None:
    total = sum(counts.values())
    if total <= 0:
        return None
    return sum(counts[k] * w for k, w in _SCORE_WEIGHTS.items()) / total


def _period(row: dict) -> date | None:
    raw = row.get("period")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _previous_monthish(reco: list[dict], latest: dict) -> dict | None:
    latest_date = _period(latest)
    if latest_date is None:
        return reco[1] if len(reco) > 1 else None
    for row in reco[1:]:
        d = _period(row)
        if d is not None and (latest_date - d).days >= 21:
            return row
    return reco[1] if len(reco) > 1 else None


def _fetch_one(ticker: str, api_key: str, requests, limiter: StartRateLimiter) -> tuple[list[dict], list[dict]]:
    failures: list[dict]=[]
    try:
        limiter.wait()
        reco_resp = requests.get(
            f"{FINNHUB_BASE}/stock/recommendation",
            params={"symbol": ticker, "token": api_key}, timeout=15,
        )
        reco_resp.raise_for_status()
        reco = reco_resp.json()
        if not reco:
            return [], [{"ticker": ticker, "reason": "NO_RECOMMENDATION_DATA"}]

        latest = reco[0]
        counts = _counts(latest)
        score = _score_from_counts(counts)
        if score is None:
            return [], [{"ticker": ticker, "reason": "EMPTY_RECOMMENDATION_COUNTS"}]

        score = round(score, 4)
        rating = _label_from_score(score)
        total = sum(counts.values())
        fields: dict[str, object] = {
            "consensus": rating,
            "consensus_rating": rating,
            "consensus_score": score,
            "consensus_period": latest.get("period"),
            "buy_n": counts["strongBuy"] + counts["buy"],
            "hold_n": counts["hold"],
            "sell_n": counts["strongSell"] + counts["sell"],
            "n_analysts": total,
            "consensus_status": "OK",
            "consensus_source": "Finnhub",
        }

        previous = _previous_monthish(reco, latest)
        if previous is not None:
            previous_counts = _counts(previous)
            previous_score = _score_from_counts(previous_counts)
            if previous_score is not None:
                delta_100 = (score - previous_score) * 20.0
                current_net = (counts["strongBuy"] + counts["buy"]) - (counts["sell"] + counts["strongSell"])
                previous_net = (previous_counts["strongBuy"] + previous_counts["buy"]) - (previous_counts["sell"] + previous_counts["strongSell"])
                fields.update({
                    "consensus_previous_period": previous.get("period"),
                    "consensus_delta_4w": round(delta_100, 4),
                    "net_upgrades_30d": int(current_net - previous_net),
                    "broker_weighted_revision_30d": round(delta_100, 4),
                })

        limiter.wait()
        target_resp = requests.get(
            f"{FINNHUB_BASE}/stock/price-target",
            params={"symbol": ticker, "token": api_key}, timeout=15,
        )
        if target_resp.ok:
            target = target_resp.json()
            if target.get("targetMean") is not None:
                fields["target_price"] = target["targetMean"]
            if target.get("lastUpdated"):
                fields["target_last_updated"] = target["lastUpdated"]

        observations=[
            {"ticker": ticker, "field": field, "value": value, "source": "Finnhub"}
            for field,value in fields.items() if value is not None
        ]
        return observations, failures
    except Exception as exc:
        failures.append({"ticker": ticker, "reason": type(exc).__name__, "detail": str(exc)[:160]})
        return [], failures


def fetch_consensus(
    tickers: list[str], api_key: str, delay_seconds: float = 1.1, max_workers: int = 8,
) -> tuple[list[dict], list[dict]]:
    """Collect consensus and 4-week revisions with rate-safe bounded concurrency.

    Two calls may still be made per ticker. Request starts remain globally spaced
    by `delay_seconds`, preserving the former API cadence ceiling, while multiple
    workers overlap HTTP latency instead of adding it serially to every ticker.
    Missing history remains missing and no neutral value is imputed.
    """
    import requests

    unique=sorted({t for t in tickers if t})
    if not unique:
        return [], []
    limiter=StartRateLimiter(delay_seconds)
    observations: list[dict]=[]
    failures: list[dict]=[]
    workers=max(1,min(int(max_workers),len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures=[executor.submit(_fetch_one,ticker,api_key,requests,limiter) for ticker in unique]
        for future in as_completed(futures):
            obs,failed=future.result()
            observations.extend(obs)
            failures.extend(failed)
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    failures.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("reason",""))))
    return observations, failures
