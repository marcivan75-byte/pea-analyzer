from __future__ import annotations
from datetime import datetime, timezone
import requests
from v183.smart_money.models import SmartMoneyEvent

BASE_URL = "https://finnhub.io/api/v1/stock/insider-transactions"

BUY_CODES = {"P", "BUY", "PURCHASE"}
SELL_CODES = {"S", "SELL", "SALE"}
NEUTRAL_CODES = {"A", "M", "G", "F", "D", "I", "J", "L", "U", "W", "X", "Z"}


def fetch(symbol: str, api_key: str, from_date: str | None = None,
          to_date: str | None = None, limit: int = 100, timeout: int = 20) -> list[dict]:
    params = {"symbol": symbol, "limit": min(int(limit), 100), "token": api_key}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    response = requests.get(BASE_URL, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", []) if isinstance(payload, dict) else []


def normalize(rows: list[dict], isin: str) -> list[dict]:
    events = []
    for row in rows:
        code = str(row.get("transactionCode") or "").upper().strip()
        change = row.get("change")
        direction = _direction(code, change)
        subtype = "MARKET_BUY" if direction > 0 and code in BUY_CODES else \
                  "MARKET_SELL" if direction < 0 and code in SELL_CODES else \
                  "NON_DIRECTIONAL" if direction == 0 else "DIRECTION_INFERRED_FROM_CHANGE"
        price = _float(row.get("transactionPrice"))
        qty = abs(_float(change)) if _float(change) is not None else None
        value = price * qty if price is not None and qty is not None else None
        tx_date = row.get("transactionDate") or row.get("filingDate")
        pub_date = row.get("filingDate") or tx_date
        if not pub_date:
            continue
        event = SmartMoneyEvent(
            universe="ACTION",
            isin=isin,
            event_type="INSIDER",
            event_subtype=subtype,
            source="Finnhub",
            evidence_level="B",
            validation_status="ISIN_MATCHED",
            publication_date=str(pub_date)[:10],
            transaction_date=str(tx_date)[:10] if tx_date else None,
            actor_name=row.get("name"),
            actor_role="OTHER_PDMR",
            direction=direction,
            quantity=qty,
            price=price,
            value_eur=value,
            source_document_id=f"FINNHUB:{row.get('symbol','')}:{row.get('filingDate','')}:{row.get('name','')}",
            collected_at=datetime.now(timezone.utc).isoformat(),
            metadata={"transaction_code": code, "shares_after": row.get("share")},
        )
        events.append(event.to_dict())
    return events


def _direction(code: str, change) -> int:
    if code in BUY_CODES:
        return 1
    if code in SELL_CODES:
        return -1
    if code in NEUTRAL_CODES:
        return 0
    c = _float(change)
    if c is None or c == 0:
        return 0
    return 1 if c > 0 else -1


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
