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
SOURCE = "BOURSORAMA_PUBLIC_V2"
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


def _row_values(soup: BeautifulSoup, label: str, expected: int | None = None) -> list[float]:
    label_l = _norm(label).lower()
    for tr in soup.find_all("tr"):
        cells = [_norm(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if not cells or label_l not in cells[0].lower():
            continue
        vals = []
        for cell in cells[1:]:
            v = _fr_num(cell)
            if v is not None:
                vals.append(v)
        if expected is None or len(vals) >= expected:
            return vals[:expected] if expected else vals
    return []


def _five_from_text(text: str, label: str) -> list[float]:
    # Fallback for pages whose responsive HTML does not expose normal table rows.
    stop_labels = ["Nombre d'analystes", "Note médiane", "Historique des objectifs", "Potentiel", "Liste des cabinets"]
    stop = "|".join(re.escape(x) for x in stop_labels if x.lower() != label.lower())
    m = re.search(re.escape(label) + rf"\s+(.+?)(?={stop}|$)", text, flags=re.I | re.S)
    if not m:
        return []
    vals = []
    for token in re.findall(r"-?\d+(?:[,.]\d+)?", m.group(1)):
        v = _fr_num(token)
        if v is not None:
            vals.append(v)
    return vals[:5]


def _series5(soup: BeautifulSoup, text: str, label: str) -> list[float]:
    vals = _row_values(soup, label, expected=5)
    return vals if len(vals) == 5 else _five_from_text(text, label)


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


def _label_value(text: str, label_regex: str) -> float | None:
    # Boursorama inserts help text between the label and value. Bound the scan so a year
    # or unrelated page number cannot be mistaken for the requested metric.
    m = re.search(label_regex + r"(.{0,180})", text, flags=re.I | re.S)
    if not m:
        return None
    block = m.group(1)
    # Prefer the last numeric token in the short block because help headings can include years.
    vals = [_fr_num(x) for x in re.findall(r"-?\d+(?:[,.]\d+)?", block)]
    vals = [x for x in vals if x is not None]
    return vals[-1] if vals else None


def _forecast_rows(soup: BeautifulSoup) -> dict:
    """Capture public forecast table values without mapping them into historical canonical fields."""
    out: dict[str, float] = {}
    for tr in soup.find_all("tr"):
        cells = [_norm(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if len(cells) < 4:
            continue
        label = cells[0].lower()
        nums = [_fr_num(c) for c in cells[1:4]]
        if any(v is None for v in nums):
            continue
        if "bénéfice net par action" in label or "benefice net par action" in label:
            out["boursorama_eps_prev_year"] = nums[0]
            out["boursorama_eps_est_current_year"] = nums[1]
            out["boursorama_eps_est_next_year"] = nums[2]
        elif label == "per" or label.startswith("per "):
            out["boursorama_per_prev_year"] = nums[0]
            out["boursorama_per_est_current_year"] = nums[1]
            out["boursorama_per_est_next_year"] = nums[2]
        elif "chiffre d'affaires" in label:
            out["boursorama_revenue_prev_year_m"] = nums[0]
            out["boursorama_revenue_est_current_year_m"] = nums[1]
            out["boursorama_revenue_est_next_year_m"] = nums[2]
        elif label.startswith("ebit ") or label == "ebit":
            out["boursorama_ebit_prev_year_m"] = nums[0]
            out["boursorama_ebit_est_current_year_m"] = nums[1]
            out["boursorama_ebit_est_next_year_m"] = nums[2]
        elif label.startswith("dividende"):
            out["boursorama_dividend_prev_year"] = nums[0]
            out["boursorama_dividend_est_current_year"] = nums[1]
            out["boursorama_dividend_est_next_year"] = nums[2]
    # Derived forward growth is stored under explicit Boursorama names only.
    for prefix in ["eps", "revenue", "ebit", "dividend"]:
        a = out.get(f"boursorama_{prefix}_prev_year" if prefix != "revenue" and prefix != "ebit" else f"boursorama_{prefix}_prev_year_m")
        b = out.get(f"boursorama_{prefix}_est_current_year" if prefix != "revenue" and prefix != "ebit" else f"boursorama_{prefix}_est_current_year_m")
        if a not in {None, 0} and b is not None:
            out[f"boursorama_{prefix}_forecast_growth_current_pct"] = (b / abs(a) - 1.0) * 100.0
    return out


def _parse(html: str, expected_isin: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = _norm(soup.get_text(" ", strip=True))
    if expected_isin not in text:
        return {"_identity_error": "ISIN_NOT_ON_PAGE"}
    if "Recommandations des analystes professionnels" not in text and "Consensus des analystes" not in text:
        return {}

    analysts = _series5(soup, text, "Nombre d'analystes")
    notes = _series5(soup, text, "Note médiane")
    targets = _series5(soup, text, "Historique des objectifs de cours médian")

    per = _label_value(text, r"PER estimé\s+20\d{2}")
    if per is not None and not (0.0 < per <= 250.0):
        per = None

    current_note = notes[-1] if len(notes) == 5 else None
    note_1m = notes[2] if len(notes) == 5 else None
    current_analysts = analysts[-1] if len(analysts) == 5 else None
    current_target_median = targets[-1] if len(targets) == 5 and targets[-1] > 0 else None
    target_1m = targets[2] if len(targets) == 5 and targets[2] > 0 else None

    score = None if current_note is None or not (1.0 <= current_note <= 5.0) else (5.0 - current_note) / 4.0 * 100.0
    score_1m = None if note_1m is None or not (1.0 <= note_1m <= 5.0) else (5.0 - note_1m) / 4.0 * 100.0
    delta_4w = None if score is None or score_1m is None else score - score_1m
    target_revision = None
    if current_target_median not in {None, 0} and target_1m not in {None, 0}:
        target_revision = (current_target_median / target_1m - 1.0) * 100.0

    out = {
        "n_analysts_v21": current_analysts if current_analysts is not None and 0 <= current_analysts <= 100 else None,
        "consensus_score_100_v21": score,
        "consensus_delta_4w": delta_4w,
        "per_forward_v21": per,
        "boursorama_target_median": current_target_median,
        "boursorama_target_median_1m": target_1m,
        "boursorama_target_revision_1m_pct": target_revision,
        "next_earnings_date": _next_earnings(text),
    }
    out.update(_forecast_rows(soup))
    return out


def capture(universe: pd.DataFrame, store: CaptureStore, cfg: dict, max_symbols: int = 40) -> dict:
    prioritized = priority_frame(universe, cfg)
    facts_old = store.facts()
    fresh = set()
    if not facts_old.empty:
        b = facts_old[facts_old["source"].astype(str).eq(SOURCE)].copy()
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
    identity_rejected = 0
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
            parsed = _parse(r.text, isin)
            if parsed.pop("_identity_error", None):
                identity_rejected += 1
                failed += 1
                continue
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
                    "source": SOURCE,
                    "evidence": "PUBLIC_PAGE_FACTSET_ATTRIBUTED",
                    "confidence": 0.84,
                    "status": "OBSERVED_VALIDATED_ISIN",
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
        SOURCE, status, len(targets), succeeded, failed,
        message=(f"facts_added={added}; blocked={blocked}; identity_rejected={identity_rejected}; "
                 f"targeted_XPAR_only; samples={samples}")
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
