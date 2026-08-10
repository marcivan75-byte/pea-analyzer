from __future__ import annotations

from datetime import date
import time

import pandas as pd
import requests

from .core import CaptureStore, clean_text, is_observed, number, utcnow
from .zonebourse_public import (
    _candidate_roots,
    _headers,
    _listing_map,
    _parse_consensus,
    _search_fallback,
)


SOURCE = "ZONEBOURSE_PUBLIC_V2"
PRIMARY_FIELDS = {
    "target_mean_v21",
    "target_high_v21",
    "target_low_v21",
    "n_analysts_v21",
    "consensus_score_100_v21",
}


def _obs(s: pd.Series) -> pd.Series:
    return s.map(is_observed)


def _priority(universe: pd.DataFrame) -> pd.DataFrame:
    """Prefer securities already known to have analyst coverage but with consensus gaps.

    This avoids spending public-page requests on microcaps for which no analyst consensus is
    likely to exist. Ranking is deliberately independent from the V21 scoring weights.
    """
    x = universe.copy()
    idx = x.index
    n = pd.to_numeric(x.get("n_analysts_v21", pd.Series(index=idx, dtype=object)), errors="coerce")
    target_mean = _obs(x.get("target_mean_v21", pd.Series("", index=idx)))
    target_high = _obs(x.get("target_high_v21", pd.Series("", index=idx)))
    target_low = _obs(x.get("target_low_v21", pd.Series("", index=idx)))
    consensus = _obs(x.get("consensus_score_100_v21", pd.Series("", index=idx)))

    missing_count = (~target_mean).astype(int) + (~target_high).astype(int) + (~target_low).astype(int) + (~consensus).astype(int)
    known_analysts = n.fillna(0).clip(lower=0)
    has_analysts = known_analysts.gt(0)

    mt = pd.to_numeric(x.get("score_mt", pd.Series(0, index=idx)), errors="coerce").fillna(0)
    lt = pd.to_numeric(x.get("score_lt", pd.Series(0, index=idx)), errors="coerce").fillna(0)
    selected = x.get("selection_mt", pd.Series(False, index=idx)).astype(str).str.lower().isin({"true", "1", "yes"})
    selected |= x.get("selection_lt", pd.Series(False, index=idx)).astype(str).str.lower().isin({"true", "1", "yes"})

    # Large weights are intentional: verified analyst coverage is the strongest predictor
    # that a public consensus page will exist. No model score or investment weight is changed.
    x["_zb_priority"] = (
        has_analysts.astype(float) * 10000.0
        + known_analysts.clip(upper=30) * 100.0
        + missing_count * 500.0
        + selected.astype(float) * 250.0
        + (mt + lt) / 2.0
    )
    x["_zb_missing"] = missing_count
    x = x[x["_zb_missing"].gt(0)].copy()
    return x.sort_values(["_zb_priority", "_zb_missing"], ascending=[False, False], kind="stable")


def capture(universe: pd.DataFrame, store: CaptureStore, cfg: dict, max_symbols: int = 60) -> dict:
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
    countries = set(targets.get("country", pd.Series(dtype=object)).fillna("").astype(str).str.upper())
    listings = _listing_map(session, headers, countries)

    rows: list[dict] = []
    succeeded = failed = blocked = identity_rejected = 0
    no_candidate = 0
    samples: list[dict] = []
    today = date.today().isoformat()

    for _, row in targets.iterrows():
        isin = str(row["isin"])
        canonical_close = number(row.get("last_close"))
        candidates = _candidate_roots(row, listings)
        if not candidates:
            candidates = [(0.0, name, root) for name, root in _search_fallback(session, headers, isin)]
        if not candidates:
            no_candidate += 1
            failed += 1
            if len(samples) < 10:
                samples.append({
                    "isin": isin,
                    "name": clean_text(row.get("name")),
                    "n_analysts_base": number(row.get("n_analysts_v21")),
                    "status": "NO_CANDIDATE",
                })
            continue

        accepted = None
        tried = 0
        for name_score, candidate_name, root in candidates[:5]:
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
                time.sleep(0.8)

        if blocked >= 3 and accepted is None:
            failed += 1
            break
        if accepted is None:
            failed += 1
            if len(samples) < 10:
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
        for field, value in observed.items():
            rows.append({
                "isin": isin,
                "field": field,
                "value": value,
                "value_text": "",
                "as_of": today,
                "source": SOURCE,
                "evidence": f"PUBLIC_CONSENSUS_ISIN_PRICE_VALIDATED|{url}",
                "confidence": 0.90,
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
                "evidence": f"PUBLIC_CONSENSUS_ISIN_PRICE_VALIDATED|{url}",
                "confidence": 0.90,
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
                "evidence": f"PUBLIC_CONSENSUS_ISIN_PRICE_VALIDATED|{url}",
                "confidence": 0.90,
                "status": "OBSERVED_VALIDATED_ISIN_PRICE",
                "observed_at_utc": utcnow(),
            })
        if len(samples) < 10:
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
            f"facts_added={added}; blocked={blocked}; no_candidate={no_candidate}; "
            f"identity_rejected={identity_rejected}; analyst_coverage_first; samples={samples}"
        ),
    )
    return {
        "status": status,
        "attempted": min(len(targets), succeeded + failed),
        "succeeded": succeeded,
        "failed": failed,
        "blocked": blocked,
        "no_candidate": no_candidate,
        "identity_rejected": identity_rejected,
        "facts_added": added,
        "samples": samples,
    }
