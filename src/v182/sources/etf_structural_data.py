from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
import math
import re
import time
from typing import Any

import pandas as pd


USER_AGENT = "PEA-Analyzer/21.10 (+governed ETF structural data audit)"
JUSTETF_URL = "https://www.justetf.com/en-be/etf-profile.html?isin={isin}"
AMUNDI_FACTSHEET_URL = (
    "https://www.amundietf.fr/pdfDocuments/monthly-factsheet/"
    "{isin}/FRA/FRA/RETAIL/ETF/{yyyymmdd}"
)
HSBC_FACTSHEET_URL = (
    "https://fi.assetmanagement.hsbc.com/api/v1/download/document/"
    "{isin_lower}/fi/en/factsheet"
)

# Small providers whose official page does not expose a stable ISIN-only route.
# Every response is still validated against the exact ISIN before extraction.
OFFICIAL_HTML_URLS: dict[str, str] = {
    "IE00B910VR50": "https://www.ssga.com/fr/fr/intermediary/etfs/state-street-spdr-msci-emu-ucits-etf-zpre-gy",
    "IE00B5M1WJ87": "https://www.ssga.com/fr/fr/intermediary/etfs/state-street-spdr-sp-euro-dividend-aristocrats-ucits-etf-dist-spyw-gy",
    "IE00BKX55S42": "https://www.vanguard.co.uk/professional/product/etf/equity/9524/ftse-developed-europe-ex-uk-ucits-etf-eur-distributing",
}

PCT_LABELS = (
    r"Frais de gestion et autres coûts administratifs ou d[’']exploitation",
    r"Frais courants(?: réels)?",
    r"Ongoing charge figure",
    r"Ongoing charges figure",
    r"Total expense ratio",
    r"Total des Frais sur Encours",
    r"\bTER\b",
)
FUND_ASSET_LABELS = (
    r"Actif géré",
    r"Actif du compartiment",
    r"Fund size",
    r"Assets Under Management",
    r"Total Assets",
)
SHARE_CLASS_ASSET_LABELS = (r"Share Class Assets'?",)


def _clean_text(value: str) -> str:
    return re.sub(r"[\u00a0\u202f\t]+", " ", str(value or "")).replace("\r", "\n")


