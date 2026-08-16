from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import json
import math
import os
import re
import unicodedata

import pandas as pd
import requests

from v182.sources import sec_ipo

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "IPO_RADAR_V1.json"
ENRICHMENT_PATH = ROOT / "inputs" / "IPO_ENRICHMENT.csv"

EEA_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT",
    "LV", "LI", "LT", "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE"
}
DECISION_RANK = {
    "PRIORITY_DD": 0,
    "DEEP_DD": 1,
    "WATCH": 2,
    "WATCH_EARLY_FILING": 3,
    "WATCH_DATA_GAP": 4,
    "AVOID_OR_LOW_EDGE": 5,
    "AVOID_HIGH_RISK": 6,
    "AVOID_HARD_BLOCK": 7,
    "AVOID_WITHDRAWN": 8,
}


def _load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


def _norm_name(value: object) -> str:
    text = "" if value is None else str(value).upper()
    text = re.sub(r"\b(INCORPORATED|INC|CORPORATION|CORP|LIMITED|LTD|PLC|S A|SA|N V|NV|HOLDINGS?|GROUP)\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", "", text)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "n/a", "na", "-"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def parse_price_range(value: object) -> tuple[float | None, float | None, float | None]:
    if value is None:
        return None, None, None
    text = str(value).replace(",", "")
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None, None, None
    low = numbers[0]
    high = numbers[1] if len(numbers) > 1 else numbers[0]
    if high < low:
        low, high = high, low
    return low, high, (low + high) / 2.0


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "n/a", "na", "-"}:
        return None
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=False)
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _candidate_id(row: dict) -> str:
    exchange = _norm_text(row.get("exchange")) or "UNKNOWN"
    symbol = _norm_text(row.get("symbol"))
    name = _norm_text(row.get("name"))
    cik = str(row.get("sec_cik") or "").strip()
    if symbol and exchange not in {"UNKNOWN", "SECPRIVATE"}:
        return f"{exchange}:{symbol}"
    if cik:
        return f"SEC:{cik}"
    return f"{exchange}:{symbol or name}"


def _identity_key(row: dict) -> str:
    cik = str(row.get("sec_cik") or "").strip()
    if cik:
        return f"CIK:{int(cik)}"
    isin = _norm_text(row.get("isin"))
    if isin:
        return f"ISIN:{isin}"
    name = _norm_name(row.get("name"))
    if name:
        return f"NAME:{name}"
    return f"CID:{row.get('candidate_id', '')}"


def _standard_candidate(
    *,
    name: object,
    symbol: object = None,
    exchange: object = None,
    expected_date: object = None,
    status: object = None,
    price_range: object = None,
    number_of_shares: object = None,
    offer_value: object = None,
    issuer_country: object = None,
    source: str,
    **extra: object,
) -> dict:
    low, high, mid = parse_price_range(price_range)
    candidate = {
        "name": "" if name is None else str(name).strip(),
        "symbol": "" if symbol is None else str(symbol).strip(),
        "exchange": "" if exchange is None else str(exchange).strip(),
        "expected_date": _parse_date(expected_date).isoformat() if _parse_date(expected_date) else "",
        "status": "" if status is None else str(status).strip().lower(),
        "price_range": "" if price_range is None else str(price_range).strip(),
        "price_low": low,
        "price_high": high,
        "price_mid": mid,
        "number_of_shares": _as_float(number_of_shares),
        "offer_value": _as_float(offer_value),
        "issuer_country": "" if issuer_country is None else str(issuer_country).strip().upper(),
        "sources": source,
        "source_count": 1,
        **extra,
    }
    candidate["candidate_id"] = _candidate_id(candidate)
    candidate["identity_key"] = _identity_key(candidate)
    return candidate


def _http_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer-IPO-Radar/1.1)",
        "Accept": "application/json,text/html,application/xhtml+xml",
    }


