from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
import os
import re
import time
import unicodedata
from urllib.parse import quote_plus, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .core import CaptureStore, clean_text, is_observed, number, priority_frame, utcnow


BASE = "https://www.zonebourse.com"
EUROPE_LIST = f"{BASE}/bourse/actions/europe/"
COUNTRY_ALIASES = {
    "FRANCE": {"france"},
    "BELGIUM": {"belgique", "belgium"},
    "NETHERLANDS": {"pays bas", "netherlands", "hollande"},
    "ITALY": {"italie", "italy"},
    "NORWAY": {"norvege", "norway"},
    "PORTUGAL": {"portugal"},
    "SPAIN": {"espagne", "spain"},
    "IRELAND": {"irlande", "ireland"},
    "LUXEMBOURG": {"luxembourg"},
    "SWEDEN": {"suede", "sweden"},
    "DENMARK": {"danemark", "denmark"},
    "GERMANY": {"allemagne", "germany"},
    "GREECE": {"grece", "greece"},
    "AUSTRIA": {"autriche", "austria"},
    "FINLAND": {"finlande", "finland"},
    "CYPRUS": {"chypre", "cyprus"},
    "MALTA": {"malte", "malta"},
}
CORP_WORDS = {
    "sa", "sas", "se", "nv", "plc", "spa", "ag", "ab", "asa", "oyj", "ltd", "limited",
    "group", "groupe", "holding", "holdings", "societe", "company", "compagnie", "corporation",
}
RECOMMENDATION_SCORE = {
    "ACHETER": 100.0,
    "ACCUMULER": 75.0,
    "CONSERVER": 50.0,
    "NEUTRE": 50.0,
    "ALLEGER": 25.0,
    "ALLÉGER": 25.0,
    "VENDRE": 0.0,
}


def _norm(value: object) -> str:
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _name_key(value: object) -> str:
    tokens = [t for t in _norm(value).split() if t not in CORP_WORDS]
    return " ".join(tokens)


def _fr_num(value: object) -> float | None:
    s = str(value or "").replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return number(m.group(0)) if m else None


def _score_names(a: object, b: object) -> float:
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ta, tb = set(ka.split()), set(kb.split())
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    seq = SequenceMatcher(None, ka, kb).ratio()
    contains = 0.95 if (ka in kb or kb in ka) and min(len(ka), len(kb)) >= 5 else 0.0
    return max(seq, jaccard, contains)


def _headers() -> dict[str, str]:
    return {
        "User-Agent": os.getenv("V182_USER_AGENT", "PEA-V21.1-FreeCapture/1.0"),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    }