def _html_text(value: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(str(value or ""), "lxml").get_text(" ", strip=True)


def _localized_number(token: str, *, percent: bool = False) -> float | None:
    text = _clean_text(token).strip().replace("'", "")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if percent:
            text = text.replace(",", ".")
        elif text.count(",") > 1:
            text = text.replace(",", "")
        else:
            left, right = text.split(",", 1)
            # English fund sizes commonly use one thousands separator: 1,092 m.
            text = left + right if len(right) == 3 and len(left.lstrip("-")) <= 3 else left + "." + right
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _first_pct(text: str, labels: tuple[str, ...] = PCT_LABELS) -> float | None:
    clean = _clean_text(text)
    for label in labels:
        match = re.search(label + r".{0,100}?([0-9]+(?:[.,][0-9]+)?)\s*%", clean, flags=re.I | re.S)
        if match:
            value = _localized_number(match.group(1), percent=True)
            if value is not None and 0 <= value <= 10:
                return round(value, 6)
    return None


def _eur_m_after_label(text: str, labels: tuple[str, ...]) -> float | None:
    clean = _clean_text(text)
    for label in labels:
        start = re.search(label, clean, flags=re.I)
        if not start:
            continue
        snippet = clean[start.end(): start.end() + 180]

        # Currency before value, optional explicit scale: EUR 1,092 m / €4.76 B.
        before = re.search(
            r"(?:EUR|€)\s*([0-9][0-9 .,'\u00a0\u202f]*)\s*(bn|billion|milliards?|b|mn|millions?|m)?\b",
            snippet,
            flags=re.I,
        )
        if before:
            number = _localized_number(before.group(1))
            scale = (before.group(2) or "").lower()
            if number is not None:
                if scale in {"bn", "billion", "b", "milliard", "milliards"}:
                    return round(number * 1000.0, 6)
                if scale in {"mn", "million", "millions", "m"}:
                    return round(number, 6)
                # No scale means an absolute EUR amount, never a unitless guess.
                if number >= 1_000_000:
                    return round(number / 1_000_000.0, 6)

        # Value before unit/currency: 4 466,14 ( millions EUR ).
        after = re.search(
            r"([0-9][0-9 .,'\u00a0\u202f]*)\s*\(?\s*(bn|billion|milliards?|b|mn|millions?|m)\s+EUR\s*\)?",
            snippet,
            flags=re.I,
        )
        if after:
            number = _localized_number(after.group(1))
            scale = after.group(2).lower()
            if number is not None:
                return round(number * 1000.0, 6) if scale in {"bn", "billion", "b", "milliard", "milliards"} else round(number, 6)
    return None


def _source_date(text: str, fallback: str) -> str:
    clean = _clean_text(text)
    patterns = (
        r"Date de VL et d['’]actif géré\s*([0-3]?\d/[01]?\d/20\d{2})",
        r"(?:data as at|as of|au)\s*([0-3]?\d\s+[A-Za-zÀ-ÿ]+\s+20\d{2})",
        r"([0-3]?\d/[01]?\d/20\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.I)
        if not match:
            continue
        parsed = pd.to_datetime(match.group(1), errors="coerce", dayfirst=True)
        if pd.notna(parsed):
            return parsed.date().isoformat()
    return fallback


def _observation(isin: str, field: str, value: Any, *, source: str, source_url: str, evidence: str, as_of: str) -> dict:
    return {
        "universe": "ETF",
        "isin": isin,
        "field": field,
        "value": value,
        "source": source,
        "source_url": source_url,
        "evidence_level": evidence,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "validation_status": "EXACT_ISIN_SOURCE_MATCH",
    }


def _observations_from_text(
    isin: str,
    text: str,
    *,
    source: str,
    source_url: str,
    evidence: str,
    fallback_as_of: str,
    include_share_class_assets: bool = False,
) -> list[dict]:
    clean = _clean_text(text)
    if isin.upper() not in clean.upper():
        return []
    as_of = _source_date(clean, fallback_as_of)
    out: list[dict] = []
    ter = _first_pct(clean)
    if ter is not None:
        out.append(_observation(isin, "ter_pct", ter, source=source, source_url=source_url, evidence=evidence, as_of=as_of))
    assets = _eur_m_after_label(clean, FUND_ASSET_LABELS)
    if assets is not None:
        out.append(_observation(isin, "fund_total_assets_eur_m", assets, source=source, source_url=source_url, evidence=evidence, as_of=as_of))
    if include_share_class_assets:
        share_assets = _eur_m_after_label(clean, SHARE_CLASS_ASSET_LABELS)
        if share_assets is not None:
            out.append(_observation(isin, "aum_m", share_assets, source=source, source_url=source_url, evidence=evidence, as_of=as_of))
    return out


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _month_ends(today: date, count: int = 4) -> list[str]:
    anchor = pd.Timestamp(today).replace(day=1) - pd.Timedelta(days=1)
    return [(anchor - pd.offsets.MonthEnd(i)).strftime("%Y%m%d") for i in range(count)]


def _get(session, url: str, *, timeout: int = 25):
    return session.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,fr;q=0.9"}, timeout=timeout)


def _collect_amundi(session, isin: str, today: date) -> tuple[list[dict], list[dict]]:
    failures: list[dict] = []
    for yyyymmdd in _month_ends(today):
        url = AMUNDI_FACTSHEET_URL.format(isin=isin, yyyymmdd=yyyymmdd)
        try:
            response = _get(session, url)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            text = _pdf_text(response.content)
            observations = _observations_from_text(
                isin,
                text,
                source="Amundi official monthly factsheet",
                source_url=url,
                evidence="A",
                fallback_as_of=datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat(),
            )
            if observations:
                return observations, failures
            failures.append({"isin": isin, "provider": "Amundi", "reason": "OFFICIAL_FACTSHEET_NO_EXACT_STRUCTURAL_FIELDS", "source_url": url})
        except Exception as exc:
            failures.append({"isin": isin, "provider": "Amundi", "reason": type(exc).__name__, "detail": str(exc)[:180], "source_url": url})
    return [], failures


def _collect_hsbc(session, isin: str, today: date) -> tuple[list[dict], list[dict]]:
    url = HSBC_FACTSHEET_URL.format(isin_lower=isin.lower())
    try:
        response = _get(session, url)
        response.raise_for_status()
        text = _pdf_text(response.content)
        observations = _observations_from_text(
            isin,
            text,
            source="HSBC Asset Management official factsheet",
            source_url=url,
            evidence="A",
            fallback_as_of=today.isoformat(),
        )
        if observations:
            return observations, []
        return [], [{"isin": isin, "provider": "HSBC", "reason": "OFFICIAL_FACTSHEET_NO_EXACT_STRUCTURAL_FIELDS", "source_url": url}]
    except Exception as exc:
        return [], [{"isin": isin, "provider": "HSBC", "reason": type(exc).__name__, "detail": str(exc)[:180], "source_url": url}]


def _collect_official_html(session, isin: str, today: date) -> tuple[list[dict], list[dict]]:
    url = OFFICIAL_HTML_URLS.get(isin)
    if not url:
        return [], []
    try:
        response = _get(session, url)
        response.raise_for_status()
        observations = _observations_from_text(
            isin,
            _html_text(response.text),
            source="Issuer official product page",
            source_url=url,
            evidence="A",
            fallback_as_of=today.isoformat(),
            include_share_class_assets="vanguard.co.uk" in url,
        )
        if observations:
            return observations, []
        return [], [{"isin": isin, "reason": "OFFICIAL_HTML_NO_EXACT_STRUCTURAL_FIELDS", "source_url": url}]
    except Exception as exc:
        return [], [{"isin": isin, "reason": type(exc).__name__, "detail": str(exc)[:180], "source_url": url}]


def _collect_justetf(session, isin: str, today: date, wanted_fields: set[str]) -> tuple[list[dict], list[dict]]:
    url = JUSTETF_URL.format(isin=isin)
    try:
        response = _get(session, url)
        response.raise_for_status()
        parsed = _observations_from_text(
            isin,
            _html_text(response.text),
            source="justETF exact-ISIN profile",
            source_url=url,
            evidence="B",
            fallback_as_of=today.isoformat(),
        )
        observations = [row for row in parsed if row["field"] in wanted_fields]
        if observations:
            return observations, []
        return [], [{"isin": isin, "reason": "JUSTETF_NO_EXACT_STRUCTURAL_FIELDS", "source_url": url}]
    except Exception as exc:
        return [], [{"isin": isin, "reason": type(exc).__name__, "detail": str(exc)[:180], "source_url": url}]


def collect_etf_structural_data(
    frame: pd.DataFrame,
    *,
    today: date | None = None,
    delay_seconds: float = 0.05,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Collect TER and explicit-EUR fund size with an exact-ISIN fail-closed policy.

    Evidence A issuer pages/factsheets are attempted first where an adapter exists.
    justETF is evidence B and fills only fields not observed from the issuer adapter.
    No FX conversion is performed. Missing values remain missing.
    """
    import requests

    current = today or datetime.now(timezone.utc).date()
    session = requests.Session()
    observations: list[dict] = []
    failures: list[dict] = []
    provider_metrics: dict[str, dict[str, int]] = {}

    if "isin" not in frame.columns:
        return [], [{"reason": "ETF_MASTER_MISSING_ISIN"}], {"requested": 0}

    for _, row in frame.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        provider = str(row.get("provider") or "UNKNOWN").strip()
        if not isin:
            failures.append({"provider": provider, "reason": "MISSING_ISIN"})
            continue
        metrics = provider_metrics.setdefault(provider, {"requested": 0, "issuer_observations": 0, "fallback_observations": 0})
        metrics["requested"] += 1

        issuer_obs: list[dict] = []
        issuer_failures: list[dict] = []
        if provider.casefold() == "amundi":
            issuer_obs, issuer_failures = _collect_amundi(session, isin, current)
        elif provider.casefold() == "hsbc":
            issuer_obs, issuer_failures = _collect_hsbc(session, isin, current)
        elif isin in OFFICIAL_HTML_URLS:
            issuer_obs, issuer_failures = _collect_official_html(session, isin, current)

        observations.extend(issuer_obs)
        failures.extend(issuer_failures)
        metrics["issuer_observations"] += len(issuer_obs)
        observed_fields = {item["field"] for item in issuer_obs}
        wanted = {"ter_pct", "fund_total_assets_eur_m"} - observed_fields
        if wanted:
            fallback_obs, fallback_failures = _collect_justetf(session, isin, current, wanted)
            observations.extend(fallback_obs)
            failures.extend(fallback_failures)
            metrics["fallback_observations"] += len(fallback_obs)
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    unique_fields = {(item["isin"], item["field"]) for item in observations}
    summary = {
        "requested": int(len(frame)),
        "observations": len(observations),
        "unique_isin_fields": len(unique_fields),
        "ter_observations": sum(item["field"] == "ter_pct" for item in observations),
        "fund_assets_eur_observations": sum(item["field"] == "fund_total_assets_eur_m" for item in observations),
        "share_class_aum_observations": sum(item["field"] == "aum_m" for item in observations),
        "evidence_a_observations": sum(item["evidence_level"] == "A" for item in observations),
        "evidence_b_observations": sum(item["evidence_level"] == "B" for item in observations),
        "failures": len(failures),
        "provider_metrics": provider_metrics,
        "governance": {
            "exact_isin_required": True,
            "issuer_before_secondary": True,
            "issuer_evidence": "A",
            "justetf_evidence": "B",
            "fx_conversion": False,
            "quote_currency_used_as_asset_currency": False,
            "missing_imputation": False,
        },
    }
    return observations, failures, summary
