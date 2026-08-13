from __future__ import annotations

from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
import re
import unicodedata

import pandas as pd
from bs4 import BeautifulSoup

SOURCE = "Boursorama"


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _canonical_url(soup: BeautifulSoup) -> str:
    node = soup.find("link", attrs={"rel": "canonical"})
    if node and node.get("href"):
        return str(node.get("href"))
    node = soup.find("meta", attrs={"property": "og:url"})
    return str(node.get("content")) if node and node.get("content") else ""


def _company_aliases(actions: pd.DataFrame) -> dict[str, str]:
    """Return unique exact/legally-suffixed company aliases; never fuzzy match."""
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    suffixes = {"sa", "se", "nv", "ag", "spa", "plc", "sas", "s a", "s e", "n v"}
    if "isin" not in actions.columns or "name" not in actions.columns:
        return result
    for _, row in actions.iterrows():
        isin = str(row.get("isin") or "").strip()
        name = _norm(row.get("name"))
        if not isin or not name:
            continue
        aliases = {name}
        tokens = name.split()
        if tokens and tokens[-1] in suffixes:
            aliases.add(" ".join(tokens[:-1]))
        for alias in aliases:
            if not alias:
                continue
            if alias in result and result[alias] != isin:
                duplicates.add(alias)
            else:
                result[alias] = isin
    for alias in duplicates:
        result.pop(alias, None)
    return result


def _tables(html: str) -> list[tuple[BeautifulSoup, pd.DataFrame]]:
    soup = BeautifulSoup(html, "lxml")
    result = []
    for table in soup.find_all("table"):
        try:
            frame = pd.read_html(StringIO(str(table)), decimal=",", thousands=" ")[0]
        except (ValueError, ImportError, IndexError):
            continue
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = [" | ".join(str(v).strip() for v in col if str(v).strip() and not str(v).startswith("Unnamed")) for col in frame.columns]
        else:
            frame.columns = [str(c).strip() for c in frame.columns]
        result.append((table, frame))
    return result


def _col(frame: pd.DataFrame, *names: str) -> str | None:
    for col in frame.columns:
        normalized = _norm(col)
        if any(_norm(name) == normalized or _norm(name) in normalized for name in names):
            return str(col)
    return None


