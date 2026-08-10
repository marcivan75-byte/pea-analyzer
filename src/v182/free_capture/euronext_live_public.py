from __future__ import annotations

from datetime import date
import json
import os
import re
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .core import CaptureStore, is_observed, number, utcnow


SOURCE = "EURONEXT_LIVE_PUBLIC"
BASE = "https://live.euronext.com/en/product"
EURONEXT_MICS = {"XPAR", "XAMS", "XBRU", "XLIS", "XOSL", "XDUB"}


def _num(text: object) -> float | None:
    if text is None:
        return None
    raw = str(text).strip().replace("\u202f", "").replace(" ", "").replace(",", ".")
    raw = re.sub(r"[^0-9.+-]", "", raw)
    return number(raw)


def _label_number(text: str, labels: tuple[str, ...], percent: bool = False) -> float | None:
    for label in labels:
        pattern = rf"\b{re.escape(label)}\b\s*[:\-]?\s*([0-9][0-9\s.,]*)\s*{'%' if percent else ''}"
        match = re.search(pattern, text, flags=re.I)
        if match:
            value = _num(match.group(1))
            if value is not None:
                return value
    return None


def _json_candidates(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for node in soup.find_all("script"):
        raw = node.string or node.get_text("", strip=True)
        if not raw or len(raw) > 3_000_000:
            continue
        if node.get("type") == "application/ld+json":
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    out.append(payload)
                elif isinstance(payload, list):
                    out.extend(x for x in payload if isinstance(x, dict))
            except Exception:
                pass
        if "lastPrice" in raw or "freeFloat" in raw or '"bid"' in raw:
            for key in ("lastPrice", "last_price", "price", "freeFloat", "free_float", "bid", "ask"):
                for match in re.finditer(rf'"{re.escape(key)}"\s*:\s*"?([-+0-9.,]+)', raw):
                    out.append({key: match.group(1)})
    return out


def _first_json_number(items: list[dict], keys: tuple[str, ...]) -> float | None:
    for item in items:
        for key in keys:
            if key in item:
                value = _num(item.get(key))
                if value is not None:
                    return value
    return None


def _url(isin: str, mic: str, asset_class: str) -> str:
    kind = "etfs" if str(asset_class).upper() == "ETF" else "equities"
    return f"{BASE}/{kind}/{isin}-{mic}"


def capture(universe: pd.DataFrame, store: CaptureStore, max_symbols: int = 150) -> dict:
    mic_series = universe.get("euronext_mic", universe.get("mic", pd.Series("", index=universe.index))).astype(str).str.upper()
    candidates = universe.loc[mic_series.isin(EURONEXT_MICS)].head(max(0, int(max_symbols))).copy()
    if candidates.empty:
        store.add_health(SOURCE, "NO_EURONEXT_CANDIDATE")
        return {"status": "NO_EURONEXT_CANDIDATE", "attempted": 0, "facts_added": 0}

    session = requests.Session()
    headers = {
        "User-Agent": os.getenv("V182_USER_AGENT", "PEA-V21.1-FreeCapture/1.2"),
        "Accept-Language": "en-GB,en;q=0.8,fr;q=0.6",
    }
    facts: list[dict] = []
    identities: list[dict] = []
    attempted = succeeded = blocked = failed = 0
    today = date.today().isoformat()

    for _, row in candidates.iterrows():
        attempted += 1
        isin = str(row.get("isin") or "").strip().upper()
        mic = str(row.get("euronext_mic") or row.get("mic") or "").strip().upper()
        asset_class = str(row.get("asset_class") or "ACTION").strip().upper()
        url = _url(isin, mic, asset_class)
        try:
            response = session.get(url, headers=headers, timeout=20)
            if response.status_code in {401, 403, 429}:
                blocked += 1
                if blocked >= 3:
                    break
                continue
            if not response.ok:
                failed += 1
                continue
            html = response.text
            if isin not in html.upper():
                failed += 1
                continue
            soup = BeautifulSoup(html, "html.parser")
            text = " ".join(soup.stripped_strings)
            json_items = _json_candidates(soup)
            last = _first_json_number(json_items, ("lastPrice", "last_price", "price"))
            if last is None:
                last = _label_number(text, ("Last price", "Dernier", "Last"))
            free_float = _first_json_number(json_items, ("freeFloat", "free_float"))
            if free_float is None:
                free_float = _label_number(text, ("Free float", "Flottant"), percent=True)
            bid = _first_json_number(json_items, ("bid",))
            ask = _first_json_number(json_items, ("ask",))
            if bid is None:
                bid = _label_number(text, ("Bid", "Achat"))
            if ask is None:
                ask = _label_number(text, ("Ask", "Vente"))
            spread_pct = None
            if bid is not None and ask is not None and ask >= bid and (ask + bid) > 0:
                spread_pct = (ask - bid) / ((ask + bid) / 2.0) * 100.0

            observed = {
                "euronext_live_last_price": last,
                "free_float_pct": free_float,
                "spread_pct": spread_pct,
                "euronext_live_bid": bid,
                "euronext_live_ask": ask,
            }
            observed = {k: v for k, v in observed.items() if v is not None}
            identities.append({
                "isin": isin,
                "name": str(row.get("name") or ""),
                "source": SOURCE,
                "ticker": str(row.get("euronext_symbol") or row.get("ticker") or ""),
                "exchange": "EURONEXT",
                "mic": mic,
                "figi": "",
                "composite_figi": "",
                "share_class_figi": "",
                "security_type": asset_class,
                "lei": "",
                "lei_source": "",
                "resolution_status": "VALIDATED_ISIN_ON_PUBLIC_PRODUCT_PAGE",
                "as_of": today,
                "observed_at_utc": utcnow(),
            })
            for field, value in observed.items():
                facts.append({
                    "isin": isin,
                    "field": field,
                    "value": value,
                    "value_text": "",
                    "as_of": today,
                    "source": SOURCE,
                    "evidence": f"EURONEXT_PUBLIC_PRODUCT_PAGE_ISIN_VALIDATED|{url}",
                    "confidence": 0.94,
                    "status": "OBSERVED_VALIDATED_ISIN",
                    "observed_at_utc": utcnow(),
                })
            succeeded += 1
        except Exception:
            failed += 1
        time.sleep(float(os.getenv("V211_EURONEXT_LIVE_DELAY_SECONDS", "0.35")))

    identity_added = store.upsert_identity(identities)
    fact_added = store.upsert_facts(facts)
    status = "BLOCKED" if blocked >= 3 and succeeded == 0 else ("OK" if succeeded else "NO_NEW_DATA")
    store.add_health(
        SOURCE,
        status,
        attempted=attempted,
        succeeded=succeeded,
        failed=failed + blocked,
        message=f"identity_added={identity_added}; facts_added={fact_added}; blocked={blocked}; ISIN validation required",
    )
    return {
        "status": status,
        "attempted": attempted,
        "succeeded": succeeded,
        "blocked": blocked,
        "failed": failed,
        "identity_added": identity_added,
        "facts_added": fact_added,
        "fields": ["identity", "last_price_if_exposed", "free_float_if_exposed", "bid_ask_spread_if_exposed"],
    }
