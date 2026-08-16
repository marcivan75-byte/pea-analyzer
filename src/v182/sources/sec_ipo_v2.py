from __future__ import annotations

from datetime import date, timedelta
import time

import requests

from v182.sources.sec_ipo import (
    collect_listed_ciks,
    enrich_candidate,
    financial_scores,
    match_registration,
    parse_form_index,
    prospectus_text_scores,
    registration_candidates,
)


def _quarter(value: date) -> int:
    return (value.month - 1) // 3 + 1


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "text/plain,application/json,*/*",
    }


def _dates(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def collect_recent_registrations(
    start: date,
    end: date,
    user_agent: str,
    timeout: int = 20,
) -> tuple[list[dict], dict]:
    """Discover S-1/F-1 filings from SEC daily indexes.

    The quarterly full-index is a completed-quarter artifact. Daily indexes are
    the appropriate source for the active quarter and also work across quarter
    boundaries. Missing daily files (weekends/holidays/not-yet-published dates)
    are non-errors; access/server failures are reported explicitly.
    """
    rows: list[dict] = []
    access_errors: list[str] = []
    missing_days = 0
    successful_days = 0

    for day in _dates(start, end):
        url = (
            f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/"
            f"QTR{_quarter(day)}/form.{day:%Y%m%d}.idx"
        )
        try:
            response = requests.get(url, headers=_headers(user_agent), timeout=timeout)
            if response.status_code == 404:
                missing_days += 1
                continue
            if response.status_code != 200:
                access_errors.append(f"{day:%Y%m%d}:HTTP_{response.status_code}")
                continue
            successful_days += 1
            rows.extend(parse_form_index(response.text))
        except requests.RequestException as exc:
            access_errors.append(f"{day:%Y%m%d}:{type(exc).__name__}")
        time.sleep(0.12)

    dedup: dict[tuple[str, str], dict] = {}
    for row in rows:
        filed = date.fromisoformat(row["filed"])
        if not start <= filed <= end:
            continue
        key = (str(int(row["cik"])), row["form"])
        if key not in dedup or row["filed"] > dedup[key]["filed"]:
            dedup[key] = row
    output = sorted(dedup.values(), key=lambda row: row["filed"], reverse=True)

    listed_ciks, listed_status = collect_listed_ciks(user_agent, timeout)
    before_filter = len(output)
    if listed_ciks:
        output = [row for row in output if str(int(row["cik"])) not in listed_ciks]
    filtered_listed = before_filter - len(output)

    if successful_days == 0:
        status = "FAILED"
    elif access_errors or listed_status.get("status") != "SUCCESS":
        status = "PARTIAL"
    else:
        status = "SUCCESS"

    detail = (
        f"daily_success={successful_days}|daily_missing={missing_days}|"
        f"access_errors={len(access_errors)}|listed_filter={listed_status.get('status')}|"
        f"filtered_listed={filtered_listed}"
    )
    if access_errors:
        detail += "|" + "|".join(access_errors[:4])

    return output, {
        "source": "SEC_EDGAR",
        "status": status,
        "count": len(output),
        "detail": detail[:400],
    }


__all__ = [
    "collect_recent_registrations",
    "registration_candidates",
    "match_registration",
    "enrich_candidate",
    "parse_form_index",
    "collect_listed_ciks",
    "prospectus_text_scores",
    "financial_scores",
]