def _parse_day_month(text: str, today: date) -> date | None:
    normalized = _norm(text)
    months = {
        "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    }
    match = re.search(r"\b(\d{1,2})\s+(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)(?:\s+(\d{4}))?\b", normalized)
    if not match:
        return None
    day = int(match.group(1)); month = months[match.group(2)]
    year = int(match.group(3)) if match.group(3) else today.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if not match.group(3):
        if candidate < today and (today - candidate).days > 180:
            candidate = date(year + 1, month, day)
        elif candidate > today and (candidate - today).days > 250:
            candidate = date(year - 1, month, day)
    return candidate


def _table_date(table, today: date) -> date | None:
    # Date tabs/headings often sit outside the HTML table. Search nearby
    # previous text nodes only; do not infer from current clock if absent.
    for raw in table.find_all_previous(string=True, limit=50):
        parsed = _parse_day_month(str(raw), today)
        if parsed:
            return parsed
    return None


def _event_class(value: str) -> str:
    text = _norm(value)
    if "resultat" in text:
        return "RESULTS"
    if "chiffre d affaires" in text:
        return "REVENUE"
    if "reunion d analystes" in text or "reunion investisseurs" in text or "road show" in text:
        return "ANALYST_INVESTOR_MEETING"
    if "assemblee generale" in text:
        return "GENERAL_MEETING"
    if "dividende" in text:
        return "DIVIDEND"
    if "rapport" in text:
        return "REPORT"
    return "OTHER"


def _obs(isin: str, field: str, value, *, url: str, source_file: str, as_of: str) -> dict:
    return {
        "universe": "ACTION",
        "isin": isin,
        "field": field,
        "value": value,
        "source": SOURCE,
        "source_url": url,
        "source_file": source_file,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "evidence_level": "B",
        "validation_status": "ATTRIBUTED",
    }


def parse_company_calendar_html(
    html: str,
    actions: pd.DataFrame,
    source_file: str = "",
    *,
    today: date | None = None,
) -> tuple[list[dict], list[dict], dict]:
    now = today or datetime.now(timezone.utc).date()
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/bourse/actualites/calendriers/societes-cotees" not in url and "/bourse/actualites/calendriers/" not in url:
        return [], [], {"matched_rows": 0, "not_company_calendar": True}
    aliases = _company_aliases(actions)
    events: dict[str, list[tuple[date, str, str]]] = {}
    failures: list[dict] = []
    table_count = 0
    for table, frame in _tables(html):
        company_col = _col(frame, "société", "societe")
        event_col = _col(frame, "évènement", "evenement", "événement")
        time_col = _col(frame, "heure")
        if not company_col or not event_col:
            continue
        event_date = _table_date(table, now)
        if event_date is None:
            failures.append({"source": SOURCE, "source_file": source_file, "reason": "COMPANY_CALENDAR_TABLE_DATE_NOT_OBSERVED"})
            continue
        table_count += 1
        for _, row in frame.iterrows():
            company = _norm(row.get(company_col))
            isin = aliases.get(company, "")
            if not isin:
                continue
            event = str(row.get(event_col) or "").strip()
            if not event or event.casefold() == "nan":
                continue
            time_value = str(row.get(time_col) or "").strip() if time_col else ""
            if time_value.casefold() == "nan":
                time_value = ""
            events.setdefault(isin, []).append((event_date, time_value, event))

    observations: list[dict] = []
    matched = 0
    upcoming_total = 0
    for isin, rows in events.items():
        rows.sort(key=lambda x: (x[0], x[1]))
        future = [row for row in rows if row[0] >= now]
        if not future:
            continue
        matched += 1
        upcoming_total += len(future)
        event_date, event_time, event = future[0]
        days = (event_date - now).days
        fields: dict[str, object] = {
            "boursorama_next_corporate_event_date": event_date.isoformat(),
            "boursorama_days_to_corporate_event": days,
            "boursorama_next_corporate_event_type": event,
            "boursorama_next_corporate_event_class": _event_class(event),
            "boursorama_corporate_events_visible_count": len(future),
            "boursorama_corporate_event_within_7d_flag": 1.0 if days <= 7 else 0.0,
            "boursorama_corporate_event_within_30d_flag": 1.0 if days <= 30 else 0.0,
        }
        if event_time:
            fields["boursorama_next_corporate_event_time"] = event_time
        for field, value in fields.items():
            observations.append(_obs(isin, field, value, url=url, source_file=source_file, as_of=now.isoformat()))
    return observations, failures, {
        "tables": table_count,
        "matched_rows": matched,
        "upcoming_events": upcoming_total,
        "observations": len(observations),
        "source_url": url,
    }


def load_company_calendar_pages(
    root: Path,
    actions: pd.DataFrame,
    relative_root: str = "inputs/boursorama_snapshots",
) -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    if not directory.exists():
        return [], [], {"files": 0, "matched_rows": 0, "observations": 0}
    observations: list[dict] = []
    failures: list[dict] = []
    files = 0
    matched = 0
    upcoming = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        if "societes-cotees" not in html.casefold() and "sociétés cotées" not in html.casefold() and "societes cotees" not in html.casefold():
            continue
        obs, failed, stats = parse_company_calendar_html(html, actions, str(path))
        if stats.get("not_company_calendar"):
            continue
        files += 1
        matched += int(stats.get("matched_rows", 0))
        upcoming += int(stats.get("upcoming_events", 0))
        observations.extend(obs)
        failures.extend(failed)
    return observations, failures, {
        "files": files,
        "matched_rows": matched,
        "upcoming_events": upcoming,
        "observations": len(observations),
        "failures": len(failures),
    }
