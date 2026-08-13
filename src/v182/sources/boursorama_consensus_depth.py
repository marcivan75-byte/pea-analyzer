from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
import unicodedata

import pandas as pd
from bs4 import BeautifulSoup

SOURCE = "Boursorama/FactSet"


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    match = re.search(r"[-+]?\d+(?:[,.]\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _canonical_url(soup: BeautifulSoup) -> str:
    node = soup.find("link", attrs={"rel": "canonical"})
    if node and node.get("href"):
        return str(node.get("href"))
    node = soup.find("meta", attrs={"property": "og:url"})
    return str(node.get("content")) if node and node.get("content") else ""


def _tables(html: str) -> list[pd.DataFrame]:
    try:
        frames = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return []
    result = []
    for frame in frames:
        f = frame.copy()
        if isinstance(f.columns, pd.MultiIndex):
            f.columns = [" | ".join(str(v).strip() for v in col if str(v).strip() and not str(v).startswith("Unnamed")) for col in f.columns]
        else:
            f.columns = [str(c).strip() for c in f.columns]
        result.append(f)
    return result


def _latest(frame: pd.DataFrame, *aliases: str) -> float | None:
    for _, row in frame.iterrows():
        label = _norm(row.iloc[0] if len(row) else "")
        if any(label.startswith(_norm(alias)) for alias in aliases):
            values = [_num(v) for v in row.iloc[1:]]
            values = [v for v in values if v is not None]
            return values[-1] if values else None
    return None


def _as_of(text: str) -> str:
    matches = re.findall(r"(?:le|au)\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text, flags=re.I)
    dates = []
    for raw in matches:
        for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y"):
            try:
                dates.append(datetime.strptime(raw, fmt).date().isoformat())
                break
            except ValueError:
                continue
    return max(dates) if dates else datetime.now(timezone.utc).date().isoformat()


def _obs(isin: str, field: str, value, url: str, source_file: str, as_of: str) -> dict:
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


def parse_consensus_depth_html(
    html: str,
    canonical_action_isins: set[str],
    source_file: str = "",
) -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/cours/consensus/" not in url:
        return [], [], {"matched_rows": 0, "not_consensus": True}
    text = soup.get_text("\n", strip=True)
    found = list(dict.fromkeys(i for i in re.findall(r"\b[A-Z]{2}[A-Z0-9]{10}\b", text) if i in canonical_action_isins))
    if len(found) != 1:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "CONSENSUS_DEPTH_ISIN_NOT_UNIQUE"}], {"matched_rows": 0}
    isin = found[0]
    fields: dict[str, object] = {}
    for frame in _tables(html):
        analysts = _latest(frame, "Nombre d'analystes")
        buy = _latest(frame, "1. Acheter", "Acheter")
        reinforce = _latest(frame, "2. Renforcer", "Renforcer")
        hold = _latest(frame, "3. Conserver", "Conserver")
        reduce = _latest(frame, "4. Alléger", "4. Alleger", "Alléger", "Alleger")
        sell = _latest(frame, "5. Vendre", "Vendre")
        if analysts is None or all(v is None for v in (buy, reinforce, hold, reduce, sell)):
            continue
        values = [0.0 if v is None else float(v) for v in (buy, reinforce, hold, reduce, sell)]
        bullish = values[0] + values[1]
        neutral = values[2]
        bearish = values[3] + values[4]
        total = float(analysts) if analysts and analysts > 0 else bullish + neutral + bearish
        fields["boursorama_consensus_bullish_count"] = int(round(bullish))
        fields["boursorama_consensus_neutral_count"] = int(round(neutral))
        fields["boursorama_consensus_bearish_count"] = int(round(bearish))
        if total > 0:
            fields["boursorama_consensus_bullish_pct"] = round(bullish / total * 100.0, 4)
            fields["boursorama_consensus_neutral_pct"] = round(neutral / total * 100.0, 4)
            fields["boursorama_consensus_bearish_pct"] = round(bearish / total * 100.0, 4)
            fields["boursorama_consensus_net_bullish_balance_pct"] = round((bullish - bearish) / total * 100.0, 4)
        break

    firms = re.search(
        r"Liste\s+des\s+cabinets\s+d['’]analystes\s+ayant\s+suivi\s+la\s+valeur\s+au\s+moins\s+une\s+fois\s+dans\s+l['’]ann[ée]e\s*:\s*(.*?)\s*(?:NB\s*:|Note\s+m[ée]diane|Le\s+pr[ée]sent\s+consensus|Copyright)",
        text,
        flags=re.I | re.S,
    )
    if firms:
        raw = re.sub(r"\s+", " ", firms.group(1)).strip(" ,;\n")
        if raw:
            # Preserve the publisher's raw firm list. Do not split on commas:
            # some legal broker names themselves contain commas.
            fields["boursorama_analyst_firms_list_raw"] = raw
            fields["boursorama_analyst_firms_list_observed_flag"] = 1.0
    if re.search(r"certains\s+bureaux\s+d['’]analyses\s+ont\s+souhait[ée]\s+conserver\s+l['’]anonymat", text, flags=re.I):
        fields["boursorama_analyst_firms_anonymity_warning"] = True

    as_of = _as_of(text)
    observations = [_obs(isin, field, value, url, source_file, as_of) for field, value in fields.items()]
    return observations, [], {"matched_rows": 1, "observations": len(observations), "isin": isin, "source_url": url}


def load_consensus_depth_pages(
    root: Path,
    actions: pd.DataFrame,
    relative_root: str = "inputs/boursorama_snapshots",
) -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    canonical = set(actions["isin"].astype(str).str.strip()) if "isin" in actions.columns else set()
    if not directory.exists():
        return [], [], {"files": 0, "matched_rows": 0, "observations": 0}
    observations: list[dict] = []
    failures: list[dict] = []
    files = 0
    matched = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        if "/cours/consensus/" not in html:
            continue
        obs, failed, stats = parse_consensus_depth_html(html, canonical, str(path))
        if stats.get("not_consensus"):
            continue
        files += 1
        matched += int(stats.get("matched_rows", 0))
        observations.extend(obs)
        failures.extend(failed)
    return observations, failures, {
        "files": files,
        "matched_rows": matched,
        "observations": len(observations),
        "failures": len(failures),
    }
