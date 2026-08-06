from __future__ import annotations
import time

FINNHUB_BASE = "https://finnhub.io/api/v1"

# Barème simple note/5 pour dériver un score et un label homogènes avec le
# reste du projet (colonnes consensus_score / consensus_rating).
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


def fetch_consensus(tickers: list[str], api_key: str, delay_seconds: float = 1.1) -> tuple[list[dict], list[dict]]:
    """Wave 05 : pour chaque ticker, interroge /stock/recommendation (avis des
    analystes par période) et /stock/price-target (objectifs de cours), puis
    dérive consensus/consensus_rating/consensus_score/buy_n/hold_n/sell_n.

    Le plan gratuit Finnhub est limité à 60 requêtes/minute : delay_seconds
    par défaut (1.1s) reste sous cette limite avec 2 appels par ticker.
    """
    import requests

    observations: list[dict] = []
    failures: list[dict] = []

    for ticker in sorted({t for t in tickers if t}):
        try:
            reco_resp = requests.get(f"{FINNHUB_BASE}/stock/recommendation",
                                      params={"symbol": ticker, "token": api_key}, timeout=15)
            reco_resp.raise_for_status()
            reco = reco_resp.json()
            if not reco:
                failures.append({"ticker": ticker, "reason": "NO_RECOMMENDATION_DATA"})
                time.sleep(delay_seconds)
                continue

            latest = reco[0]  # Finnhub renvoie du plus récent au plus ancien
            counts = {k: latest.get(k, 0) or 0 for k in _SCORE_WEIGHTS}
            total = sum(counts.values())
            if total == 0:
                failures.append({"ticker": ticker, "reason": "EMPTY_RECOMMENDATION_COUNTS"})
                time.sleep(delay_seconds)
                continue

            score = round(sum(counts[k] * w for k, w in _SCORE_WEIGHTS.items()) / total, 2)
            rating = _label_from_score(score)

            fields = {
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

            time.sleep(delay_seconds)
            target_resp = requests.get(f"{FINNHUB_BASE}/stock/price-target",
                                        params={"symbol": ticker, "token": api_key}, timeout=15)
            if target_resp.ok:
                target = target_resp.json()
                if target.get("targetMean"):
                    fields["target_price"] = target["targetMean"]

            for field, value in fields.items():
                if value is None:
                    continue
                observations.append({"ticker": ticker, "field": field, "value": value, "source": "Finnhub"})

        except Exception as exc:
            failures.append({"ticker": ticker, "reason": type(exc).__name__})
        time.sleep(delay_seconds)

    return observations, failures
