from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import os
import time

import pandas as pd
import requests

from .core import CaptureStore, clean_text, utcnow

BASE = "https://api.gleif.org/api/v1/lei-records"


def _resolve(isin: str) -> tuple[str, str, str]:
    try:
        r = requests.get(
            BASE,
            params={"filter[isin]": isin, "page[size]": 2},
            headers={"Accept": "application/vnd.api+json", "User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")},
            timeout=20,
        )
        if r.status_code == 429:
            time.sleep(2.0)
            r = requests.get(BASE, params={"filter[isin]": isin, "page[size]": 2}, timeout=20)
        r.raise_for_status()
        data = r.json().get("data") or []
        if len(data) == 1:
            lei = clean_text(data[0].get("id"))
            return isin, lei, "UNIQUE" if lei else "NO_MATCH"
        if len(data) > 1:
            leis = sorted({clean_text(x.get("id")) for x in data if clean_text(x.get("id"))})
            return isin, leis[0] if len(leis) == 1 else "", "AMBIGUOUS"
        return isin, "", "NO_MATCH"
    except Exception as exc:
        return isin, "", f"ERROR:{type(exc).__name__}"


def capture(universe: pd.DataFrame, store: CaptureStore, max_symbols: int = 600, workers: int = 6) -> dict:
    identity = store.identity()
    mapped: set[str] = set()
    if not identity.empty:
        # Any valid LEI already captured (bulk or API) is definitive enough to skip another API call.
        mapped = set(identity.loc[identity["lei"].astype(str).str.len().eq(20), "isin"].astype(str))
    targets = [str(x) for x in universe["isin"] if str(x) not in mapped][:max_symbols]
    if not targets:
        store.add_health("GLEIF_ISIN_LEI", "NO_NEW_DATA", message="No true LEI gaps after bulk/cache")
        return {"status": "NO_NEW_DATA", "attempted": 0, "resolved": 0}

    resolved: list[tuple[str, str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        future_map = {pool.submit(_resolve, isin): isin for isin in targets}
        for future in as_completed(future_map):
            resolved.append(future.result())

    names = universe.set_index(universe["isin"].astype(str))["name"].to_dict()
    rows = []
    ok = 0
    no_match = 0
    errors = 0
    for isin, lei, status in resolved:
        if lei:
            ok += 1
            rows.append({
                "isin": isin, "name": clean_text(names.get(isin)), "source": "GLEIF_ISIN_LEI",
                "ticker": "", "exchange": "", "mic": "", "figi": "", "composite_figi": "",
                "share_class_figi": "", "security_type": "", "lei": lei, "lei_source": "GLEIF_API_ISIN",
                "resolution_status": status, "as_of": date.today().isoformat(), "observed_at_utc": utcnow(),
            })
        elif status == "NO_MATCH":
            no_match += 1
        else:
            errors += 1
    added = store.upsert_identity(rows)
    store.add_health(
        "GLEIF_ISIN_LEI", "OK" if ok else "NO_NEW_DATA", len(targets), ok, no_match + errors,
        message=f"added={added}; true_gaps={len(targets)}; no_match={no_match}; errors={errors}; workers={workers}"
    )
    return {"status": "OK", "attempted": len(targets), "resolved": ok, "no_match": no_match, "errors": errors}
