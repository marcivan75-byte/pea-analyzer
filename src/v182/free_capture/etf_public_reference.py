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

from .euronext_live_public import (
    EURONEXT_MICS,
    _first_json_number as _euronext_json_number,
    _json_candidates as _euronext_json_candidates,
    _label_number as _euronext_label_number,
    _url as _euronext_url,
)
from .openfigi_v3 import URL as OPENFIGI_URL, _pick_result as _openfigi_pick


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TARGET = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
DEFAULT_AUDIT = ROOT / "outputs/audit/V21.1_ETF_PUBLIC_REFERENCE_AUDIT.json"
UA = "PEA-V21.1-ETF-PublicReference/1.1"

BOURSORAMA_PEA_URLS = (
    "https://www.boursorama.com/bourse/trackers/recherche/?beginnerEtfSearch%5BisEtf%5D=1&beginnerEtfSearch%5Btaxation%5D=1&tableName=partner-table",
    "https://www.boursorama.com/bourse/trackers/recherche/autres/?beginnerEtfSearch%5BisEtf%5D=1&beginnerEtfSearch%5Btaxation%5D=1&tableName=other-table",
)
LESMEILLEURSFONDS_URL = "https://www.lesmeilleursfonds.com/article/liste-des-etf-eligibles-au-pea-avec-les-frais"
AMUNDI_SEARCH = "https://www.amundietf.fr/fr/particuliers/produits-etf/recherche?pea=true"
BNPP_ETF_LIST = "https://www.bnpparibas-am.com/fr-fr/nos-fonds/notre-selection-detf/"
SPDR_FINDER = "https://www.ssga.com/fr/fr/intermediary/fund-finder"
MORNINGSTAR_QUICKRANK = "https://tools.morningstar.fr/fr/etfquickrank/default.aspx?LanguageId=fr-FR&Site=fr&Universe=ETALL%24%24ALL"
ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.upper()
    text = re.sub(r"\b(UCITS|ETF|EUR|USD|ACC|DIST|CAP|DR|PEA|THE|INDEX|FUND)\b", " ", text)
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


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip().lower() in {"", "nan", "none", "null", "n/a", "<na>", "not_available"}


