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
        "Accept": "application/json,text/plain,*/*",
    }


def _dates(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def _parse_efts_hits(payload: dict, start: date, end: date) -> list[dict]:
    rows: list[dict] = []
    hits = ((payload.get("hits") or {}).get("hits") or [])
    for hit in hits:
        source = hit.get("_source") or {}
        form = str(source.get("form_type") or source.get("root_form") or "").strip().upper()
        if form not in {"S-1", "F-1"}:
            continue
        filed = str(source.get("file_date") or source.get("display_date_filed") or "").strip()
        try:
            filed_date = date.fromisoformat(filed)
        except ValueError:
            continue
        if not start <= filed_date <= end:
            continue
        ciks = source.get("ciks") or []
        if isinstance(ciks, str):
            ciks = [ciks]
        if not ciks:
            continue
        cik = str(ciks[0]).strip()
        if not cik.isdigit():
            continue
        company = str(source.get("entity_name") or "").strip()
        if not company:
            display_names = source.get("display_names") or []
            company = str(display_names[0]).strip() if display_names else ""
        if not company:
            continue
        rows.append(
            {
                "form": form,
                "company": company,
                "cik": cik,
                "filed": filed_date.isoformat(),
                "filename": str(hit.get("_id") or ""),
            }
        )
    return rows


def _collect_efts(start: date, end: date, user_agent: str, timeout: int) -> tuple[list[dict], dict]:
    try:
        response = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q": "",
                "dateRange": "custom",
                "forms": "S-1,F-1",
                "startdt": start.isoformat(),
                "enddt": end.isoformat(),
                "from": 0,
                "size": 100,
            },
            headers=_headers(user_agent),
            timeout=timeout,
        )
        if response.status_code != 200:
            return [], {"status": "FAILED", "detail": f"HTTP_{response.status_code}"}
        payload = response.json()
        rows = _parse_efts_hits(payload, start, end)
        total = ((payload.get("hits") or {}).get("total") or {})
        total_value = total.get("value") if isinstance(total, dict) else total
        return rows, {"status": "SUCCESS", "detail": f"hits={len(rows)}|reported_total={total_value}"}
    except (requests.RequestException, ValueError, TypeError) as exc:
        return [], {"status": "FAILED", "detail": f"{type(exc).__name__}:{str(exc)[:120]}"}


def _collect_daily(start: date, end: date, user_agent: str, timeout: int) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    access_errors: list[str] = []
    missing_days = 0
    successful_days = 0
    consecutive_forbidden = 0
    stopped_early = False
    for day in _dates(start, end):
        url = (
            f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/"
            f"QTR{_quarter(day)}/form.{day:%Y%m%d}.idx"
        )
        try:
            response = requests.get(url, headers=_headers(user_agent), timeout=timeout)
            if response.status_code == 404:
                missing_days += 1
                consecutive_forbidden = 0
                continue
            if response.status_code == 403:
                access_errors.append(f"{day:%Y%m%d}:HTTP_403")
                consecutive_forbidden += 1
                if consecutive_forbidden >= 2:
                    stopped_early = True
                    break
                continue
            if response.status_code != 200:
                access_errors.append(f"{day:%Y%m%d}:HTTP_{response.status_code}")
                consecutive_forbidden = 0
                continue
            consecutive_forbidden = 0
            successful_days += 1
            rows.extend(parse_form_index(response.text))
        except requests.RequestException as exc:
            access_errors.append(f"{day:%Y%m%d}:{type(exc).__name__}")
            consecutive_forbidden = 0
        time.sleep(0.12)
    detail = (
        f"daily_success={successful_days}|daily_missing={missing_days}|"
        f"access_errors={len(access_errors)}|stopped_after_repeated_403={stopped_early}"
    )
    if access_errors:
        detail += "|" + "|".join(access_errors[:4])
    return rows, {"status": "SUCCESS" if successful_days else "FAILED", "detail": detail[:350]}


def _deduplicate(rows: list[dict], start: date, end: date) -> list[dict]:
    dedup: dict[tuple[str, str], dict] = {}
    for row in rows:
        try:
            filed = date.fromisoformat(str(row["filed"]))
        except (KeyError, ValueError, TypeError):
            continue
        if not start <= filed <= end:
            continue
        cik = str(row.get("cik") or "").strip()
        form = str(row.get("form") or "").strip().upper()
        if not cik.isdigit() or form not in {"S-1", "F-1"}:
            continue
        key = (str(int(cik)), form)
        if key not in dedup or str(row["filed"]) > str(dedup[key]["filed"]):
            dedup[key] = row
    return sorted(dedup.values(), key=lambda row: row["filed"], reverse=True)


def collect_recent_registrations(
    start: date,
    end: date,
    user_agent: str,
    timeout: int = 20,
) -> tuple[list[dict], dict]:
    """Discover initial S-1/F-1 registrations with hosted-runner failover.

    EFTS is attempted first because some hosted networks receive 403 responses
    from www.sec.gov archive paths. SEC daily indexes remain the fallback. The
    public-company CIK filter is best-effort and never suppresses all discovery
    merely because the association file is temporarily inaccessible.
    """
    efts_rows, efts_status = _collect_efts(start, end, user_agent, timeout)
    route = "EFTS"
    rows = efts_rows
    route_status = efts_status
    fallback_detail = ""
    if efts_status["status"] != "SUCCESS":
        rows, route_status = _collect_daily(start, end, user_agent, timeout)
        route = "DAILY_INDEX"
        fallback_detail = f"efts={efts_status.get('detail', '')}|"

    output = _deduplicate(rows, start, end)
    listed_ciks, listed_status = collect_listed_ciks(user_agent, timeout)
    before_filter = len(output)
    if listed_ciks:
        output = [row for row in output if str(int(row["cik"])) not in listed_ciks]
    filtered_listed = before_filter - len(output)

    if route_status["status"] != "SUCCESS":
        status = "FAILED"
    elif listed_status.get("status") != "SUCCESS":
        status = "PARTIAL"
    else:
        status = "SUCCESS"

    detail = (
        f"route={route}|{fallback_detail}{route_status.get('detail','')}|"
        f"listed_filter={listed_status.get('status')}|filtered_listed={filtered_listed}"
    )
    return output, {"source": "SEC_EDGAR", "status": status, "count": len(output), "detail": detail[:600]}


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