def _action_links(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if "/cours/action/" not in href:
            continue
        m = re.search(r"(/cours/action/[^/?#]+/)", href)
        if not m:
            continue
        root = urljoin(BASE, m.group(1))
        name = " ".join(a.stripped_strings).strip()
        if not name or root in seen:
            continue
        seen.add(root)
        out.append((name, root))
    return out


def _country_catalogue(session: requests.Session, headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {"FRANCE": f"{BASE}/bourse/actions/europe/france-51/"}
    try:
        r = session.get(EUROPE_LIST, headers=headers, timeout=30)
        if not r.ok:
            return out
        soup = BeautifulSoup(r.text, "html.parser")
        links: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            href = str(a.get("href") or "")
            if not re.search(r"/bourse/actions/europe/[^/?#]+-\d+/?$", href):
                continue
            label = _norm(" ".join(a.stripped_strings))
            slug = _norm(href.rsplit("/", 2)[-2].rsplit("-", 1)[0].replace("-", " "))
            links.append((f"{label} {slug}".strip(), urljoin(BASE, href)))
        for country, aliases in COUNTRY_ALIASES.items():
            for label, url in links:
                if any(_norm(alias) in label for alias in aliases):
                    out[country] = url
                    break
    except Exception:
        pass
    return out


def _listing_map(
    session: requests.Session,
    headers: dict[str, str],
    countries: set[str],
) -> dict[str, list[tuple[str, str]]]:
    catalogue = _country_catalogue(session, headers)
    out: dict[str, list[tuple[str, str]]] = {}
    for country in sorted(countries):
        url = catalogue.get(country)
        if not url:
            continue
        try:
            r = session.get(url, headers=headers, timeout=30)
            if r.ok:
                out[country] = _action_links(r.text)
        except Exception:
            continue
        time.sleep(0.4)
    # The broad Europe page is an additional fallback for large-cap European issuers.
    try:
        r = session.get(EUROPE_LIST, headers=headers, timeout=30)
        if r.ok:
            out["__EUROPE__"] = _action_links(r.text)
    except Exception:
        pass
    return out


def _search_fallback(session: requests.Session, headers: dict[str, str], isin: str) -> list[tuple[str, str]]:
    # Public search routes are only fallback URL discovery. Every candidate is still ISIN-validated later.
    urls = [
        f"{BASE}/recherche/?q={quote_plus(isin)}",
        f"{BASE}/recherche/?search={quote_plus(isin)}",
    ]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in urls:
        try:
            r = session.get(url, headers=headers, timeout=20)
            if not r.ok:
                continue
            for name, root in _action_links(r.text):
                if root not in seen:
                    seen.add(root)
                    out.append((name, root))
        except Exception:
            continue
    return out


def _candidate_roots(row: pd.Series, listings: dict[str, list[tuple[str, str]]]) -> list[tuple[float, str, str]]:
    name = clean_text(row.get("name"))
    country = clean_text(row.get("country")).upper()
    pool = list(listings.get(country, [])) + list(listings.get("__EUROPE__", []))
    ranked: list[tuple[float, str, str]] = []
    seen: set[str] = set()
    for candidate_name, root in pool:
        if root in seen:
            continue
        seen.add(root)
        score = _score_names(name, candidate_name)
        if score >= 0.62:
            ranked.append((score, candidate_name, root))
    ranked.sort(reverse=True, key=lambda x: x[0])
    return ranked[:5]


def _field_num(text: str, label: str) -> float | None:
    m = re.search(re.escape(label) + r"\s+(-?\d[\d\s\u202f\xa0]*(?:[,.]\d+)?)", text, flags=re.I)
    return _fr_num(m.group(1)) if m else None


def _parse_consensus(html: str, isin: str, canonical_close: float | None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    text = re.sub(r"\s+", " ", text).strip()
    compact = re.sub(r"\s+", "", text).upper()
    if isin.upper() not in compact:
        return {"identity": "ISIN_MISMATCH"}
    if "Consensus des Analystes" not in text:
        return {"identity": "NO_CONSENSUS"}

    rec = None
    m = re.search(
        r"Recommandation moyenne\s+(ACHETER|ACCUMULER|CONSERVER|NEUTRE|ALL[ÉE]GER|VENDRE)",
        text,
        flags=re.I,
    )
    if m:
        rec = m.group(1).upper()

    n_analysts = _field_num(text, "Nombre d'Analystes")
    last_close = _field_num(text, "Dernier Cours de Cloture")
    target_mean = _field_num(text, "Objectif de cours Moyen")
    target_high = _field_num(text, "Objectif de cours Haut")
    target_low = _field_num(text, "Objectif de cours Bas")

    currency = ""
    cm = re.search(r"Dernier Cours de Cloture\s+-?\d[\d\s\u202f\xa0]*(?:[,.]\d+)?\s+([A-Z]{3})", text, flags=re.I)
    if cm:
        currency = cm.group(1).upper()

    price_ratio = None
    if canonical_close not in {None, 0} and last_close not in {None, 0}:
        price_ratio = float(last_close) / float(canonical_close)
        if not 0.60 <= price_ratio <= 1.40:
            return {"identity": "PRICE_MISMATCH", "price_ratio": price_ratio, "currency": currency}

    targets = [x for x in [target_low, target_mean, target_high] if x is not None]
    if any(x <= 0 for x in targets):
        target_low = target_mean = target_high = None
    if target_low is not None and target_mean is not None and target_low > target_mean:
        target_low = target_mean = target_high = None
    if target_mean is not None and target_high is not None and target_mean > target_high:
        target_low = target_mean = target_high = None

    score = RECOMMENDATION_SCORE.get(rec) if rec else None
    upside = None
    if target_mean is not None and last_close not in {None, 0}:
        upside = (float(target_mean) / float(last_close) - 1.0) * 100.0

    return {
        "identity": "VALIDATED_ISIN_PRICE" if price_ratio is not None else "VALIDATED_ISIN",
        "price_ratio": price_ratio,
        "currency": currency,
        "recommendation": rec,
        "n_analysts_v21": n_analysts,
        "consensus_score_100_v21": score,
        "target_mean_v21": target_mean,
        "target_high_v21": target_high,
        "target_low_v21": target_low,
        "zonebourse_target_upside_pct": upside,
        "zonebourse_last_close": last_close,
    }


def capture(universe: pd.DataFrame, store: CaptureStore, cfg: dict, max_symbols: int = 40) -> dict:
    prioritized = priority_frame(universe, cfg)
    old = store.facts()
    fresh: set[str] = set()
    if not old.empty:
        z = old[old["source"].astype(str).eq("ZONEBOURSE_PUBLIC_V1")].copy()
        if not z.empty:
            z["_d"] = pd.to_datetime(z["observed_at_utc"], errors="coerce", utc=True)
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
            fresh = set(z.loc[z["_d"].ge(cutoff), "isin"].astype(str))

    targets = prioritized.loc[~prioritized["isin"].astype(str).isin(fresh)].head(max_symbols).copy()
    if targets.empty:
        store.add_health("ZONEBOURSE_PUBLIC_V1", "CACHE_FRESH")
        return {"status": "CACHE_FRESH", "attempted": 0, "succeeded": 0, "facts_added": 0}

    session = requests.Session()
    headers = _headers()
    countries = set(targets.get("country", pd.Series(dtype=object)).fillna("").astype(str).str.upper())
    listings = _listing_map(session, headers, countries)

    rows: list[dict] = []
    succeeded = failed = blocked = identity_rejected = 0
    samples: list[dict] = []
    today = date.today().isoformat()

    for _, row in targets.iterrows():
        isin = str(row["isin"])
        canonical_close = number(row.get("last_close"))
        candidates = _candidate_roots(row, listings)
        if not candidates:
            candidates = [(0.0, name, root) for name, root in _search_fallback(session, headers, isin)]

        accepted = None
        tried = 0
        for score_name, candidate_name, root in candidates[:5]:
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
                if str(parsed.get("identity", "")).startswith("VALIDATED_ISIN"):
                    accepted = (url, candidate_name, score_name, parsed)
                    break
                identity_rejected += 1
            except Exception:
                continue
            finally:
                time.sleep(0.9)
        if blocked >= 3 and not accepted:
            failed += 1
            break
        if not accepted:
            failed += 1
            if len(samples) < 8:
                samples.append({"isin": isin, "name": clean_text(row.get("name")), "status": "NO_VALIDATED_PAGE", "candidates": tried})
            continue

        url, candidate_name, score_name, parsed = accepted
        observed = {
            k: v for k, v in parsed.items()
            if k not in {"identity", "price_ratio", "currency", "recommendation"} and is_observed(v)
        }
        if not observed:
            failed += 1
            continue

        succeeded += 1
        for field, value in observed.items():
            rows.append({
                "isin": isin,
                "field": field,
                "value": value if not isinstance(value, str) else "",
                "value_text": value if isinstance(value, str) else "",
                "as_of": today,
                "source": "ZONEBOURSE_PUBLIC_V1",
                "evidence": f"PUBLIC_CONSENSUS_ISIN_VALIDATED|{url}",
                "confidence": 0.88,
                "status": "OBSERVED_VALIDATED_ISIN_PRICE" if parsed.get("price_ratio") is not None else "OBSERVED_VALIDATED_ISIN",
                "observed_at_utc": utcnow(),
            })
        if parsed.get("recommendation"):
            rows.append({
                "isin": isin,
                "field": "zonebourse_recommendation_label",
                "value": "",
                "value_text": parsed["recommendation"],
                "as_of": today,
                "source": "ZONEBOURSE_PUBLIC_V1",
                "evidence": f"PUBLIC_CONSENSUS_ISIN_VALIDATED|{url}",
                "confidence": 0.88,
                "status": "OBSERVED_VALIDATED_ISIN_PRICE" if parsed.get("price_ratio") is not None else "OBSERVED_VALIDATED_ISIN",
                "observed_at_utc": utcnow(),
            })
        if len(samples) < 8:
            samples.append({
                "isin": isin,
                "candidate": candidate_name,
                "name_score": round(float(score_name), 3),
                "identity": parsed.get("identity"),
                "price_ratio": parsed.get("price_ratio"),
                "currency": parsed.get("currency"),
                "fields": sorted(observed),
            })

    added = store.upsert_facts(rows)
    status = "BLOCKED" if blocked >= 3 and not succeeded else ("OK" if succeeded else "NO_NEW_DATA")
    store.add_health(
        "ZONEBOURSE_PUBLIC_V1",
        status,
        attempted=min(len(targets), succeeded + failed),
        succeeded=succeeded,
        failed=failed,
        message=(
            f"facts_added={added}; blocked={blocked}; identity_rejected={identity_rejected}; "
            f"public_country_lists_then_ISIN_validation; samples={samples}"
        ),
    )
    return {
        "status": status,
        "attempted": min(len(targets), succeeded + failed),
        "succeeded": succeeded,
        "failed": failed,
        "blocked": blocked,
        "identity_rejected": identity_rejected,
        "facts_added": added,
        "samples": samples,
    }
