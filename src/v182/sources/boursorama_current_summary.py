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


def _summary_value(text: str, label: str, *, percent: bool = False) -> float | None:
    """Read a Boursorama summary metric after its optional fiscal year label."""
    suffix = r"\s*%" if percent else ""
    pattern = rf"{label}\s+estim[ée](?:\s+\d{{4}})?[^0-9]{{0,160}}([0-9]+(?:[,.][0-9]+)?){suffix}"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return _num(match.group(1)) if match else None


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

    per = _summary_value(text, "PER")
    if per is not None:
        fields["boursorama_per_forward_current"] = per
        fields["per_forward_v21"] = per

    dividend_yield = _summary_value(text, "rendement", percent=True)
    if dividend_yield is not None:
        fields["boursorama_dividend_yield_forward_current_pct"] = dividend_yield
        fields["dividend_yield_v21_pct"] = dividend_yield

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
        failures.extend(f for f in failed if f.get("reason") != "ACTION_ISIN_NOT_UNIQUE")
    return observations, failures, {"files": files, "observations": len(observations), "failures": len(failures)}