def collect_finnhub(start: date, end: date, api_key: str | None, timeout: int = 20) -> tuple[list[dict], dict]:
    source = "FINNHUB"
    if not api_key:
        return [], {"source": source, "status": "SKIPPED_MISSING_KEY", "count": 0}
    try:
        response = requests.get(
            "https://finnhub.io/api/v1/calendar/ipo",
            params={"from": start.isoformat(), "to": end.isoformat(), "token": api_key},
            headers=_http_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        rows = response.json().get("ipoCalendar") or []
        candidates = [
            _standard_candidate(
                name=row.get("name"), symbol=row.get("symbol"), exchange=row.get("exchange"), expected_date=row.get("date"),
                status=row.get("status"), price_range=row.get("price"), number_of_shares=row.get("numberOfShares"),
                offer_value=row.get("totalSharesValue"), source=source,
            )
            for row in rows if row.get("name") or row.get("symbol")
        ]
        return candidates, {"source": source, "status": "SUCCESS", "count": len(candidates)}
    except Exception as exc:
        return [], {"source": source, "status": "FAILED", "count": 0, "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _month_starts(start: date, end: date) -> list[date]:
    current = start.replace(day=1)
    months: list[date] = []
    while current <= end:
        months.append(current)
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def collect_nasdaq(start: date, end: date, timeout: int = 20) -> tuple[list[dict], dict]:
    source = "NASDAQ"
    candidates: list[dict] = []
    errors: list[str] = []
    for month in _month_starts(start, end):
        try:
            response = requests.get(
                "https://api.nasdaq.com/api/ipo/calendar",
                params={"date": month.strftime("%Y-%m")}, headers=_http_headers(), timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json().get("data") or {}
            for bucket_name in ("upcoming", "filings"):
                bucket = payload.get(bucket_name) or {}
                for row in bucket.get("rows") or []:
                    event_date = row.get("expectedDate") or row.get("filedDate") or row.get("date")
                    parsed = _parse_date(event_date)
                    if parsed and not (start <= parsed <= end):
                        continue
                    candidates.append(
                        _standard_candidate(
                            name=row.get("companyName") or row.get("company"),
                            symbol=row.get("proposedTickerSymbol") or row.get("symbol"),
                            exchange=row.get("proposedExchange") or "NASDAQ",
                            expected_date=event_date if bucket_name == "upcoming" else None,
                            status="expected" if bucket_name == "upcoming" else "filed",
                            price_range=row.get("proposedSharePrice") or row.get("expectedPrice"),
                            number_of_shares=row.get("sharesOffered"),
                            offer_value=row.get("dollarValueOfSharesOffered") or row.get("dealSize"),
                            source=source,
                        )
                    )
        except Exception as exc:
            errors.append(f"{month:%Y-%m}:{type(exc).__name__}")
    status = "SUCCESS" if not errors else "PARTIAL" if candidates else "FAILED"
    return candidates, {"source": source, "status": status, "count": len(candidates), "detail": "|".join(errors)[:240]}


def collect_euronext(start: date, end: date, timeout: int = 20) -> tuple[list[dict], dict]:
    source = "EURONEXT"
    try:
        response = requests.get("https://live.euronext.com/en/ipo-showcase/all", headers=_http_headers(), timeout=timeout)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        candidates: list[dict] = []
        for table in tables:
            columns = {str(col).strip().lower(): col for col in table.columns}
            if "company name" not in columns or "date" not in columns:
                continue
            for _, row in table.iterrows():
                parsed = _parse_date(row.get(columns["date"]))
                if not parsed or not (start <= parsed <= end):
                    continue
                isin = str(row.get(columns.get("isin code", ""), "") or "").strip()
                location = str(row.get(columns.get("location", ""), "") or "").strip()
                market = str(row.get(columns.get("market", ""), "") or "").strip()
                candidates.append(
                    _standard_candidate(
                        name=row.get(columns["company name"]),
                        symbol=row.get(columns.get("ticker", "")) if "ticker" in columns else None,
                        exchange=market or "EURONEXT",
                        expected_date=parsed,
                        status="expected",
                        source=source,
                        isin=isin,
                        euronext_location=location,
                        issuer_country_hint=isin[:2].upper() if len(isin) >= 2 else "",
                    )
                )
        return candidates, {"source": source, "status": "SUCCESS", "count": len(candidates)}
    except Exception as exc:
        return [], {"source": source, "status": "FAILED", "count": 0, "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _meaningful(field: str, value: object) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    if field == "exchange" and str(value).upper() in {"SEC_PRIVATE", "UNKNOWN"}:
        return False
    return True


def deduplicate_candidates(rows: list[dict], source_priority: dict[str, int]) -> list[dict]:
    merged: dict[str, dict] = {}
    for original in rows:
        row = dict(original)
        name_key = _norm_name(row.get("name"))
        key = f"NAME:{name_key}" if name_key else row.get("candidate_id") or _candidate_id(row)
        if key not in merged:
            merged[key] = row
            continue
        current = merged[key]
        current_sources = set(filter(None, str(current.get("sources", "")).split("|")))
        incoming_sources = set(filter(None, str(row.get("sources", "")).split("|")))
        all_sources = current_sources | incoming_sources
        incoming_priority = max((source_priority.get(src, 0) for src in incoming_sources), default=0)
        current_priority = max((source_priority.get(src, 0) for src in current_sources), default=0)
        for field, incoming in row.items():
            if field in {"sources", "source_count", "candidate_id", "identity_key"}:
                continue
            existing = current.get(field)
            if not _meaningful(field, incoming):
                continue
            if not _meaningful(field, existing) or incoming_priority > current_priority:
                current[field] = incoming
        statuses = {str(current.get("status", "")).lower(), str(row.get("status", "")).lower()}
        if "withdrawn" in statuses:
            current["status"] = "withdrawn"
        elif "priced" in statuses:
            current["status"] = "priced"
        elif "expected" in statuses:
            current["status"] = "expected"
        elif "filed" in statuses:
            current["status"] = "filed"
        current["sources"] = "|".join(sorted(all_sources, key=lambda src: source_priority.get(src, 0), reverse=True))
        current["source_count"] = len(all_sources)
    output = []
    for row in merged.values():
        row["candidate_id"] = _candidate_id(row)
        row["identity_key"] = _identity_key(row)
        output.append(row)
    return output


def _read_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _previous_map(history: pd.DataFrame) -> dict[str, dict]:
    if history.empty:
        return {}
    key_col = "identity_key" if "identity_key" in history.columns else "candidate_id"
    if key_col not in history.columns:
        return {}
    if "observed_at_utc" in history.columns:
        history = history.sort_values("observed_at_utc")
    latest = history.groupby(key_col, dropna=False).tail(1)
    return {str(row[key_col]): row.to_dict() for _, row in latest.iterrows()}


def _offer_liquidity_score(offer_value: object) -> float | None:
    value = _as_float(offer_value)
    if value is None or value <= 0:
        return None
    if value >= 1_000_000_000:
        return 95.0
    if value >= 500_000_000:
        return 88.0
    if value >= 200_000_000:
        return 78.0
    if value >= 100_000_000:
        return 68.0
    if value >= 50_000_000:
        return 55.0
    if value >= 20_000_000:
        return 42.0
    return 28.0


def _range_width_pct(row: dict) -> float | None:
    low, high, mid = (_as_float(row.get("price_low")), _as_float(row.get("price_high")), _as_float(row.get("price_mid")))
    if low is None or high is None or mid is None or mid <= 0:
        return None
    return (high - low) / mid * 100.0


def _derive_history_signals(row: dict, previous: dict | None) -> dict[str, float | None]:
    liquidity = _offer_liquidity_score(row.get("offer_value"))
    small_float_risk = None if liquidity is None else 100.0 - liquidity
    bookbuilding = None
    revision_pct = None
    date_shift_days = None
    if previous:
        prev_mid = _as_float(previous.get("price_mid"))
        now_mid = _as_float(row.get("price_mid"))
        if prev_mid and now_mid:
            revision_pct = (now_mid / prev_mid - 1.0) * 100.0
            bookbuilding = 95.0 if revision_pct >= 10 else 85.0 if revision_pct >= 5 else 65.0 if revision_pct >= 0 else 48.0 if revision_pct >= -5 else 32.0 if revision_pct >= -10 else 18.0
        prev_date = _parse_date(previous.get("expected_date"))
        now_date = _parse_date(row.get("expected_date"))
        if prev_date and now_date:
            date_shift_days = float((now_date - prev_date).days)
    instability = 20.0
    status = str(row.get("status", "")).lower()
    if status == "withdrawn":
        instability = 100.0
    elif status == "filed":
        instability = 45.0
    elif status == "priced":
        instability = 10.0
    width = _range_width_pct(row)
    if width is not None:
        instability += max(0.0, min(25.0, (width - 10.0) * 1.25))
    if date_shift_days is not None and date_shift_days > 7:
        instability += min(25.0, date_shift_days)
    if revision_pct is not None and revision_pct < -5:
        instability += min(20.0, abs(revision_pct))
    return {
        "bookbuilding_demand": bookbuilding,
        "float_liquidity": liquidity,
        "small_float_liquidity": small_float_risk,
        "deal_instability": min(100.0, instability),
        "price_revision_pct": revision_pct,
        "date_shift_days": date_shift_days,
    }


def market_readiness_score(row: dict) -> float:
    status_map = {"priced": 95.0, "expected": 78.0, "filed": 45.0, "withdrawn": 0.0}
    status = status_map.get(str(row.get("status", "")).lower(), 45.0)
    exchange = str(row.get("exchange", "")).upper()
    if "ACCESS" in exchange or "SEC_PRIVATE" in exchange:
        venue = 30.0
    elif "GROWTH" in exchange:
        venue = 55.0
    elif any(token in exchange for token in ("NASDAQ", "NYSE", "EURONEXT", "LSE", "LONDON", "OSLO")):
        venue = 85.0
    else:
        venue = 50.0
    liquidity = _offer_liquidity_score(row.get("offer_value")) or 45.0
    width = _range_width_pct(row)
    range_score = 45.0 if width is None else 90.0 if width <= 10 else 75.0 if width <= 20 else 55.0 if width <= 30 else 35.0
    source_count = int(_as_float(row.get("source_count")) or 1)
    confirmation = 45.0 if source_count == 1 else 75.0 if source_count == 2 else 90.0
    prospectus = 85.0 if row.get("sec_analysis_status") == "PROSPECTUS_PARSED" else 45.0
    return round(0.20 * status + 0.15 * venue + 0.20 * liquidity + 0.15 * range_score + 0.15 * confirmation + 0.15 * prospectus, 2)


def score_dimension(values: dict[str, object], weights: dict[str, float]) -> tuple[float | None, float]:
    numerator = 0.0
    active_weight = 0.0
    total_weight = sum(float(weight) for weight in weights.values())
    for criterion, weight in weights.items():
        value = _as_float(values.get(criterion))
        if value is None:
            continue
        numerator += min(100.0, max(0.0, value)) * float(weight)
        active_weight += float(weight)
    coverage = 0.0 if total_weight <= 0 else active_weight / total_weight * 100.0
    if active_weight <= 0:
        return None, round(coverage, 2)
    return round(numerator / active_weight, 2), round(coverage, 2)


def pea_eligibility_status(issuer_country: object) -> str:
    country = "" if issuer_country is None else str(issuer_country).strip().upper()
    if not country:
        return "UNVERIFIED"
    if country in EEA_COUNTRIES:
        return "POTENTIAL_VERIFY_TAX_AND_SECURITY"
    return "LIKELY_INELIGIBLE"


def _merge_enrichment(candidates: list[dict], path: Path) -> list[dict]:
    if not path.exists():
        return candidates
    enrich = pd.read_csv(path, dtype=str, low_memory=False).fillna("")
    if enrich.empty or "candidate_id" not in enrich.columns:
        return candidates
    by_id = {str(row.get("candidate_id", "")): row.to_dict() for _, row in enrich.iterrows() if row.get("candidate_id")}
    for candidate in candidates:
        extra = by_id.get(candidate["candidate_id"])
        if extra:
            for key, value in extra.items():
                if key != "candidate_id" and value != "":
                    candidate[key] = value
    return candidates


def _append_hard_flags(current: object, incoming: object) -> str:
    flags = {flag.strip() for source in (current, incoming) for flag in str(source or "").split("|") if flag.strip()}
    return "|".join(sorted(flags))


def enrich_with_sec(candidates: list[dict], registrations: list[dict], config: dict) -> tuple[list[dict], list[dict]]:
    user_agent = str(os.environ.get("SEC_USER_AGENT") or config.get("sec_user_agent_fallback") or "PEA-Analyzer/1.1 marcivan75-byte@users.noreply.github.com")
    max_enrich = int(config.get("sec_max_enrichments_per_run", 15))
    matched: list[tuple[tuple[int, int, str], int, dict]] = []
    for idx, candidate in enumerate(candidates):
        registration = sec_ipo.match_registration(candidate, registrations)
        if not registration:
            continue
        expected = _parse_date(candidate.get("expected_date"))
        has_calendar = 0 if expected else 1
        days = (expected - datetime.now(timezone.utc).date()).days if expected else 9999
        matched.append(((has_calendar, max(days, 0), str(registration.get("filed", ""))), idx, registration))
    matched.sort(key=lambda item: item[0])
    statuses: list[dict] = []
    for _, idx, registration in matched[:max_enrich]:
        enriched, status = sec_ipo.enrich_candidate(candidates[idx], registration, user_agent)
        enriched["hard_flags"] = _append_hard_flags(candidates[idx].get("hard_flags"), enriched.get("hard_flags"))
        enriched["identity_key"] = _identity_key(enriched)
        enriched["candidate_id"] = _candidate_id(enriched)
        candidates[idx] = enriched
        statuses.append(status)
    if len(matched) > max_enrich:
        statuses.append({"candidate_id": "__QUEUE__", "status": "DEFERRED_RATE_BUDGET", "count": len(matched) - max_enrich})
    return candidates, statuses


def classify_candidate(row: dict, config: dict) -> str:
    status = str(row.get("status", "")).lower()
    if status == "withdrawn":
        return "AVOID_WITHDRAWN"
    flags = {flag.strip() for flag in str(row.get("hard_flags", "")).split("|") if flag.strip()}
    if flags.intersection(config["hard_block_flags"]):
        return "AVOID_HARD_BLOCK"
    opportunity = _as_float(row.get("opportunity_score"))
    risk = _as_float(row.get("risk_score"))
    net = _as_float(row.get("net_ipo_score"))
    readiness = _as_float(row.get("market_readiness_score")) or 0.0
    opp_cov = _as_float(row.get("opportunity_coverage_pct")) or 0.0
    risk_cov = _as_float(row.get("risk_coverage_pct")) or 0.0
    min_cov = min(opp_cov, risk_cov)
    if opportunity is None or risk is None or net is None or min_cov < float(config["minimum_scored_weight_pct"]):
        if row.get("sec_cik") and not row.get("expected_date"):
            return "WATCH_EARLY_FILING"
        return "WATCH_DATA_GAP"
    thresholds = config["decision_thresholds"]
    priority = thresholds["priority_dd"]
    if readiness >= 60 and net >= priority["net_min"] and opportunity >= priority["opportunity_min"] and risk <= priority["risk_max"] and min_cov >= priority["coverage_min"]:
        return "PRIORITY_DD"
    deep = thresholds["deep_dd"]
    if readiness >= 50 and net >= deep["net_min"] and opportunity >= deep["opportunity_min"] and risk <= deep["risk_max"] and min_cov >= deep["coverage_min"]:
        return "DEEP_DD"
    if risk >= thresholds["avoid_risk_min"]:
        return "AVOID_HIGH_RISK"
    watch = thresholds["watch"]
    if net >= watch["net_min"] and risk <= watch["risk_max"]:
        return "WATCH_EARLY_FILING" if readiness < 50 and row.get("sec_cik") else "WATCH"
    return "AVOID_OR_LOW_EDGE"


def evaluate_candidates(candidates: list[dict], config: dict, history: pd.DataFrame) -> list[dict]:
    previous = _previous_map(history)
    output: list[dict] = []
    for candidate in candidates:
        candidate["identity_key"] = _identity_key(candidate)
        signals = _derive_history_signals(candidate, previous.get(candidate["identity_key"]))
        candidate.update(signals)
        opportunity_values = {criterion: candidate.get(f"opportunity_{criterion}") for criterion in config["opportunity_weights"]}
        risk_values = {criterion: candidate.get(f"risk_{criterion}") for criterion in config["risk_weights"]}
        for criterion in ("bookbuilding_demand", "float_liquidity"):
            if opportunity_values.get(criterion) in (None, ""):
                opportunity_values[criterion] = candidate.get(criterion)
        for criterion in ("small_float_liquidity", "deal_instability"):
            if risk_values.get(criterion) in (None, ""):
                risk_values[criterion] = candidate.get(criterion)
        opportunity_score, opportunity_coverage = score_dimension(opportunity_values, config["opportunity_weights"])
        risk_score, risk_coverage = score_dimension(risk_values, config["risk_weights"])
        candidate["opportunity_score"] = opportunity_score
        candidate["risk_score"] = risk_score
        candidate["opportunity_coverage_pct"] = opportunity_coverage
        candidate["risk_coverage_pct"] = risk_coverage
        candidate["market_readiness_score"] = market_readiness_score(candidate)
        if opportunity_score is not None and risk_score is not None:
            weights = config["net_score_weights"]
            candidate["net_ipo_score"] = round(float(weights["opportunity"]) * opportunity_score + float(weights["risk_inverse"]) * (100.0 - risk_score), 2)
        else:
            candidate["net_ipo_score"] = None
        candidate["pea_eligibility"] = pea_eligibility_status(candidate.get("issuer_country"))
        candidate["decision"] = classify_candidate(candidate, config)
        candidate["live_order_allowed"] = False
        output.append(candidate)
    return output


def build_alerts(evaluated: list[dict], history: pd.DataFrame) -> list[dict]:
    previous = _previous_map(history)
    alerts: list[dict] = []
    for row in evaluated:
        key = row["identity_key"]
        prior = previous.get(key)
        base = {"identity_key": key, "candidate_id": row.get("candidate_id"), "name": row.get("name"), "decision": row.get("decision")}
        if prior is None:
            alerts.append({**base, "severity": "MEDIUM", "alert": "NEW_CANDIDATE", "detail": "New IPO candidate detected"})
            continue
        prior_status = str(prior.get("status", "")).lower()
        now_status = str(row.get("status", "")).lower()
        if now_status == "withdrawn" and prior_status != "withdrawn":
            alerts.append({**base, "severity": "CRITICAL", "alert": "WITHDRAWN", "detail": "IPO status changed to withdrawn"})
        prev_mid = _as_float(prior.get("price_mid"))
        now_mid = _as_float(row.get("price_mid"))
        if prev_mid and now_mid:
            delta = (now_mid / prev_mid - 1.0) * 100.0
            if abs(delta) >= 5:
                severity = "HIGH" if delta <= -10 else "MEDIUM"
                alerts.append({**base, "severity": severity, "alert": "PRICE_RANGE_REVISION", "detail": f"Midpoint revision {delta:+.1f}%"})
        prev_date = _parse_date(prior.get("expected_date"))
        now_date = _parse_date(row.get("expected_date"))
        if prev_date and now_date and (now_date - prev_date).days >= 5:
            alerts.append({**base, "severity": "HIGH", "alert": "IPO_DELAY", "detail": f"Expected date delayed {(now_date - prev_date).days} days"})
        prev_risk = _as_float(prior.get("risk_score"))
        now_risk = _as_float(row.get("risk_score"))
        if prev_risk is not None and now_risk is not None and now_risk - prev_risk >= 10:
            alerts.append({**base, "severity": "HIGH", "alert": "RISK_DETERIORATION", "detail": f"Risk score +{now_risk - prev_risk:.1f} points"})
        prev_decision = str(prior.get("decision", ""))
        now_decision = str(row.get("decision", ""))
        if prev_decision and now_decision and prev_decision != now_decision:
            direction = "UPGRADE" if DECISION_RANK.get(now_decision, 99) < DECISION_RANK.get(prev_decision, 99) else "DOWNGRADE"
            alerts.append({**base, "severity": "MEDIUM" if direction == "UPGRADE" else "HIGH", "alert": f"DECISION_{direction}", "detail": f"{prev_decision} -> {now_decision}"})
        prev_accession = str(prior.get("sec_accession", ""))
        now_accession = str(row.get("sec_accession", ""))
        if prev_accession and now_accession and prev_accession != now_accession:
            alerts.append({**base, "severity": "MEDIUM", "alert": "PROSPECTUS_UPDATE", "detail": f"New SEC prospectus {row.get('sec_form', '')}"})
        prev_flags = {x for x in str(prior.get("hard_flags", "")).split("|") if x}
        now_flags = {x for x in str(row.get("hard_flags", "")).split("|") if x}
        new_flags = sorted(now_flags - prev_flags)
        if new_flags:
            alerts.append({**base, "severity": "CRITICAL", "alert": "NEW_HARD_FLAG", "detail": "|".join(new_flags)})
    return alerts


def _history_rows(rows: list[dict], observed_at: str) -> pd.DataFrame:
    columns = [
        "observed_at_utc", "identity_key", "candidate_id", "name", "symbol", "exchange", "expected_date", "status", "price_range",
        "price_low", "price_high", "price_mid", "number_of_shares", "offer_value", "sources", "source_count", "market_readiness_score",
        "opportunity_score", "risk_score", "net_ipo_score", "opportunity_coverage_pct", "risk_coverage_pct", "decision", "hard_flags",
        "sec_cik", "sec_form", "sec_filing_date", "sec_accession", "sec_analysis_status", "pea_eligibility",
    ]
    records = []
    for row in rows:
        record = {column: row.get(column) for column in columns}
        record["observed_at_utc"] = observed_at
        records.append(record)
    return pd.DataFrame(records, columns=columns)


def _committee_brief(ranking: pd.DataFrame, alerts: list[dict], generated_at: str) -> dict:
    attention = [alert for alert in alerts if alert.get("severity") in {"CRITICAL", "HIGH"}]
    candidates = []
    if not ranking.empty:
        selected = ranking[ranking["decision"].isin(["PRIORITY_DD", "DEEP_DD", "WATCH", "WATCH_EARLY_FILING"])].head(12)
        for _, row in selected.iterrows():
            candidates.append(
                {
                    "name": row.get("name"), "symbol": row.get("symbol"), "expected_date": row.get("expected_date"),
                    "decision": row.get("decision"), "opportunity_score": row.get("opportunity_score"), "risk_score": row.get("risk_score"),
                    "net_ipo_score": row.get("net_ipo_score"), "market_readiness_score": row.get("market_readiness_score"),
                    "coverage_min_pct": min(_as_float(row.get("opportunity_coverage_pct")) or 0, _as_float(row.get("risk_coverage_pct")) or 0),
                    "pea_eligibility": row.get("pea_eligibility"), "sources": row.get("sources"), "hard_flags": row.get("hard_flags"),
                    "sec_form": row.get("sec_form"), "sec_prospectus_url": row.get("sec_prospectus_url"),
                }
            )
    return {
        "generated_at_utc": generated_at,
        "execution_policy": "DUE_DILIGENCE_ONLY_NO_BUY",
        "high_priority_alerts": attention[:20],
        "shortlist": candidates,
        "committee_instruction": "Review PRIORITY_DD/DEEP_DD and HIGH/CRITICAL alerts. No IPO score is authorized to create an order before dedicated PIT/OOS promotion.",
    }


def run(root: Path = ROOT) -> dict:
    config = _load_config(root / "config" / "IPO_RADAR_V1.json")
    outdir = root / "outputs" / "ipo_radar"
    state_dir = root / "state" / "ipo_radar"
    enrichment_path = root / "inputs" / "IPO_ENRICHMENT.csv"
    outdir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    history_path = state_dir / "IPO_HISTORY.csv"
    start = datetime.now(timezone.utc).date()
    end = start + timedelta(days=int(config["lookahead_days"]))
    history = _read_history(history_path)
    all_candidates: list[dict] = []
    source_status: list[dict] = []

    for collector in (
        lambda: collect_finnhub(start, end, os.environ.get("FINNHUB_API_KEY")),
        lambda: collect_nasdaq(start, end),
        lambda: collect_euronext(start, end),
    ):
        rows, status = collector()
        all_candidates.extend(rows)
        source_status.append(status)

    sec_lookback = int(config.get("sec_discovery_lookback_days", 45))
    sec_user_agent = str(os.environ.get("SEC_USER_AGENT") or config.get("sec_user_agent_fallback") or "PEA-Analyzer/1.1 marcivan75-byte@users.noreply.github.com")
    registrations, sec_status = sec_ipo.collect_recent_registrations(start - timedelta(days=sec_lookback), start, sec_user_agent)
    source_status.append(sec_status)
    all_candidates.extend(sec_ipo.registration_candidates(registrations))

    candidates = deduplicate_candidates(all_candidates, config["source_priority"])
    candidates = _merge_enrichment(candidates, enrichment_path)
    candidates, sec_enrichment_status = enrich_with_sec(candidates, registrations, config)
    evaluated = evaluate_candidates(candidates, config, history)
    alerts = build_alerts(evaluated, history)

    ranking = pd.DataFrame(evaluated)
    if not ranking.empty:
        ranking["_decision_rank"] = ranking["decision"].map(DECISION_RANK).fillna(99)
        ranking = ranking.sort_values(["_decision_rank", "net_ipo_score", "market_readiness_score"], ascending=[True, False, False], na_position="last").drop(columns=["_decision_rank"])
    ranking.to_csv(outdir / "IPO_RANKING.csv", index=False)
    pd.DataFrame(source_status).to_csv(outdir / "IPO_SOURCE_STATUS.csv", index=False)
    pd.DataFrame(sec_enrichment_status).to_csv(outdir / "IPO_SEC_DD_STATUS.csv", index=False)
    pd.DataFrame(alerts).to_csv(outdir / "IPO_ALERTS.csv", index=False)

    observed_at = datetime.now(timezone.utc).isoformat()
    new_history = _history_rows(evaluated, observed_at)
    combined_history = pd.concat([history, new_history], ignore_index=True) if not history.empty else new_history
    if not combined_history.empty:
        combined_history.to_csv(history_path, index=False)

    brief = _committee_brief(ranking, alerts, observed_at)
    (outdir / "IPO_COMMITTEE_BRIEF.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    decision_counts = ranking["decision"].value_counts().to_dict() if not ranking.empty and "decision" in ranking.columns else {}
    degraded_sources = [row["source"] for row in source_status if row["status"] not in {"SUCCESS", "SKIPPED_MISSING_KEY"}]
    successful_sources = [row["source"] for row in source_status if row["status"] in {"SUCCESS", "PARTIAL"}]
    summary = {
        "module_version": config["version"],
        "mode": config["mode"],
        "generated_at_utc": observed_at,
        "operational_status": "SUCCESS" if successful_sources and not degraded_sources else "DEGRADED" if successful_sources else "FAILED_DISCOVERY",
        "window": {"from": start.isoformat(), "to": end.isoformat(), "sec_filing_lookback_days": sec_lookback},
        "candidate_count": int(len(ranking)),
        "decision_counts": decision_counts,
        "alert_count": len(alerts),
        "high_critical_alert_count": sum(1 for alert in alerts if alert.get("severity") in {"HIGH", "CRITICAL"}),
        "sec_prospectus_enriched_count": sum(1 for row in sec_enrichment_status if row.get("status") == "SUCCESS"),
        "source_status": source_status,
        "degraded_sources": degraded_sources,
        "live_orders_enabled": False,
        "can_create_buy": False,
        "governance": config["governance"],
        "outputs": {
            "ranking": "outputs/ipo_radar/IPO_RANKING.csv",
            "source_status": "outputs/ipo_radar/IPO_SOURCE_STATUS.csv",
            "sec_dd_status": "outputs/ipo_radar/IPO_SEC_DD_STATUS.csv",
            "alerts": "outputs/ipo_radar/IPO_ALERTS.csv",
            "committee_brief": "outputs/ipo_radar/IPO_COMMITTEE_BRIEF.json",
            "history": "state/ipo_radar/IPO_HISTORY.csv",
        },
    }
    (outdir / "IPO_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