def _response(session: requests.Session, url: str, timeout: int = 25) -> requests.Response:
    response = session.get(
        url,
        headers={
            "User-Agent": os.getenv("V182_USER_AGENT", UA),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response


def _fill(df: pd.DataFrame, index: int, field: str, value: object, source: str, source_url: str, audit: dict) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    if field not in df.columns:
        df[field] = pd.Series(pd.NA, index=df.index, dtype="object")
    if not _is_missing(df.at[index, field]):
        audit["skipped_existing"] += 1
        return False
    df.at[index, field] = value
    for suffix, provenance_value in {
        "source": source,
        "url": source_url,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }.items():
        col = f"v211_{field}_{suffix}"
        if col not in df.columns:
            df[col] = pd.Series(pd.NA, index=df.index, dtype="object")
        df.at[index, col] = provenance_value
    audit["applied_cells"] += 1
    audit["field_applied"][field] = int(audit["field_applied"].get(field, 0)) + 1
    audit["source_applied"][source] = int(audit["source_applied"].get(source, 0)) + 1
    return True


def _set_status(df: pd.DataFrame, index: int, source: str, status: str, url: str = "") -> None:
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


def _issuer_page_fields(html: str, isin: str, page_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    if isin not in text.upper() and isin not in html.upper():
        return {"identity": "REJECTED_ISIN_NOT_ON_PAGE"}
    out: dict[str, object] = {"identity": "VALIDATED_ISIN"}

    raw = _parse_label_value(text, ("Ongoing Charges", "Frais courants", "Total des Frais sur Encours", "Total Expense Ratio", "TER"))
    ter = _float(raw)
    if ter is not None and 0 <= ter <= 10:
        out["ter_pct"] = ter

    aum_raw = _parse_label_value(text, ("Assets Under Management", "Actifs gérés", "Actifs sous gestion", "AUM"), 160)
    if aum_raw:
        amount = _float(aum_raw)
        currency = "EUR" if "€" in aum_raw or re.search(r"\bEUR\b", aum_raw, re.I) else ("USD" if "$" in aum_raw or re.search(r"\bUSD\b", aum_raw, re.I) else ("GBP" if "£" in aum_raw else ""))
        multiplier_m = 1000.0 if any(token in aum_raw.lower() for token in ("bn", "billion", "mrd", "milliard")) else 1.0
        if amount is not None:
            out["issuer_aum_m"] = amount * multiplier_m
            out["issuer_aum_currency"] = currency
            if currency == "EUR":
                out["fund_total_assets_eur_m"] = amount * multiplier_m
                out["aum_m"] = amount * multiplier_m

    holdings = _float(_parse_label_value(text, ("Nombre de Lignes", "Number of Holdings", "Nombre de positions", "Holdings"), 60))
    if holdings is not None and 0 < holdings < 100000:
        out["holdings"] = int(round(holdings))

    benchmark = _parse_label_value(text, ("Indice de référence", "Reference Index", "Benchmark", "Indice"), 180)
    if benchmark and len(benchmark) < 180:
        out["official_benchmark"] = benchmark

    distribution = _parse_label_value(text, ("Traitement des revenus", "Fréquence de Distribution", "Income Treatment", "Distribution Frequency"), 100)
    if distribution:
        upper = distribution.upper()
        if "ACC" in upper or "CAPITAL" in upper:
            out["distribution_policy"] = "ACC"
        elif "DIST" in upper or "DISTRIB" in upper:
            out["distribution_policy"] = "DIST"
        out["distribution_frequency"] = distribution

    replication = _parse_label_value(text, ("Méthode de Réplication", "Replication Method", "Réplication", "Replication"), 120)
    if replication:
        out["replication_method"] = replication
        upper = replication.upper()
        if any(token in upper for token in ("PHYSICAL", "PHYSIQUE", "DIRECT")):
            out["replication_hint"] = "PHYSICAL_OR_DIRECT"
        elif any(token in upper for token in ("SWAP", "SYNTH")):
            out["replication_hint"] = "SYNTHETIC_OR_SWAP"

    pea = re.search(r"(?:Eligible|Éligible)\s+(?:au\s+)?PEA\s*[:|]?\s*(Oui|Non|Yes|No)", text, flags=re.I)
    if pea:
        out["pea_issuer_status"] = "CONFIRMED" if pea.group(1).lower() in {"oui", "yes"} else "NOT_ELIGIBLE"

    for field, labels in {
        "perf_1m_pct": ("1 Mois", "1 Month"),
        "perf_1y_pct": ("1 An", "1 Year"),
        "perf_3y_pct": ("3 Ans", "3 Years"),
        "perf_5y_pct": ("5 Ans", "5 Years"),
    }.items():
        value = _float(_parse_label_value(text, labels, 40))
        if value is not None and -1000 < value < 10000:
            out[field] = value

    docs: dict[str, str] = {}
    for link in soup.find_all("a", href=True):
        label = " ".join(link.stripped_strings).lower()
        href = urljoin(page_url, str(link.get("href") or ""))
        marker = href.lower() + " " + label
        if not any(token in marker for token in (".pdf", ".xls", ".xlsx", "document", "kid", "dic", "factsheet", "fiche", "prospect")):
            continue
        kind = "document"
        if any(token in label for token in ("kid", "dic", "key information")):
            kind = "kid"
        elif any(token in label for token in ("fiche", "factsheet", "product sheet")):
            kind = "factsheet"
        elif "prospect" in label:
            kind = "prospectus"
        docs.setdefault(kind, href)
    out["official_documents"] = docs
    return out


def _apply_fields(df: pd.DataFrame, index: int, fields: dict, source: str, url: str, audit: dict) -> int:
    applied = 0
    for field, value in fields.items():
        if field in {"identity", "official_documents", "issuer_aum_currency", "issuer_aum_m"}:
            continue
        applied += int(_fill(df, index, field, value, source, url, audit))
    if fields.get("issuer_aum_currency"):
        applied += int(_fill(df, index, "issuer_aum_currency", fields["issuer_aum_currency"], source, url, audit))
    if fields.get("issuer_aum_m") is not None:
        applied += int(_fill(df, index, "issuer_aum_m", fields["issuer_aum_m"], source, url, audit))
    for kind, doc_url in (fields.get("official_documents") or {}).items():
        applied += int(_fill(df, index, f"official_{kind}_url", doc_url, source, url, audit))
    return applied


def _links_by_isin(html: str, base_url: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for link in soup.find_all("a", href=True):
        text = " ".join(link.stripped_strings) + " " + str(link.get("href") or "")
        for isin in ISIN_RE.findall(text.upper()):
            out.setdefault(isin, urljoin(base_url, str(link["href"])))
    return out


def _openfigi(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    key = str(os.getenv("OPENFIGI_API_KEY") or "").strip()
    batch_size = 100 if key else 5
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    attempted = matched = requests_used = 0
    for start in range(0, len(df), batch_size):
        chunk = df.iloc[start:start + batch_size]
        payload = [{"idType": "ID_ISIN", "idValue": str(value).upper()} for value in chunk["isin"]]
        try:
            response = session.post(OPENFIGI_URL, json=payload, headers=headers, timeout=30)
            requests_used += 1
            if response.status_code == 429:
                time.sleep(6 if key else 15)
                response = session.post(OPENFIGI_URL, json=payload, headers=headers, timeout=30)
                requests_used += 1
            response.raise_for_status()
            answers = response.json()
        except Exception:
            continue
        for (idx, row), answer in zip(chunk.iterrows(), answers, strict=False):
            attempted += 1
            chosen, resolution = _openfigi_pick(
                answer.get("data") or [],
                str(row.get("yahoo_ticker") or row.get("ticker_yahoo_final") or ""),
                str(row.get("euronext_mic") or row.get("mic") or ""),
            )
            if not chosen:
                _set_status(df, idx, "OPENFIGI_ETF", "NO_MATCH")
                continue
            matched += 1
            _set_status(df, idx, "OPENFIGI_ETF", resolution, OPENFIGI_URL)
            for field, value in {
                "free_identity_figi": chosen.get("figi"),
                "free_identity_composite_figi": chosen.get("compositeFIGI"),
                "free_identity_share_class_figi": chosen.get("shareClassFIGI"),
                "free_identity_ticker": chosen.get("ticker"),
                "free_identity_exchange": chosen.get("exchCode"),
                "free_identity_security_type": chosen.get("securityType2") or chosen.get("securityType"),
            }.items():
                _fill(df, idx, field, value, "OPENFIGI_V3", OPENFIGI_URL, audit)
        time.sleep(0.3 if key else 2.5)
    return {"status": "OK" if matched else "NO_MATCH", "attempted": attempted, "matched": matched, "requests": requests_used, "api_key_present": bool(key)}


def _euronext(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    attempted = matched = blocked = 0
    mic_series = df.get("euronext_mic", df.get("mic", pd.Series("", index=df.index))).astype(str).str.upper()
    for idx in df.index[mic_series.isin(EURONEXT_MICS)]:
        row = df.loc[idx]
        attempted += 1
        isin = str(row.get("isin") or "").upper()
        mic = str(row.get("euronext_mic") or row.get("mic") or "").upper()
        url = _euronext_url(isin, mic, "ETF")
        try:
            response = _response(session, url, 20)
            if response.status_code in {401, 403, 429}:
                blocked += 1
                _set_status(df, idx, "EURONEXT_LIVE_ETF", f"HTTP_{response.status_code}", url)
                continue
            html = response.text
            if isin not in html.upper():
                _set_status(df, idx, "EURONEXT_LIVE_ETF", "REJECTED_ISIN", url)
                continue
            soup = BeautifulSoup(html, "html.parser")
            text = " ".join(soup.stripped_strings)
            items = _euronext_json_candidates(soup)
            last = _euronext_json_number(items, ("lastPrice", "last_price", "price")) or _euronext_label_number(text, ("Last price", "Dernier", "Last"))
            free_float = _euronext_json_number(items, ("freeFloat", "free_float")) or _euronext_label_number(text, ("Free float", "Flottant"), percent=True)
            bid = _euronext_json_number(items, ("bid",)) or _euronext_label_number(text, ("Bid", "Achat"))
            ask = _euronext_json_number(items, ("ask",)) or _euronext_label_number(text, ("Ask", "Vente"))
            spread = (ask - bid) / ((ask + bid) / 2) * 100 if bid is not None and ask is not None and ask >= bid and (ask + bid) > 0 else None
            matched += 1
            _set_status(df, idx, "EURONEXT_LIVE_ETF", "VALIDATED_ISIN", url)
            for field, value in {
                "euronext_live_last_price": last,
                "free_float_pct": free_float,
                "spread_pct": spread,
                "euronext_live_bid": bid,
                "euronext_live_ask": ask,
                "free_identity_mic": mic,
                "free_identity_exchange": "EURONEXT",
            }.items():
                _fill(df, idx, field, value, "EURONEXT_LIVE_PUBLIC", url, audit)
        except Exception as exc:
            _set_status(df, idx, "EURONEXT_LIVE_ETF", f"ERROR_{type(exc).__name__}", url)
        time.sleep(0.35)
    return {"status": "OK" if matched else "NO_MATCH", "attempted": attempted, "matched": matched, "blocked": blocked}


def _boursorama(session: requests.Session, df: pd.DataFrame, audit: dict, max_pages: int = 8) -> dict:
    candidates: list[tuple[str, str]] = []
    errors: list[str] = []
    for base in BOURSORAMA_PEA_URLS:
        for page in range(1, max_pages + 1):
            url = f"{base}&page={page}"
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
    unique: dict[str, str] = {}
    for name, url in candidates:
        unique.setdefault(url, name)
    candidates = [(name, url) for url, name in unique.items()]
    matched = applied = 0
    for idx, row in df.iterrows():
        target_name = _norm(row.get("name"))
        scored = sorted(((SequenceMatcher(None, target_name, _norm(name)).ratio(), url) for name, url in candidates), reverse=True)
        if not target_name or not scored or scored[0][0] < 0.72:
            _set_status(df, idx, "BOURSORAMA_ETF", "NOT_RESOLVED_IN_PEA_LIST")
            continue
        score, url = scored[0]
        try:
            html = _response(session, url).text
            isin = str(row.get("isin") or "").upper()
            if isin not in html.upper():
                _set_status(df, idx, "BOURSORAMA_ETF", "NAME_MATCH_REJECTED_ISIN", url)
                continue
            matched += 1
            _set_status(df, idx, "BOURSORAMA_ETF", "PEA_LIST_AND_ISIN_CONFIRMED", url)
            applied += int(_fill(df, idx, "pea_eligibility_boursorama", True, "BOURSORAMA_ETF", url, audit))
            applied += int(_fill(df, idx, "boursorama_name_match_score", round(score, 4), "BOURSORAMA_ETF", url, audit))
            applied += _apply_fields(df, idx, _issuer_page_fields(html, isin, url), "BOURSORAMA_ETF", url, audit)
        except Exception as exc:
            _set_status(df, idx, "BOURSORAMA_ETF", f"ERROR_{type(exc).__name__}", url)
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
        for _, row in table.iterrows():
            joined = " | ".join(str(value) for value in row.tolist())
            match = ISIN_RE.search(joined.upper())
            if not match:
                continue
            payload: dict[str, object] = {"pea_eligibility_lesmeilleursfonds": True}
            for col in table.columns:
                label = str(col).lower()
                value = row.get(col)
                if "frais" in label or "ter" in label:
                    n = _float(value)
                    if n is not None and 0 <= n <= 10:
                        payload["ter_pct"] = n
                if ("indice" in label or "benchmark" in label) and not _is_missing(value):
                    payload["official_benchmark"] = str(value)
            found[match.group(0)] = payload
    matched = 0
    for idx, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        if isin not in found:
            _set_status(df, idx, "LESMEILLEURSFONDS_PEA", "NOT_LISTED", LESMEILLEURSFONDS_URL)
            continue
        matched += 1
        _set_status(df, idx, "LESMEILLEURSFONDS_PEA", "PEA_LIST_CONFIRMED", LESMEILLEURSFONDS_URL)
        _apply_fields(df, idx, found[isin], "LESMEILLEURSFONDS_PEA", LESMEILLEURSFONDS_URL, audit)
    return {"status": "OK", "tables": len(tables), "listed_isin": len(found), "matched": matched}


def _amundi(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    try:
        html = _response(session, AMUNDI_SEARCH).text
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
    links = _links_by_isin(html, AMUNDI_SEARCH)
    matched = applied = explicit_pea = 0
    for idx, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        name = str(row.get("name") or "").upper()
        url = links.get(isin)
        if not url:
            status = "AMUNDI_ISIN_NOT_RESOLVED" if "AMUNDI" in name or "LYXOR" in name else "NOT_AMUNDI"
            _set_status(df, idx, "AMUNDI_ETF", status, AMUNDI_SEARCH)
            continue
        try:
            page = _response(session, url).text
            parsed = _issuer_page_fields(page, isin, url)
            if parsed.get("identity") != "VALIDATED_ISIN":
                _set_status(df, idx, "AMUNDI_ETF", "REJECTED_ISIN", url)
                continue
            matched += 1
            if parsed.get("pea_issuer_status") == "CONFIRMED":
                explicit_pea += 1
            _set_status(df, idx, "AMUNDI_ETF", "OFFICIAL_ISIN_VALIDATED", url)
            applied += _apply_fields(df, idx, parsed, "AMUNDI_ETF", url, audit)
        except Exception as exc:
            _set_status(df, idx, "AMUNDI_ETF", f"ERROR_{type(exc).__name__}", url)
        time.sleep(0.2)
    return {
        "status": "OK" if links else "NO_LINKS",
        "search_isin": len(links),
        "matched": matched,
        "explicit_pea_confirmed": explicit_pea,
        "applied": applied,
        "control": "Search URL is discovery only; PEA confirmation requires explicit product-page evidence",
    }


def _bnpp(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    try:
        html = _response(session, BNPP_ETF_LIST).text
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
    xls_urls: list[str] = []
    product_links: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(BNPP_ETF_LIST, str(link["href"]))
        label = " ".join(link.stripped_strings)
        marker = (href + " " + label).lower()
        if ".xls" in marker or ".xlsx" in marker:
            xls_urls.append(href)
        if "bnp paribas easy" in label.lower() or "/fund/" in href.lower() or "/fonds/" in href.lower():
            product_links.append((label, href))
    by_isin: dict[str, dict] = {}
    workbook_status = "NOT_FOUND"
    for url in xls_urls[:4]:
        try:
            content = _response(session, url, 40).content
            book = pd.ExcelFile(BytesIO(content))
            workbook_status = "READ"
            for sheet in book.sheet_names:
                table = pd.read_excel(book, sheet_name=sheet, dtype=object)
                for _, row in table.iterrows():
                    match = ISIN_RE.search(" | ".join(str(value) for value in row.tolist()).upper())
                    if not match:
                        continue
                    payload: dict[str, object] = {}
                    for col in table.columns:
                        label = str(col).lower()
                        value = row.get(col)
                        if any(token in label for token in ("frais", "ongoing", "ter")):
                            n = _float(value)
                            if n is not None and 0 <= n <= 10:
                                payload["ter_pct"] = n
                        if "pea" in label and str(value).strip().lower() in {"oui", "yes", "1", "true", "x"}:
                            payload["pea_eligibility_bnpp_reference"] = True
                        if ("indice" in label or "benchmark" in label) and not _is_missing(value):
                            payload["official_benchmark"] = str(value)
                    by_isin[match.group(0)] = payload
        except Exception:
            continue
    matched = applied = 0
    for idx, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        if isin in by_isin:
            matched += 1
            _set_status(df, idx, "BNPP_ETF", "OFFICIAL_RANGE_ISIN_MATCH", BNPP_ETF_LIST)
            applied += _apply_fields(df, idx, by_isin[isin], "BNPP_ETF", BNPP_ETF_LIST, audit)
            continue
        name = _norm(row.get("name"))
        scored = sorted(((SequenceMatcher(None, name, _norm(label)).ratio(), url) for label, url in product_links if label), reverse=True)
        if "BNP" in str(row.get("name") or "").upper() and scored and scored[0][0] >= 0.78:
            url = scored[0][1]
            try:
                page = _response(session, url).text
                parsed = _issuer_page_fields(page, isin, url)
                if parsed.get("identity") == "VALIDATED_ISIN":
                    matched += 1
                    _set_status(df, idx, "BNPP_ETF", "OFFICIAL_PRODUCT_ISIN_VALIDATED", url)
                    applied += _apply_fields(df, idx, parsed, "BNPP_ETF", url, audit)
                    continue
            except Exception:
                pass
        _set_status(df, idx, "BNPP_ETF", "NOT_BNPP_OR_NOT_RESOLVED", BNPP_ETF_LIST)
    return {"status": "OK", "workbook_status": workbook_status, "workbook_isin": len(by_isin), "matched": matched, "applied": applied}


def _discover_product_link(html: str, base: str, isin: str, path_token: str) -> str | None:
    candidates: list[tuple[float, str]] = []
    for link in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = urljoin(base, str(link["href"]))
        text = " ".join(link.stripped_strings)
        if path_token in href.lower():
            candidates.append((1.0 if isin in (href + " " + text).upper() else 0.0, href))
    return max(candidates)[1] if candidates else None


def _spdr(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    matched = applied = attempted = 0
    for idx, row in df.iterrows():
        name = str(row.get("name") or "")
        if "SPDR" not in name.upper() and "STATE STREET" not in name.upper():
            _set_status(df, idx, "SPDR_ETF", "NOT_SPDR")
            continue
        attempted += 1
        isin = str(row.get("isin") or "").upper()
        search_url = f"{SPDR_FINDER}?search={quote(isin)}"
        try:
            html = _response(session, search_url).text
            url = _discover_product_link(html, SPDR_FINDER, isin, "/etfs/")
            if not url:
                _set_status(df, idx, "SPDR_ETF", "FUND_FINDER_NO_PRODUCT_LINK", search_url)
                continue
            parsed = _issuer_page_fields(_response(session, url).text, isin, url)
            if parsed.get("identity") != "VALIDATED_ISIN":
                _set_status(df, idx, "SPDR_ETF", "REJECTED_ISIN", url)
                continue
            matched += 1
            _set_status(df, idx, "SPDR_ETF", "OFFICIAL_PRODUCT_ISIN_VALIDATED", url)
            applied += _apply_fields(df, idx, parsed, "SPDR_ETF", url, audit)
        except Exception as exc:
            _set_status(df, idx, "SPDR_ETF", f"ERROR_{type(exc).__name__}", search_url)
        time.sleep(0.2)
    return {"status": "OK", "attempted": attempted, "matched": matched, "applied": applied}


def _morningstar(session: requests.Session, df: pd.DataFrame, audit: dict) -> dict:
    matched = applied = 0
    for idx, row in df.iterrows():
        isin = str(row.get("isin") or "").upper()
        url = MORNINGSTAR_QUICKRANK + "&search=" + quote(isin)
        try:
            html = _response(session, url).text
            if isin not in html.upper():
                _set_status(df, idx, "MORNINGSTAR_FR", "PUBLIC_SEARCH_NOT_RESOLVED", url)
                continue
            page_url = _discover_product_link(html, url, isin, "morningstar") or url
            page = html if page_url == url else _response(session, page_url).text
            if isin not in page.upper():
                _set_status(df, idx, "MORNINGSTAR_FR", "REJECTED_ISIN", page_url)
                continue
            text = " ".join(BeautifulSoup(page, "html.parser").stripped_strings)
            matched += 1
            _set_status(df, idx, "MORNINGSTAR_FR", "PUBLIC_ISIN_VALIDATED", page_url)
            star = None
            for pattern in (r"Morningstar(?:\s+Overall)?\s+Rating\D{0,30}([1-5])", r"([1-5])\s*(?:étoiles|etoiles|stars)\b"):
                match = re.search(pattern, text, flags=re.I)
                if match:
                    star = int(match.group(1)); break
            if star is not None:
                applied += int(_fill(df, idx, "morningstar_rating", star, "MORNINGSTAR_FR", page_url, audit))
            category = _parse_label_value(text, ("Catégorie Morningstar", "Morningstar Category", "Catégorie"), 120)
            if category:
                applied += int(_fill(df, idx, "morningstar_category", category, "MORNINGSTAR_FR", page_url, audit))
            for years in (1, 3, 5):
                rank = _float(_parse_label_value(text, (f"Rang catégorie {years} an", f"Category Rank {years} Year", f"{years} ans"), 50))
                if rank is not None:
                    applied += int(_fill(df, idx, f"rank_cat_{years}y", rank, "MORNINGSTAR_FR", page_url, audit))
        except Exception as exc:
            _set_status(df, idx, "MORNINGSTAR_FR", f"ERROR_{type(exc).__name__}", url)
        time.sleep(0.12)
    return {"status": "OK", "attempted": len(df), "matched": matched, "applied": applied, "control": "No Morningstar internal identifier is guessed"}


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
        "policy": "OFFICIAL_IDENTITY_ISSUER_FIRST_MISSING_ONLY_NO_OVERWRITE",
        "applied_cells": 0,
        "skipped_existing": 0,
        "field_applied": {},
        "source_applied": {},
        "sources": {},
        "runtime_note": "Module is wired for a future manual workflow run; this repository audit did not execute network capture.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    session = requests.Session()

    audit["sources"]["OPENFIGI_V3"] = _openfigi(session, df, audit)
    audit["sources"]["EURONEXT_LIVE_PUBLIC"] = _euronext(session, df, audit)
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
    print("V21_1_ETF_PUBLIC_REFERENCE_OK", json.dumps({"rows": result["rows"], "applied_cells": result["applied_cells"], "sources": result["sources"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
