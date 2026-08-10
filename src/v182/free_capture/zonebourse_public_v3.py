from __future__ import annotations

from datetime import date
import time

import pandas as pd
import requests

from .core import CaptureStore, clean_text, is_observed, number, utcnow
from .zonebourse_public import (
    _action_links,
    _headers,
    _parse_consensus,
    _score_names,
    _search_fallback,
)
from .zonebourse_public_v2 import _priority


SOURCE = "ZONEBOURSE_PUBLIC_V3"
BASE = "https://www.zonebourse.com"
INDEX_URLS = [
    f"{BASE}/palmares/consensus/couvertures-analystes/",
    f"{BASE}/palmares/consensus/objectif-de-cours/",
    f"{BASE}/palmares/consensus/opinion-analystes/",
]
PRIMARY_FIELDS = {
    "target_mean_v21",
    "target_high_v21",
    "target_low_v21",
    "n_analysts_v21",
    "consensus_score_100_v21",
}


def _analyst_index(session: requests.Session, headers: dict[str, str]) -> list[tuple[str, str]]:
    """Build a public URL index from Zonebourse analyst-ranking pages.

    These pages already expose links to companies with analyst coverage. They are a much better
    discovery layer than country market-cap lists for the prospective-data use case.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in INDEX_URLS:
        try:
            r = session.get(url, headers=headers, timeout=30)
            if not r.ok:
                continue
            for name, root in _action_links(r.text):
                if root in seen:
                    continue
                seen.add(root)
                out.append((name, root))
        except Exception:
            continue
        time.sleep(0.4)
    return out


def _candidates(row: pd.Series, index: list[tuple[str, str]]) -> list[tuple[float, str, str]]:
    name = clean_text(row.get("name"))
    scored: list[tuple[float, str, str]] = []
    for candidate_name, root in index:
        score = _score_names(name, candidate_name)
        if score >= 0.58:
            scored.append((score, candidate_name, root))
    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[:6]


def capture(universe: pd.DataFrame, store: CaptureStore, cfg: dict, max_symbols: int = 80) -> dict:
    ranked = _priority(universe)
    old = store.facts()
    fresh: set[str] = set()
    if not old.empty:
        z = old[old["source"].astype(str).eq(SOURCE)].copy()
        if not z.empty:
            z["_d"] = pd.to_datetime(z["observed_at_utc"], errors="coerce", utc=True)
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
            fresh = set(z.loc[z["_d"].ge(cutoff), "isin"].astype(str))

    targets = ranked.loc[~ranked["isin"].astype(str).isin(fresh)].head(max_symbols).copy()
    if targets.empty:
        store.add_health(SOURCE, "CACHE_FRESH")
        return {"status": "CACHE_FRESH", "attempted": 0, "succeeded": 0, "facts_added": 0}

    session = requests.Session()
    headers = _headers()
    index = _analyst_index(session, headers)

    rows: list[dict] = []
    succeeded = failed = blocked = identity_rejected = no_candidate = 0
    samples: list[dict] = []
    today = date.today().isoformat()

    for _, row in targets.iterrows():
        isin = str(row["isin"])
        canonical_close = number(row.get("last_close"))
        candidates = _candidates(row, index)
        if not candidates:
            candidates = [(0.0, name, root) for name, root in _search_fallback(session, headers, isin)]
        if not candidates:
            no_candidate += 1
            failed += 1
            if len(samples) < 12:
                samples.append({
                    "isin": isin,
                    "name": clean_text(row.get("name")),
                    "n_analysts_base": number(row.get("n_analysts_v21")),
                    "status": "NO_INDEX_CANDIDATE",
                })
            continue

        accepted = None
        tried = 0
        for name_score, candidate_name, root in candidates:
            tried += 1
            url = root.rstrip("/") + "/consensus/"
            try:
                r = session.get(url, headers=headers, timeout=25)
                if r.status_code in {401, 403, 429}:
                    blocked += 1
                    if blocked >= 3:
                        break
                    continue
                if not r.ok:
                    continue
                parsed = _parse_consensus(r.text, isin, canonical_close)
                if parsed.get("identity") == "VALIDATED_ISIN_PRICE":
                    accepted = (url, candidate_name, name_score, parsed)
                    break
                identity_rejected += 1
            except Exception:
                continue
            finally:
                time.sleep(0.7)

        if blocked >= 3 and accepted is None:
            failed += 1
            break
        if accepted is None:
            failed += 1
            if len(samples) < 12:
                samples.append({
                    "isin": isin,
                    "name": clean_text(row.get("name")),
                    "status": "NO_PRICE_VALIDATED_PAGE",
                    "candidates": tried,
                })
            continue

        url, candidate_name, name_score, parsed = accepted
        observed = {k: v for k, v in parsed.items() if k in PRIMARY_FIELDS and is_observed(v)}
        if not observed:
            failed += 1
            continue

        succeeded += 1
        evidence = f"PUBLIC_ANALYST_INDEX_THEN_CONSENSUS_ISIN_PRICE_VALIDATED|{url}"
        for field, value in observed.items():
            rows.append({
                "isin": isin,
                "field": field,
                "value": value,
                "value_text": "",
                "as_of": today,
                "source": SOURCE,
                "evidence": evidence,
                "confidence": 0.92,
                "status": "OBSERVED_VALIDATED_ISIN_PRICE",
                "observed_at_utc": utcnow(),
            })
        if parsed.get("recommendation"):
            rows.append({
                "isin": isin,
                "field": "zonebourse_recommendation_label",
                "value": "",
                "value_text": parsed["recommendation"],
                "as_of": today,
                "source": SOURCE,
                "evidence": evidence,
                "confidence": 0.92,
                "status": "OBSERVED_VALIDATED_ISIN_PRICE",
                "observed_at_utc": utcnow(),
            })
        if parsed.get("zonebourse_target_upside_pct") is not None:
            rows.append({
                "isin": isin,
                "field": "zonebourse_target_upside_pct",
                "value": parsed["zonebourse_target_upside_pct"],
                "value_text": "",
                "as_of": today,
                "source": SOURCE,
                "evidence": evidence,
                "confidence": 0.92,
                "status": "OBSERVED_VALIDATED_ISIN_PRICE",
                "observed_at_utc": utcnow(),
            })
        if len(samples) < 12:
            samples.append({
                "isin": isin,
                "candidate": candidate_name,
                "name_score": round(float(name_score), 3),
                "n_analysts_base": number(row.get("n_analysts_v21")),
                "price_ratio": parsed.get("price_ratio"),
                "currency": parsed.get("currency"),
                "fields": sorted(observed),
            })

    added = store.upsert_facts(rows)
    status = "BLOCKED" if blocked >= 3 and not succeeded else ("OK" if succeeded else "NO_NEW_DATA")
    store.add_health(
        SOURCE,
        status,
        attempted=min(len(targets), succeeded + failed),
        succeeded=succeeded,
        failed=failed,
        message=(
            f"index_links={len(index)}; facts_added={added}; blocked={blocked}; "
            f"no_candidate={no_candidate}; identity_rejected={identity_rejected}; samples={samples}"
        ),
    )
    return {
        "status": status,
        "index_links": len(index),
        "attempted": min(len(targets), succeeded + failed),
        "succeeded": succeeded,
        "failed": failed,
        "blocked": blocked,
        "no_candidate": no_candidate,
        "identity_rejected": identity_rejected,
        "facts_added": added,
        "samples": samples,
    }
