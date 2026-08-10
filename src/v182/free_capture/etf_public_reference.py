from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import quote, urljoin
import json
import math
import os
import re
import time
import unicodedata

import pandas as pd
import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TARGET = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
DEFAULT_AUDIT = ROOT / "outputs/audit/V21.1_ETF_PUBLIC_REFERENCE_AUDIT.json"
UA = "PEA-V21.1-ETF-PublicReference/1.0"

BOURSORAMA_PEA_URLS = (
    "https://www.boursorama.com/bourse/trackers/recherche/?beginnerEtfSearch%5BisEtf%5D=1&beginnerEtfSearch%5Btaxation%5D=1&tableName=partner-table",
    "https://www.boursorama.com/bourse/trackers/recherche/autres/?beginnerEtfSearch%5BisEtf%5D=1&beginnerEtfSearch%5Btaxation%5D=1&tableName=other-table",
)
LESMEILLEURSFONDS_URL = "https://www.lesmeilleursfonds.com/article/liste-des-etf-eligibles-au-pea-avec-les-frais"
AMUNDI_PEA_SEARCH = "https://www.amundietf.fr/fr/particuliers/produits-etf/recherche?pea=true"
BNPP_ETF_LIST = "https://www.bnpparibas-am.com/fr-fr/nos-fonds/notre-selection-detf/"
SPDR_FINDER = "https://www.ssga.com/fr/fr/intermediary/fund-finder"
MORNINGSTAR_QUICKRANK = "https://tools.morningstar.fr/fr/etfquickrank/default.aspx?LanguageId=fr-FR&Site=fr&Universe=ETALL%24%24ALL"

ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.upper()
    text = re.sub(r"\b(UCITS|ETF|EUR|USD|ACC|DIST|CAP|C|D|DR|PEA|THE|INDEX|FUND)\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def _float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("\u202f", "").replace(" ", "").replace(",", ".")
    text = text.replace("€", "").replace("$", "").replace("£", "").replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        out = float(match.group(0))
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _response(session: requests.Session, url: str, timeout: int = 25) -> requests.Response:
    response = session.get(
        url,
        headers={"User-Agent": os.getenv("V182_USER_AGENT", UA), "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip().lower() in {"", "nan", "none", "null", "n/a", "<na>", "not_available"}


def _fill(df: pd.DataFrame, index: int, field: str, value: object, source: str, source_url: str, audit: dict) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    if field not in df.columns:
        df[field] = pd.Series(pd.NA, index=df.index, dtype="object")
    if not _is_missing(df.at[index, field]):
        audit["skipped_existing"] += 1
        return False
    df.at[index, field] = value
    for suffix, v in {
        "source": source,
        "url": source_url,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }.items():
        col = f"v211_{field}_{suffix}"
        if col not in df.columns:
            df[col] = pd.Series(pd.NA, index=df.index, dtype="object")
        df.at[index, col] = v
    audit["applied_cells"] += 1
    audit["field_applied"][field] = int(audit["field_applied"].get(field, 0)) + 1
    audit["source_applied"][source] = int(audit["source_applied"].get(source, 0)) + 1
    return True


def _set_source_status(df: pd.DataFrame, index: int, source: str, status: str, url: str = "") -> None:
    key = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
    for suffix, value in (("status", status), ("url", url)):
        col = f"v211_{key}_{suffix}"
        if col not in df.columns:
            df[col] = pd.Series(pd.NA, index=df.index, dtype="object")
        df.at[index, col] = value


def _parse_label_value(text: str, labels: tuple[str, ...], max_chars: int = 120) -> str | None:
    compact = re.sub(r"\s+", " ", text)
    for label in labels:
        match = re.search(rf"\b{re.escape(label)}\b\s*[:|\-]?\s*(.{{1,{max_chars}}})", compact, flags=re.I)
        if not match:
            continue
        value = re.split(r"\s{2,}|\||(?:\b[A-ZÀ-Ý][A-Za-zÀ-ÿ ]{2,35}\s*:)", match.group(1))[0].strip(" :-|")
        if value:
            return value
    return None


def _issuer_page_fields(html: str, isin: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    if isin not in text.upper():
        return {"identity": "REJECTED_ISIN_NOT_ON_PAGE"}
    out: dict[str, object] = {"identity": "VALIDATED_ISIN"}

    # TER / ongoing charges.
    for labels in (
        ("Ongoing Charges", "Frais courants", "Total des Frais sur Encours", "Total Expense Ratio", "TER"),
    ):
        value = _parse_label_value(text, labels)
        n = _float(value)
        if n is not None and 0 <= n <= 10:
            out["ter_pct"] = n
            break

    # AUM. Preserve source currency; only convert directly to EUR millions when the page states EUR.
    aum_raw = _parse_label_value(text, ("Assets Under Management", "Actifs gérés", "Actifs sous gestion", "AUM"), 160)
    if aum_raw:
        n = _float(aum_raw)
        currency = "EUR" if "€" in aum_raw or re.search(r"\bEUR\b", aum_raw, re.I) else ("USD" if "$" in aum_raw or re.search(r"\bUSD\b", aum_raw, re.I) else ("GBP" if "£" in aum_raw else ""))
        multiplier_m = 1.0
        low = aum_raw.lower()
        if any(token in low for token in ["bn", "billion", "mrd", "milliard"]):
            multiplier_m = 1000.0
        elif any(token in low for token in ["mio", "million", " m"]):
            multiplier_m = 1.0
        if n is not None:
            out["issuer_aum_m"] = n * multiplier_m
            out["issuer_aum_currency"] = currency
            if currency == "EUR":
                out["fund_total_assets_eur_m"] = n * multiplier_m
                out["aum_m"] = n * multiplier_m

    holdings = _parse_label_value(text, ("Nombre de Lignes", "Number of Holdings", "Nombre de positions", "Holdings"), 60)
    h = _float(holdings)
    if h is not None and 0 < h < 100000:
        out["holdings"] = int(round(h))

    benchmark = _parse_label_value(text, ("Indice", "Benchmark", "Indice de référence", "Reference Index"), 180)
    if benchmark and len(benchmark) < 180:
        out["official_benchmark"] = benchmark

    distribution = _parse_label_value(text, ("Traitement des revenus", "Fréquence de Distribution", "Income Treatment", "Distribution Frequency"), 100)
    if distribution:
        d = distribution.upper()
        if "ACC" in d or "CAPITAL" in d:
            out["distribution_policy"] = "ACC"
        elif "DIST" in d or "DISTRIB" in d:
            out["distribution_policy"] = "DIST"
        out["distribution_frequency"] = distribution

    replication = _parse_label_value(text, ("Méthode de Réplication", "Replication Method", "Réplication", "Replication"), 120)
    if replication:
        out["replication_method"] = replication
        r = replication.upper()
        if any(token in r for token in ["PHYSICAL", "PHYSIQUE", "DIRECT", "RÉPLICATION", "REPLICATION"]):
            out["replication_hint"] = "PHYSICAL_OR_DIRECT"
        if any(token in r for token in ["SWAP", "SYNTH"]):
            out["replication_hint"] = "SYNTHETIC_OR_SWAP"

    pea = re.search(r"(?:Eligible|Éligible)\s+PEA\s*[:|]?\s*(Oui|Non|Yes|No)", text, flags=re.I)
    if pea:
        out["pea_issuer_status"] = "CONFIRMED" if pea.group(1).lower() in {"oui", "yes"} else "NOT_ELIGIBLE"

    # Price/performance fields when explicitly exposed on issuer page.
    for field, labels in {
        "perf_1m_pct": ("1 Mois", "1 Month"),
        "perf_1y_pct": ("1 An", "1 Year"),
        "perf_3y_pct": ("3 Ans", "3 Years"),
        "perf_5y_pct": ("5 Ans", "5 Years"),
    }.items():
        raw = _parse_label_value(text, labels, 40)
        value = _float(raw)
        if value is not None and -1000 < value < 10000:
            out[field] = value

    docs = {}
    for link in soup.find_all("a", href=True):
        label = " ".join(link.stripped_strings).lower()
        href = urljoin("https://invalid.local/", str(link.get("href") or ""))
        if not href.lower().endswith((".pdf", ".xls", ".xlsx")) and "document" not in label and "kid" not in label and "fiche" not in label and "factsheet" not in label:
            continue
        kind = "document"
        if "kid" in label or "dic" in label or "key information" in label:
            kind = "kid"
        elif "fiche" in label or "factsheet" in label or "product sheet" in label:
            kind = "factsheet"
        elif "prospect" in label:
            kind = "prospectus"
        docs.setdefault(kind, href)
    out["official_documents"] = docs
    return out


def _apply_fields(df: pd.DataFrame, index: int, fields: dict, source: str, url: str, audit: dict) -> int:
    applied = 0
    for field, value in fields.items():
        if field in {"identity", "official_documents", "issuer_aum_currency"}:
            continue
        if _fill(df, index, field, value, source, url, audit):
            applied += 1
    if fields.get("issuer_aum_currency"):
        _fill(df, index, "issuer_aum_currency", fields["issuer_aum_currency"], source, url, audit)
    if fields.get("issuer_aum_m") is not None:
        _fill(df, index, "issuer_aum_m", fields["issuer_aum_m"], source, url, audit)
    docs = fields.get("official_documents") if isinstance(fields.get("official_documents"), dict) else {}
    for kind, doc_url in docs.items():
        _fill(df, index, f"official_{kind}_url", doc_url, source, url, audit)
    return applied


def _links_by_isin(html: str, base_url: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for link in soup.find_all("a", href=True):
        text = " ".join(link.stripped_strings) + " " + str(link.get("href") or "")
        for isin in ISIN_RE.findall(text.upper()):
            out.setdefault(isin, urljoin(base_url, str(link["href"])))
    return out


def _boursorama(session: requests.Session, df: pd.DataFrame, audit: dict, max_pages: int = 8) -> dict:
    candidates: list[tuple[str, str]] = []
    errors: list[str] = []
    for base in BOURSORAMA_PEA_URLS:
        for page in range(1, max_pages + 1):
            sep = "&" if "?" in base else "?"
            url = f"{base}{sep}page={page}"
            try:
                html = _response(session, url).text
                soup = BeautifulSoup(html, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = str(link.get("href") or "")
                    name = " ".join(link.stripped_strings).strip()
                    if name and "/bourse/trackers/" in href and "/recherche" not in href:
                        candidates.append((name, urljoin("https://www.boursorama.com", href)))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}:{str(exc)[:100]}")
                break
            time.sleep(0.15)
    # Deduplicate link candidates.
    seen = set()
    candidates = [(n, u) for n, u in candidates if not (u in seen or seen.add(u))]
    matched = applied = 0
    for i, row in df.iterrows():
        target_name = _norm(row.get("name"))
        if not target_name:
            _set_source_status(df, i, "BOURSORAMA_ETF", "NO_NAME")
            continue
        scored = sorted(((SequenceMatcher(None, target_name, _norm(n)).ratio(), n, u) for n, u in candidates), reverse=True)
        if not scored or scored[0][0] < 0.72:
            _set_source_status(df, i, "BOURSORAMA_ETF", "NOT_RESOLVED_IN_PEA_LIST")
            continue
        score, _, url = scored[0]
        try:
            html = _response(session, url).text
            isin = str(row.get("isin") or "").upper()
            if isin not in html.upper():
                _set_source_status(df, i, "BOURSORAMA_ETF", "NAME_MATCH_REJECTED_ISIN", url)
                continue
            matched += 1
            _set_source_status(df, i, "BOURSORAMA_ETF", "PEA_LIST_AND_ISIN_CONFIRMED", url)
            _fill(df, i, "pea_eligibility_boursorama", True, "BOURSORAMA_ETF", url, audit)
            _fill(df, i, "boursorama_name_match_score", round(score, 4), "BOURSORAMA_ETF", url, audit)
            parsed = _issuer_page_fields(html, isin)
            applied += _apply_fields(df, i, parsed, "BOURSORAMA_ETF", url, audit)
        except Exception as exc:
            _set_source_status(df, i, "BOURSORAMA_ETF", f"ERROR_{type(exc).__name__}", url)
        time.sleep(0.25)
    return {"status": "OK" if matched else "NO_MATCH", "candidates": len(candidates), "matched": matched, "applied": applied, "errors": errors[:5]}


def _lesmeilleursfonds(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    try:
        response = _response(session, LESMEILLEURSFONDS_URL)
        tables = pd.read_html(StringIO(response.text))
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
    found: dict[str, dict] = {}
    for table in tables:
        for _, r in table.iterrows():
            joined = " | ".join(str(x) for x in r.tolist())
            match = ISIN_RE.search(joined.upper())
            if not match:
                continue
            isin = match.group(0)
            payload = {"pea_eligibility_lesmeilleursfonds": True}
            for col in table.columns:
                label = str(col).lower()
                value = r.get(col)
                if "frais" in label or "ter" in label:
                    n = _float(value)
                    if n is not None and 0 <= n <= 10:
                        payload["ter_pct"] = n
                if "indice" in label or "benchmark" in label:
                    if not _is_missing(value):
                        payload["official_benchmark"] = str(value)
            found[isin] = payload
    matched = 0
    for i, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        if isin not in found:
            _set_source_status(df, i, "LESMEILLEURSFONDS_PEA", "NOT_LISTED", LESMEILLEURSFONDS_URL)
            continue
        matched += 1
        _set_source_status(df, i, "LESMEILLEURSFONDS_PEA", "PEA_LIST_CONFIRMED", LESMEILLEURSFONDS_URL)
        _apply_fields(df, i, found[isin], "LESMEILLEURSFONDS_PEA", LESMEILLEURSFONDS_URL, audit)
    return {"status": "OK", "tables": len(tables), "listed_isin": len(found), "matched": matched}


def _amundi(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    try:
        html = _response(session, AMUNDI_PEA_SEARCH).text
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
    links = _links_by_isin(html, AMUNDI_PEA_SEARCH)
    # The Amundi search page can expose rows in text even when a link label is abbreviated.
    for isin in set(ISIN_RE.findall(html.upper())):
        if isin not in links:
            match = re.search(rf'href=["\']([^"\']*{re.escape(isin.lower())}[^"\']*)', html, flags=re.I)
            if match:
                links[isin] = urljoin(AMUNDI_PEA_SEARCH, match.group(1))
    matched = applied = 0
    for i, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        url = links.get(isin)
        if not url:
            # Only Amundi-branded rows are treated as unresolved issuer candidates.
            name = str(row.get("name") or "").upper()
            _set_source_status(df, i, "AMUNDI_ETF", "NOT_AMUNDI_OR_NOT_RESOLVED" if "AMUNDI" not in name and "LYXOR" not in name else "AMUNDI_ISIN_NOT_RESOLVED", AMUNDI_PEA_SEARCH)
            continue
        try:
            page = _response(session, url).text
            parsed = _issuer_page_fields(page, isin)
            if parsed.get("identity") != "VALIDATED_ISIN":
                _set_source_status(df, i, "AMUNDI_ETF", "REJECTED_ISIN", url)
                continue
            matched += 1
            _set_source_status(df, i, "AMUNDI_ETF", "OFFICIAL_ISIN_VALIDATED", url)
            _fill(df, i, "pea_eligibility_amundi_search", True, "AMUNDI_ETF", AMUNDI_PEA_SEARCH, audit)
            applied += _apply_fields(df, i, parsed, "AMUNDI_ETF", url, audit)
        except Exception as exc:
            _set_source_status(df, i, "AMUNDI_ETF", f"ERROR_{type(exc).__name__}", url)
        time.sleep(0.2)
    return {"status": "OK" if links else "NO_LINKS", "search_isin": len(links), "matched": matched, "applied": applied}


def _bnpp(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    try:
        html = _response(session, BNPP_ETF_LIST).text
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
    xls_urls = []
    product_links = []
    for link in soup.find_all("a", href=True):
        href = urljoin(BNPP_ETF_LIST, str(link["href"]))
        label = " ".join(link.stripped_strings)
        low = (href + " " + label).lower()
        if ".xls" in low or ".xlsx" in low or "gamme" in low and "xls" in low:
            xls_urls.append(href)
        if "bnp paribas easy" in label.lower() or "/fund/" in href.lower() or "/fonds/" in href.lower():
            product_links.append((label, href))

    by_isin: dict[str, dict] = {}
    workbook_status = "NOT_FOUND"
    for url in xls_urls[:4]:
        try:
            content = _response(session, url, timeout=40).content
            book = pd.ExcelFile(BytesIO(content))
            workbook_status = "READ"
            for sheet in book.sheet_names:
                table = pd.read_excel(book, sheet_name=sheet, dtype=object)
                for _, r in table.iterrows():
                    joined = " | ".join(str(x) for x in r.tolist())
                    match = ISIN_RE.search(joined.upper())
                    if not match:
                        continue
                    isin = match.group(0)
                    payload: dict[str, object] = {"pea_eligibility_bnpp_reference": "CHECKED_IN_OFFICIAL_RANGE"}
                    for col in table.columns:
                        label = str(col).lower()
                        value = r.get(col)
                        if any(token in label for token in ["frais", "ongoing", "ter"]):
                            n = _float(value)
                            if n is not None and 0 <= n <= 10:
                                payload["ter_pct"] = n
                        if "pea" in label and not _is_missing(value):
                            flag = str(value).strip().lower()
                            if flag in {"oui", "yes", "1", "true", "x"}:
                                payload["pea_eligibility_bnpp_reference"] = True
                        if "indice" in label or "benchmark" in label:
                            if not _is_missing(value):
                                payload["official_benchmark"] = str(value)
                    by_isin[isin] = payload
        except Exception:
            continue

    matched = applied = 0
    for i, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        payload = by_isin.get(isin)
        official_url = BNPP_ETF_LIST
        if payload:
            matched += 1
            _set_source_status(df, i, "BNPP_ETF", "OFFICIAL_RANGE_ISIN_MATCH", official_url)
            applied += _apply_fields(df, i, payload, "BNPP_ETF", official_url, audit)
            continue
        # Fallback: only follow a strongly matching official product link, then validate ISIN.
        name = _norm(row.get("name"))
        scored = sorted(((SequenceMatcher(None, name, _norm(n)).ratio(), u) for n, u in product_links if n), reverse=True)
        if scored and scored[0][0] >= 0.78 and "BNP" in str(row.get("name") or "").upper():
            url = scored[0][1]
            try:
                page = _response(session, url).text
                parsed = _issuer_page_fields(page, isin)
                if parsed.get("identity") == "VALIDATED_ISIN":
                    matched += 1
                    _set_source_status(df, i, "BNPP_ETF", "OFFICIAL_PRODUCT_ISIN_VALIDATED", url)
                    applied += _apply_fields(df, i, parsed, "BNPP_ETF", url, audit)
                    continue
            except Exception:
                pass
        _set_source_status(df, i, "BNPP_ETF", "NOT_BNPP_OR_NOT_RESOLVED", BNPP_ETF_LIST)
    return {"status": "OK", "workbook_status": workbook_status, "workbook_isin": len(by_isin), "product_links": len(product_links), "matched": matched, "applied": applied}


def _discover_product_link(html: str, base: str, isin: str, path_token: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for link in soup.find_all("a", href=True):
        href = urljoin(base, str(link["href"]))
        text = " ".join(link.stripped_strings)
        if path_token in href.lower():
            score = 1.0 if isin in (href + " " + text).upper() else 0.0
            candidates.append((score, href))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


def _spdr(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    matched = applied = attempted = 0
    for i, row in df.iterrows():
        name = str(row.get("name") or "")
        if "SPDR" not in name.upper() and "STATE STREET" not in name.upper():
            _set_source_status(df, i, "SPDR_ETF", "NOT_SPDR")
            continue
        attempted += 1
        isin = str(row.get("isin") or "").upper()
        search_url = f"{SPDR_FINDER}?search={quote(isin)}"
        try:
            html = _response(session, search_url).text
            url = _discover_product_link(html, SPDR_FINDER, isin, "/etfs/")
            if not url:
                _set_source_status(df, i, "SPDR_ETF", "FUND_FINDER_NO_PRODUCT_LINK", search_url)
                continue
            page = _response(session, url).text
            parsed = _issuer_page_fields(page, isin)
            if parsed.get("identity") != "VALIDATED_ISIN":
                _set_source_status(df, i, "SPDR_ETF", "REJECTED_ISIN", url)
                continue
            matched += 1
            _set_source_status(df, i, "SPDR_ETF", "OFFICIAL_PRODUCT_ISIN_VALIDATED", url)
            applied += _apply_fields(df, i, parsed, "SPDR_ETF", url, audit)
        except Exception as exc:
            _set_source_status(df, i, "SPDR_ETF", f"ERROR_{type(exc).__name__}", search_url)
        time.sleep(0.2)
    return {"status": "OK", "attempted": attempted, "matched": matched, "applied": applied}


def _morningstar(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    matched = applied = attempted = 0
    for i, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        # Morningstar's public Quickrank explicitly supports name/ISIN/ticker searches. The query
        # parameter is best-effort; no Morningstar internal ID is invented if the public response
        # does not expose an ISIN-resolvable link.
        url = MORNINGSTAR_QUICKRANK + "&search=" + quote(isin)
        attempted += 1
        try:
            html = _response(session, url).text
            if isin not in html.upper():
                _set_source_status(df, i, "MORNINGSTAR_FR", "PUBLIC_SEARCH_NOT_RESOLVED", url)
                continue
            soup = BeautifulSoup(html, "html.parser")
            page_url = _discover_product_link(html, url, isin, "morningstar") or url
            page = html if page_url == url else _response(session, page_url).text
            if isin not in page.upper():
                _set_source_status(df, i, "MORNINGSTAR_FR", "REJECTED_ISIN", page_url)
                continue
            text = " ".join(BeautifulSoup(page, "html.parser").stripped_strings)
            matched += 1
            _set_source_status(df, i, "MORNINGSTAR_FR", "PUBLIC_ISIN_VALIDATED", page_url)
            star = None
            for pattern in (
                r"Morningstar(?:\s+Overall)?\s+Rating\D{0,30}([1-5])",
                r"([1-5])\s*(?:étoiles|etoiles|stars)\b",
            ):
                m = re.search(pattern, text, flags=re.I)
                if m:
                    star = int(m.group(1))
                    break
            if star is not None:
                applied += int(_fill(df, i, "morningstar_rating", star, "MORNINGSTAR_FR", page_url, audit))
            category = _parse_label_value(text, ("Catégorie Morningstar", "Morningstar Category", "Catégorie"), 120)
            if category:
                applied += int(_fill(df, i, "morningstar_category", category, "MORNINGSTAR_FR", page_url, audit))
            for years in (1, 3, 5):
                raw = _parse_label_value(text, (f"Rang catégorie {years} an", f"Category Rank {years} Year", f"{years} ans"), 50)
                value = _float(raw)
                if value is not None:
                    applied += int(_fill(df, i, f"rank_cat_{years}y", value, "MORNINGSTAR_FR", page_url, audit))
        except Exception as exc:
            _set_source_status(df, i, "MORNINGSTAR_FR", f"ERROR_{type(exc).__name__}", url)
        time.sleep(0.12)
    return {"status": "OK", "attempted": attempted, "matched": matched, "applied": applied, "note": "No internal Morningstar identifier is guessed"}


def apply(target: Path, audit_path: Path = DEFAULT_AUDIT) -> dict:
    df = pd.read_csv(target, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 102 or "isin" not in df.columns or df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("V21.1 ETF public reference requires exactly 102 unique ISIN")
    if "asset_class" in df.columns and not df["asset_class"].astype(str).str.upper().eq("ETF").all():
        raise RuntimeError("V21.1 ETF public reference asset-class gate")

    audit = {
        "passed": True,
        "version": "V21.1_ETF_PUBLIC_REFERENCE",
        "execution": "RESEARCH_ONLY",
        "rows": len(df),
        "policy": "OFFICIAL_ISSUER_FIRST_MISSING_ONLY_NO_OVERWRITE",
        "applied_cells": 0,
        "skipped_existing": 0,
        "field_applied": {},
        "source_applied": {},
        "sources": {},
        "runtime_note": "This module is wired for a future workflow run; current repository audit did not execute network capture.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    session = requests.Session()

    # Eligibility cross-checks first, official issuer/fund metadata second, Morningstar last.
    audit["sources"]["BOURSORAMA_ETF"] = _boursorama(session, df, audit)
    audit["sources"]["LESMEILLEURSFONDS_PEA"] = _lesmeilleursfonds(session, df, audit)
    audit["sources"]["AMUNDI_ETF"] = _amundi(session, df, audit)
    audit["sources"]["BNPP_ETF"] = _bnpp(session, df, audit)
    audit["sources"]["SPDR_ETF"] = _spdr(session, df, audit)
    audit["sources"]["MORNINGSTAR_FR"] = _morningstar(session, df, audit)

    df["v211_etf_public_reference_checked"] = True
    df["v211_etf_public_reference_version"] = "V21.1_ETF_PUBLIC_REFERENCE"
    df["v211_etf_public_reference_as_of_utc"] = datetime.now(timezone.utc).isoformat()
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(target, sep=";", index=False, encoding="utf-8-sig")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    args = parser.parse_args()
    result = apply(Path(args.target), Path(args.audit))
    print("V21_1_ETF_PUBLIC_REFERENCE_OK", json.dumps({
        "rows": result["rows"],
        "applied_cells": result["applied_cells"],
        "sources": result["sources"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
