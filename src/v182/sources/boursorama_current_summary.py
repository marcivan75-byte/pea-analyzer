from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from bs4 import BeautifulSoup

SOURCE = "Boursorama/FactSet"


def _num(value: str) -> float | None:
    match = re.search(r"[-+]?\d[\d\s]*(?:[,.]\d+)?", str(value or ""))
    if not match:
        return None
    token = match.group(0).replace(" ", "").replace("\u202f", "").replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def _canonical(soup: BeautifulSoup) -> str:
    node = soup.find("link", attrs={"rel": "canonical"})
    if node and node.get("href"):
        return str(node.get("href"))
    node = soup.find("meta", attrs={"property": "og:url"})
    return str(node.get("content")) if node and node.get("content") else ""


def _date_iso(text: str) -> str:
    matches = re.findall(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text)
    dates = []
    for day, month, year in matches:
        year = f"20{year}" if len(year) == 2 else year
        try:
            dates.append(datetime(int(year), int(month), int(day)).date().isoformat())
        except ValueError:
            continue
    return max(dates) if dates else datetime.now(timezone.utc).date().isoformat()


def parse_current_summary(html: str, canonical_action_isins: set[str], source_file: str = "") -> tuple[list[dict], list[dict]]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    url = _canonical(soup)
    if "boursorama.com" not in url:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "BOURSORAMA_SOURCE_URL_MISSING"}]
    found = list(dict.fromkeys(i for i in re.findall(r"\b[A-Z]{2}[A-Z0-9]{10}\b", text) if i in canonical_action_isins))
    if len(found) != 1:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "ACTION_ISIN_NOT_UNIQUE", "matches": len(found)}]
    isin = found[0]
    as_of = _date_iso(text)
    fields: dict[str, float] = {}

    per = re.search(r"PER\s+estim[ée][^0-9]{0,80}([0-9]+(?:[,.][0-9]+)?)", text, flags=re.IGNORECASE)
    if per:
        value = _num(per.group(1))
        if value is not None:
            fields["boursorama_per_forward_current"] = value
            fields["per_forward_v21"] = value

    yield_match = re.search(r"rendement\s+estim[ée][^0-9]{0,80}([0-9]+(?:[,.][0-9]+)?)\s*%", text, flags=re.IGNORECASE)
    if yield_match:
        value = _num(yield_match.group(1))
        if value is not None:
            fields["boursorama_dividend_yield_forward_current_pct"] = value
            fields["dividend_yield_v21_pct"] = value

    observations = [{
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
    } for field, value in fields.items()]
    return observations, []


def load_current_summaries(root: Path, actions, relative_root: str = "inputs/boursorama_snapshots") -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    canonical = set(actions["isin"].astype(str).str.strip()) if "isin" in actions.columns else set()
    observations: list[dict] = []
    failures: list[dict] = []
    files = 0
    if not directory.exists():
        return [], [], {"files": 0, "observations": 0}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        files += 1
        html = path.read_text(encoding="utf-8", errors="replace")
        obs, failed = parse_current_summary(html, canonical, str(path))
        observations.extend(obs)
        # ETF HTML naturally has no canonical Action ISIN; do not report that as
        # a failure in this Action-summary supplemental pass.
        failures.extend(f for f in failed if f.get("reason") != "ACTION_ISIN_NOT_UNIQUE")
    return observations, failures, {"files": files, "observations": len(observations), "failures": len(failures)}
