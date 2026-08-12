from __future__ import annotations
from datetime import date
import time

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


def fetch_consensus(tickers: list[str], api_key: str, delay_seconds: float = 1.1) -> tuple[list[dict], list[dict]]:
    """Collect current consensus plus 4-week revision signals from Finnhub.

    The revision fields are derived only when a prior recommendation snapshot is
    returned by Finnhub. Missing history remains missing; no neutral value is
    imputed. Two API calls are made per ticker (recommendations + price target).
    """
    import requests

    observations: list[dict] = []
    failures: list[dict] = []

    for ticker in sorted({t for t in tickers if t}):
        try:
            reco_resp = requests.get(
                f"{FINNHUB_BASE}/stock/recommendation",
                params={"symbol": ticker, "token": api_key}, timeout=15,
            )
            reco_resp.raise_for_status()
            reco = reco_resp.json()
            if not reco:
                failures.append({"ticker": ticker, "reason": "NO_RECOMMENDATION_DATA"})
                time.sleep(delay_seconds)
                continue

            latest = reco[0]
            counts = _counts(latest)
            score = _score_from_counts(counts)
            if score is None:
                failures.append({"ticker": ticker, "reason": "EMPTY_RECOMMENDATION_COUNTS"})
                time.sleep(delay_seconds)
                continue

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

            time.sleep(delay_seconds)
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

            for field, value in fields.items():
                if value is None:
                    continue
                observations.append({"ticker": ticker, "field": field, "value": value, "source": "Finnhub"})

        except Exception as exc:
            failures.append({"ticker": ticker, "reason": type(exc).__name__, "detail": str(exc)[:160]})
        time.sleep(delay_seconds)

    return observations, failures
