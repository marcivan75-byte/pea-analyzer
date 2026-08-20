from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
import re
import time
from typing import Any

import pandas as pd


USER_AGENT = "PEA-Analyzer/21.16 (+governed ETF inception evidence)"
AMUNDI_FACTSHEET_URL = (
    "https://www.amundietf.fr/pdfDocuments/monthly-factsheet/"
    "{isin}/FRA/FRA/RETAIL/ETF/{yyyymmdd}"
)
JUSTETF_URL = "https://www.justetf.com/fr/etf-profile.html?isin={isin}"

FRENCH_MONTHS = {
    "janvier": "January",
    "février": "February",
    "fevrier": "February",
    "mars": "March",
    "avril": "April",
    "mai": "May",
    "juin": "June",
    "juillet": "July",
    "août": "August",
    "aout": "August",
    "septembre": "September",
    "octobre": "October",
    "novembre": "November",
    "décembre": "December",
    "decembre": "December",
}


def _clean_text(value: str) -> str:
    return re.sub(r"[\u00a0\u202f\t]+", " ", str(value or "")).replace("\r", "\n")


def _html_text(value: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(str(value or ""), "lxml").get_text(" ", strip=True)


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _month_ends(today: date, count: int = 4) -> list[str]:
    anchor = pd.Timestamp(today).replace(day=1) - pd.Timedelta(days=1)
    return [(anchor - pd.offsets.MonthEnd(i)).strftime("%Y%m%d") for i in range(count)]


def _normalise_date_token(token: str) -> str | None:
    raw = _clean_text(token).strip(" .,:;()")
    if not raw:
        return None
    lowered = raw.casefold()
    for french, english in FRENCH_MONTHS.items():
        lowered = re.sub(rf"\b{re.escape(french)}\b", english, lowered, flags=re.I)
    parsed = pd.to_datetime(lowered, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _date_after_labels(text: str, labels: tuple[str, ...]) -> str | None:
    clean = _clean_text(text)
    token = (
        r"([0-3]?\d/[01]?\d/20\d{2}|"
        r"20\d{2}-[01]?\d-[0-3]?\d|"
        r"[0-3]?\d\s+[A-Za-zÀ-ÿ]+\s+20\d{2})"
    )
    for label in labels:
        match = re.search(label + r".{0,80}?" + token, clean, flags=re.I | re.S)
        if match:
            parsed = _normalise_date_token(match.group(1))
            if parsed:
                return parsed
    return None


def _observation(
    isin: str,
    field: str,
    value: str,
    *,
    source: str,
    source_url: str,
    evidence: str,
    as_of: str,
) -> dict[str, Any]:
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


def _issuer_observations_from_text(
    isin: str,
    text: str,
    *,
    source: str,
    source_url: str,
    as_of: str,
) -> list[dict[str, Any]]:
    clean = _clean_text(text)
    if isin.upper() not in clean.upper():
        return []
    out: list[dict[str, Any]] = []
    class_date = _date_after_labels(
        clean,
        (
            r"Date de création de la classe",
            r"Share Class (?:Inception|Launch) Date",
            r"Share class inception",
        ),
    )
    if class_date:
        out.append(
            _observation(
                isin,
                "share_class_inception_date",
                class_date,
                source=source,
                source_url=source_url,
                evidence="A",
                as_of=as_of,
            )
        )
    first_nav = _date_after_labels(
        clean,
        (r"Date de la première VL", r"First NAV Date", r"First NAV"),
    )
    if first_nav:
        out.append(
            _observation(
                isin,
                "reported_first_nav_date",
                first_nav,
                source=source,
                source_url=source_url,
                evidence="A",
                as_of=as_of,
            )
        )
    return out


def _justetf_observations_from_text(
    isin: str,
    text: str,
    *,
    source_url: str,
    as_of: str,
) -> list[dict[str, Any]]:
    clean = _clean_text(text)
    if isin.upper() not in clean.upper():
        return []
    launch = _date_after_labels(
        clean,
        (
            r"Date de lancement / première cotation",
            r"Date de lancement",
            r"Launch date / first listing",
            r"Launch date",
        ),
    )
    if not launch:
        return []
    return [
        _observation(
            isin,
            "listing_or_launch_date",
            launch,
            source="justETF exact-ISIN profile",
            source_url=source_url,
            evidence="B",
            as_of=as_of,
        )
    ]


def _get(session, url: str, *, timeout: int = 25):
    return session.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,fr;q=0.9"},
        timeout=timeout,
    )


def _collect_amundi(session, isin: str, today: date) -> tuple[list[dict], list[dict]]:
    failures: list[dict] = []
    for yyyymmdd in _month_ends(today):
        url = AMUNDI_FACTSHEET_URL.format(isin=isin, yyyymmdd=yyyymmdd)
        try:
            response = _get(session, url)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            observations = _issuer_observations_from_text(
                isin,
                _pdf_text(response.content),
                source="Amundi official monthly factsheet",
                source_url=url,
                as_of=datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat(),
            )
            if observations:
                return observations, failures
            failures.append(
                {
                    "isin": isin,
                    "provider": "Amundi",
                    "reason": "OFFICIAL_FACTSHEET_NO_EXACT_INCEPTION_FIELDS",
                    "source_url": url,
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "isin": isin,
                    "provider": "Amundi",
                    "reason": type(exc).__name__,
                    "detail": str(exc)[:180],
                    "source_url": url,
                }
            )
    return [], failures


def _collect_justetf(session, isin: str, today: date) -> tuple[list[dict], list[dict]]:
    url = JUSTETF_URL.format(isin=isin)
    try:
        response = _get(session, url)
        response.raise_for_status()
        observations = _justetf_observations_from_text(
            isin,
            _html_text(response.text),
            source_url=url,
            as_of=today.isoformat(),
        )
        if observations:
            return observations, []
        return [], [
            {
                "isin": isin,
                "reason": "JUSTETF_NO_EXACT_LAUNCH_DATE",
                "source_url": url,
            }
        ]
    except Exception as exc:
        return [], [
            {
                "isin": isin,
                "reason": type(exc).__name__,
                "detail": str(exc)[:180],
                "source_url": url,
            }
        ]


def collect_etf_inception_data(
    frame: pd.DataFrame,
    *,
    today: date | None = None,
    delay_seconds: float = 0.05,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Collect exact-ISIN inception evidence without changing calibration eligibility.

    Issuer share-class creation is evidence A and is preferred. justETF launch / first
    listing is evidence B fallback. `reported_first_nav_date` is context only and must
    never be used as authority when a newer share class was created later.
    """
    import requests

    current = today or datetime.now(timezone.utc).date()
    if "isin" not in frame.columns:
        return [], [{"reason": "ETF_MASTER_MISSING_ISIN"}], {"requested": 0}

    session = requests.Session()
    observations: list[dict] = []
    failures: list[dict] = []
    provider_metrics: dict[str, dict[str, int]] = {}

    for _, row in frame.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        provider = str(row.get("provider") or "UNKNOWN").strip()
        if not isin:
            failures.append({"provider": provider, "reason": "MISSING_ISIN"})
            continue
        metrics = provider_metrics.setdefault(
            provider,
            {"requested": 0, "issuer_observations": 0, "fallback_observations": 0},
        )
        metrics["requested"] += 1

        issuer_obs: list[dict] = []
        issuer_failures: list[dict] = []
        if provider.casefold() == "amundi":
            issuer_obs, issuer_failures = _collect_amundi(session, isin, current)
        observations.extend(issuer_obs)
        failures.extend(issuer_failures)
        metrics["issuer_observations"] += len(issuer_obs)

        observed_fields = {item["field"] for item in issuer_obs}
        if "share_class_inception_date" not in observed_fields:
            fallback_obs, fallback_failures = _collect_justetf(session, isin, current)
            observations.extend(fallback_obs)
            failures.extend(fallback_failures)
            metrics["fallback_observations"] += len(fallback_obs)

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    unique_fields = {(item["isin"], item["field"]) for item in observations}
    summary = {
        "requested": int(len(frame)),
        "observations": int(len(observations)),
        "unique_isin_fields": int(len(unique_fields)),
        "share_class_inception_observations": sum(
            item["field"] == "share_class_inception_date" for item in observations
        ),
        "listing_or_launch_observations": sum(
            item["field"] == "listing_or_launch_date" for item in observations
        ),
        "reported_first_nav_observations": sum(
            item["field"] == "reported_first_nav_date" for item in observations
        ),
        "evidence_a_observations": sum(
            item["evidence_level"] == "A" for item in observations
        ),
        "evidence_b_observations": sum(
            item["evidence_level"] == "B" for item in observations
        ),
        "failures": int(len(failures)),
        "provider_metrics": provider_metrics,
        "governance": {
            "exact_isin_required": True,
            "issuer_share_class_date_preferred": True,
            "reported_first_nav_context_only": True,
            "justetf_launch_fallback_evidence": "B",
            "calibration_eligibility_changed": False,
            "synthetic_pre_inception_history": False,
            "stress_calibration_weight": 0.0,
        },
    }
    return observations, failures, summary
