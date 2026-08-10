from __future__ import annotations

from datetime import date, timedelta
import os
import re
import time
import unicodedata

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .core import CaptureStore, clean_text, is_observed, number, priority_frame, utcnow


BASE = "https://www.boursorama.com/cours/consensus/"
MONTHS = {
    "janv": 1, "janvier": 1, "févr": 2, "fevr": 2, "février": 2, "fevrier": 2,
    "mars": 3, "avr": 4, "avril": 4, "mai": 5, "juin": 6, "juil": 7, "juillet": 7,
    "août": 8, "aout": 8, "sept": 9, "septembre": 9, "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11, "déc": 12, "dec": 12, "décembre": 12, "decembre": 12,
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _fr_num(s: str) -> float | None:
    s = _norm(s).replace("\u202f", "").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return number(m.group(0)) if m else None


def _xpar_symbol(row: pd.Series) -> str:
    mic = clean_text(row.get("euronext_mic")).upper()
    symbol = clean_text(row.get("euronext_symbol")).upper()
    yt = clean_text(row.get("yahoo_ticker")).upper()
    if mic == "XPAR" and symbol:
        return symbol
    if yt.endswith(".PA"):
        return yt[:-3]
    return ""


def _five_after(text: str, label: str) -> list[float]:
    m = re.search(re.escape(label) + r"\s+(.{0,260})", text, flags=re.I | re.S)
    if not m:
        return []
    vals = re.findall(r"-?\d+(?:[,.]\d+)?", m.group(1))
    out = []
    for x in vals[:5]:
        v = _fr_num(x)
        if v is not None:
            out.append(v)
    return out


def _next_earnings(text: str) -> str:
    today = date.today()
    pat = re.compile(
        r"(\d{1,2})\s+(janv(?:ier)?|févr(?:ier)?|fevr(?:ier)?|mars|avr(?:il)?|mai|juin|"
        r"juil(?:let)?|août|aout|sept(?:embre)?|oct(?:obre)?|nov(?:embre)?|déc(?:embre)?|dec(?:embre)?)"
        r"(?:\s+\d{1,2}:\d{2})?\s+Résultats",
        flags=re.I,
    )
    for day_s, month_s in pat.findall(text):
        key = month_s.lower().rstrip(".")
        month = MONTHS.get(key)
        if not month:
            continue
        year = today.year
        try:
            d = date(year, month, int(day_s))
        except ValueError:
            continue
        if d < today - timedelta(days=3):
            d = date(year + 1, month, int(day_s))
        if d >= today - timedelta(days=3):
            return d.isoformat()
    return ""


def _parse(html: str) -> dict:
    text = _norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    if "Recommandations des analystes professionnels" not in text and "Consensus des analystes" not in text:
        return {}

    analysts = _five_after(text, "Nombre d'analystes")
    notes = _five_after(text, "Note médiane")
    targets = _five_after(text, "Historique des objectifs de cours médian")

    per = None
    m = re.search(r"PER estimé\s+20\d{2}.{0,220}?(-?\d+(?:[,.]\d+)?)", text, flags=re.I | re.S)
    if m:
        per = _fr_num(m.group(1))

    current_note = notes[-1] if notes else None
    note_1m = notes[2] if len(notes) >= 5 else (notes[-2] if len(notes) >= 2 else None)
    current_analysts = analysts[-1] if analysts else None
    current_target_median = targets[-1] if targets else None
    target_1m = targets[2] if len(targets) >= 5 else (targets[-2] if len(targets) >= 2 else None)

    score = None if current_note is None else max(0.0, min(100.0, (5.0 - current_note) / 4.0 * 100.0))
    score_1m = None if note_1m is None else max(0.0, min(100.0, (5.0 - note_1m) / 4.0 * 100.0))
    delta_4w = None if score is None or score_1m is None else score - score_1m
    target_revision = None
    if current_target_median not in {None, 0} and target_1m not in {None, 0}:
        target_revision = (current_target_median / target_1m - 1.0) * 100.0

    return {
        "n_analysts_v21": current_analysts,
        "consensus_score_100_v21": score,
        "consensus_delta_4w": delta_4w,
        "per_forward_v21": per,
        "boursorama_target_median": current_target_median,
        "boursorama_target_median_1m": target_1m,
        "boursorama_target_revision_1m_pct": target_revision,
        "next_earnings_date": _next_earnings(text),
    }


def capture(universe: pd.DataFrame, store: CaptureStore, cfg: dict, max_symbols: int = 40) -> dict:
    prioritized = priority_frame(universe, cfg)
    facts_old = store.facts()
    fresh = set()
    if not facts_old.empty:
        b = facts_old[facts_old["source"].astype(str).eq("BOURSORAMA_PUBLIC")].copy()
        if not b.empty:
            b["_d"] = pd.to_datetime(b["observed_at_utc"], errors="coerce", utc=True)
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
            fresh = set(b.loc[b["_d"].ge(cutoff), "isin"].astype(str))

    targets = []
    for _, row in prioritized.iterrows():
        isin = str(row["isin"])
        if isin in fresh:
            continue
        symbol = _xpar_symbol(row)
        if symbol:
            targets.append((row, symbol))
        if len(targets) >= max_symbols:
            break

    session = requests.Session()
    headers = {
        "User-Agent": os.getenv("V182_USER_AGENT", "PEA-V21.1-FreeCapture/1.0"),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    }
    rows: list[dict] = []
    succeeded = 0
    failed = 0
    blocked = 0
    samples: list[dict] = []
    today = date.today().isoformat()

    for row, symbol in targets:
        isin = str(row["isin"])
        url = f"{BASE}1rP{symbol}/"
        try:
            r = session.get(url, headers=headers, timeout=25)
            if r.status_code in {401, 403, 429}:
                blocked += 1
                failed += 1
                if len(samples) < 5:
                    samples.append({"isin": isin, "symbol": symbol, "status": r.status_code})
                if blocked >= 3:
                    break
                continue
            if not r.ok:
                failed += 1
                continue
            parsed = _parse(r.text)
            observed = {k: v for k, v in parsed.items() if is_observed(v)}
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
                    "source": "BOURSORAMA_PUBLIC",
                    "evidence": "PUBLIC_PAGE_FACTSET_ATTRIBUTED",
                    "confidence": 0.82,
                    "status": "OBSERVED",
                    "observed_at_utc": utcnow(),
                })
            if len(samples) < 5:
                samples.append({"isin": isin, "symbol": symbol, "fields": sorted(observed)})
        except Exception as exc:
            failed += 1
            if len(samples) < 5:
                samples.append({"isin": isin, "symbol": symbol, "error": f"{type(exc).__name__}:{str(exc)[:120]}"})
        time.sleep(1.25)

    added = store.upsert_facts(rows)
    status = "BLOCKED" if blocked >= 3 and not succeeded else ("OK" if succeeded else "NO_NEW_DATA")
    store.add_health(
        "BOURSORAMA_PUBLIC", status, len(targets), succeeded, failed,
        message=f"facts_added={added}; blocked={blocked}; targeted_XPAR_only; samples={samples}"
    )
    return {
        "status": status,
        "attempted": min(len(targets), succeeded + failed),
        "succeeded": succeeded,
        "failed": failed,
        "blocked": blocked,
        "facts_added": added,
        "samples": samples,
    }
