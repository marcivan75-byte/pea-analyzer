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
    text = re.sub(r"\bsrd\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    if not text or "atteint" in text.casefold() or text in {"-", "—"}:
        return None
    match = re.search(r"[-+]?\d[\d .]*(?:,\d+|\.\d+)?", text)
    if not match:
        return None
    token = match.group(0).replace(" ", "")
    if "," in token and "." in token:
        token = token.replace(".", "").replace(",", ".")
    elif "," in token:
        token = token.replace(",", ".")
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


def _updated_date(text: str) -> str:
    match = re.search(r"Mis\s+[àa]\s+jour\s+le\s+(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text, flags=re.IGNORECASE)
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        try:
            return datetime(int(year), int(month), int(day)).date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _flatten(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        cols = []
        for col in out.columns:
            parts = [str(v).strip() for v in col if str(v).strip() and not str(v).startswith("Unnamed")]
            cols.append(" | ".join(dict.fromkeys(parts)))
        out.columns = cols
    else:
        out.columns = [str(c).strip() for c in out.columns]
    return out


def _col(frame: pd.DataFrame, *candidates: str) -> str | None:
    normalized = {_norm(c): str(c) for c in frame.columns}
    for candidate in candidates:
        needle = _norm(candidate)
        for normed, original in normalized.items():
            if needle == normed or needle in normed:
                return original
    return None


def _master_aliases(actions: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    name_map: dict[str, str] = {}
    name_dupes: set[str] = set()
    ticker_map: dict[str, str] = {}
    for _, row in actions.iterrows():
        isin = str(row.get("isin") or "").strip()
        name = _norm(row.get("name"))
        ticker = str(row.get("yahoo_ticker") or "").strip().upper()
        if not isin:
            continue
        aliases = {name}
        tokens = name.split()
        if tokens and tokens[-1] in {"sa", "se", "nv", "plc", "ag", "spa"}:
            aliases.add(" ".join(tokens[:-1]))
        for alias in {a for a in aliases if a}:
            if alias in name_map and name_map[alias] != isin:
                name_dupes.add(alias)
            else:
                name_map[alias] = isin
        if ticker:
            ticker_map[ticker] = isin
    for alias in name_dupes:
        name_map.pop(alias, None)
    return name_map, ticker_map


def _link_ticker_map(soup: BeautifulSoup) -> dict[str, str]:
    """Map visible Boursorama row label to a conservative Euronext Paris ticker.

    Only the explicit 1rP code form is converted to `.PA`; other market codes
    are deliberately left to exact-name matching to avoid inventing tickers.
    """
    result = {}
    for anchor in soup.find_all("a", href=True):
        label = _norm(anchor.get_text(" ", strip=True))
        href = str(anchor.get("href") or "")
        match = re.search(r"/cours/(?:consensus/)?1rP([^/?]+)/?", href, flags=re.IGNORECASE)
        if label and match:
            result[label] = f"{match.group(1).upper()}.PA"
    return result


def _observation(isin: str, field: str, value, as_of: str, source_url: str, source_file: str) -> dict:
    return {
        "universe": "ACTION",
        "isin": isin,
        "field": field,
        "value": value,
        "source": SOURCE,
        "source_url": source_url,
        "source_file": source_file,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "evidence_level": "B",
        "validation_status": "ATTRIBUTED",
    }


def parse_consensus_bulk_html(html: str, actions: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    source_url = _canonical(soup)
    text = soup.get_text("\n", strip=True)
    if "boursorama.com" not in source_url:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "BOURSORAMA_SOURCE_URL_MISSING"}], {"matched_rows": 0}
    if "recommandations" not in source_url.casefold() and "consensus" not in text.casefold():
        return [], [], {"matched_rows": 0, "not_a_consensus_bulk_page": True}
    try:
        # Boursorama publishes French decimal commas. Explicit parsing avoids
        # destructive conversions such as 224,043 -> 224043.
        tables = [_flatten(frame) for frame in pd.read_html(StringIO(html), decimal=",", thousands=" ")]
    except (ValueError, ImportError):
        tables = []
    name_map, ticker_map = _master_aliases(actions)
    link_tickers = _link_ticker_map(soup)
    as_of = _updated_date(text)
    observations: list[dict] = []
    failures: list[dict] = []
    matched_isins: set[str] = set()
    rows_seen = 0

    for frame in tables:
        name_col = _col(frame, "Libellé", "Libelle")
        reco_col = _col(frame, "Reco")
        last_col = _col(frame, "Der. Cours", "Dernier Cours")
        target_col = _col(frame, "Obj. Cours", "Objectif Cours")
        upside_col = _col(frame, "Potentiel")
        analysts_col = _col(frame, "Nb. Analystes", "Nb Analystes")
        eps_col = _col(frame, "Bna. 2026", "Bna 2026")
        yield_col = _col(frame, "Rend. 2026", "Rend 2026")
        per_fwd_col = _col(frame, "Per. 2026", "Per 2026")
        per_reported_col = _col(frame, "Per. 2025", "Per 2025")
        if not name_col or not (target_col and upside_col and analysts_col and per_fwd_col):
            continue
        for _, row in frame.iterrows():
            rows_seen += 1
            raw_name = str(row.get(name_col) or "").strip()
            key = _norm(raw_name)
            isin = ""
            ticker = link_tickers.get(key, "")
            if ticker:
                isin = ticker_map.get(ticker, "")
            if not isin:
                isin = name_map.get(key, "")
            if not isin:
                failures.append({"source": SOURCE, "source_file": source_file, "name": raw_name, "reason": "BULK_NAME_OR_TICKER_NOT_MATCHED"})
                continue
            matched_isins.add(isin)
            fields: dict[str, object] = {}
            if reco_col:
                reco = str(row.get(reco_col) or "").strip()
                if reco and reco.casefold() != "nan":
                    fields["boursorama_bulk_recommendation"] = reco
            for col, field, canonical_field in (
                (last_col, "boursorama_reference_close", None),
                (target_col, "boursorama_target_price", None),
                (upside_col, "boursorama_target_upside_pct", "target_upside_pct_v21"),
                (analysts_col, "boursorama_consensus_analysts", None),
                (eps_col, "boursorama_eps_forward_1y", None),
                (yield_col, "boursorama_dividend_yield_forward_current_pct", "dividend_yield_v21_pct"),
                (per_fwd_col, "boursorama_per_forward_current", "per_forward_v21"),
                (per_reported_col, "boursorama_per_reported", None),
            ):
                if not col:
                    continue
                value = _num(row.get(col))
                if value is None:
                    continue
                fields[field] = value
                if canonical_field:
                    fields[canonical_field] = value
            for field, value in fields.items():
                observations.append(_observation(isin, field, value, as_of, source_url, source_file))

    return observations, failures, {
        "matched_rows": len(matched_isins),
        "rows_seen": rows_seen,
        "observations": len(observations),
        "as_of": as_of,
        "source_url": source_url,
    }


def load_bulk_consensus_pages(root: Path, actions: pd.DataFrame, relative_root: str = "inputs/boursorama_snapshots") -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    if not directory.exists():
        return [], [], {"files": 0, "matched_rows": 0, "observations": 0}
    observations: list[dict] = []
    failures: list[dict] = []
    files = 0
    matched_rows = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        lower = html.casefold()
        if "nb. analyst" not in lower and "nb analyst" not in lower and "obj. cours" not in lower:
            continue
        obs, failed, stats = parse_consensus_bulk_html(html, actions, str(path))
        if stats.get("rows_seen", 0) == 0:
            continue
        files += 1
        matched_rows += int(stats.get("matched_rows", 0))
        observations.extend(obs)
        failures.extend(failed)
    return observations, failures, {"files": files, "matched_rows": matched_rows, "observations": len(observations), "failures": len(failures)}
