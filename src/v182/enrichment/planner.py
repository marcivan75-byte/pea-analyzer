from __future__ import annotations

METHOD_GAIN = {
    "YFINANCE_BULK_HISTORY": 100,
    "INTERNAL_FROM_OHLCV": 98,
    "YFINANCE_BULK_INFO_THEN_FALLBACK": 84,
    "BOURSORAMA_ZONEBOURSE_YFINANCE": 82,
    "ABC_BOURSE_ISSUER_BOURSORAMA": 86,
    "BOURSORAMA_BULK": 80,
    "AMF_FINNHUB_GDELT": 72,
    "INTERNAL_SHORTLIST_ENGINE": 90,
    "USER_AND_BROKER_INPUT": 0,
}

def priority(method: str, missing_count: int, committee_priority: float = 0) -> float:
    return METHOD_GAIN.get(method, 50) + missing_count * 2 + committee_priority
