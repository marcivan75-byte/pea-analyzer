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

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "IPO_RADAR_V1.json"
OUTDIR = ROOT / "outputs" / "ipo_radar"
STATE_DIR = ROOT / "state" / "ipo_radar"
ENRICHMENT_PATH = ROOT / "inputs" / "IPO_ENRICHMENT.csv"

EEA_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT",
    "LV", "LI", "LT", "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE"
}


def _load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


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
    return f"{exchange}:{symbol or name}"


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
    }
    candidate["candidate_id"] = _candidate_id(candidate)
    return candidate


def _http_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer-IPO-Radar/1.0)",
        "Accept": "application/json,text/html,application/xhtml+xml",
    }


def collect_finnhub(start: date, end: date, api_key: str | None, timeout: int = 20) -> tuple[list[dict], dict]:
    source = "FINNHUB"
    if not api_key:
        return [], {"source": source, "status": "SKIPPED_MISSING_KEY", "count": 0}
    url = "https://finnhub.io/api/v1/calendar/ipo"
    try:
        response = requests.get(
            url,
            params={"from": start.isoformat(), "to": end.isoformat(), "token": api_key},
            headers=_http_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("ipoCalendar") or []
        candidates = [
            _standard_candidate(
                name=row.get("name"),
                symbol=row.get("symbol"),
                exchange=row.get("exchange"),
                expected_date=row.get("date"),
                status=row.get("status"),
                price_range=row.get("price"),
                number_of_shares=row.get("numberOfShares"),
                offer_value=row.get("totalSharesValue"),
                source=source,
            )
            for row in rows
            if row.get("name") or row.get("symbol")
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
    try:
        for month in _month_starts(start, end):
            response = requests.get(
                "https://api.nasdaq.com/api/ipo/calendar",
                params={"date": month.strftime("%Y-%m")},
                headers=_http_headers(),
                timeout=timeout,
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
        return candidates, {"source": source, "status": "SUCCESS", "count": len(candidates)}
    except Exception as exc:
        return [], {"source": source, "status": "FAILED", "count": 0, "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}


def collect_euronext(start: date, end: date, timeout: int = 20) -> tuple[list[dict], dict]:
    source = "EURONEXT"
    url = "https://live.euronext.com/en/ipo-showcase/all"
    try:
        response = requests.get(url, headers=_http_headers(), timeout=timeout)
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
                candidates.append(
                    _standard_candidate(
                        name=row.get(columns["company name"]),
                        symbol=row.get(columns.get("ticker", "")) if "ticker" in columns else None,
                        exchange=row.get(columns.get("market", "")) if "market" in columns else "EURONEXT",
                        expected_date=parsed,
                        status="expected",
                        source=source,
                    )
                )
        return candidates, {"source": source, "status": "SUCCESS", "count": len(candidates)}
    except Exception as exc:
        return [], {"source": source, "status": "FAILED", "count": 0, "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}


def deduplicate_candidates(rows: list[dict], source_priority: dict[str, int]) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in rows:
        key = row["candidate_id"]
        if key not in merged:
            merged[key] = dict(row)
            continue
        current = merged[key]
        sources = set(filter(None, str(current.get("sources", "")).split("|")))
        sources.update(filter(None, str(row.get("sources", "")).split("|")))
        current["sources"] = "|".join(sorted(sources, key=lambda x: source_priority.get(x, 0), reverse=True))
        current["source_count"] = len(sources)
        current_priority = max((source_priority.get(src, 0) for src in str(current.get("sources", "")).split("|")), default=0)
        row_priority = max((source_priority.get(src, 0) for src in str(row.get("sources", "")).split("|")), default=0)
        for field in (
            "name", "symbol", "exchange", "expected_date", "status", "price_range", "price_low", "price_high",
            "price_mid", "number_of_shares", "offer_value", "issuer_country"
        ):
            existing = current.get(field)
            incoming = row.get(field)
            existing_missing = existing is None or existing == "" or (isinstance(existing, float) and math.isnan(existing))
            if incoming is not None and incoming != "" and (existing_missing or row_priority > current_priority):
                current[field] = incoming
    return list(merged.values())


def _read_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _previous_by_candidate(history: pd.DataFrame) -> dict[str, dict]:
    if history.empty or "candidate_id" not in history.columns:
        return {}
    if "observed_at_utc" in history.columns:
        history = history.sort_values("observed_at_utc")
    latest = history.groupby("candidate_id", dropna=False).tail(1)
    return {str(row["candidate_id"]): row.to_dict() for _, row in latest.iterrows()}


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
    low = _as_float(row.get("price_low"))
    high = _as_float(row.get("price_high"))
    mid = _as_float(row.get("price_mid"))
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
            if revision_pct >= 10:
                bookbuilding = 95.0
            elif revision_pct >= 5:
                bookbuilding = 85.0
            elif revision_pct >= 0:
                bookbuilding = 65.0
            elif revision_pct >= -5:
                bookbuilding = 48.0
            elif revision_pct >= -10:
                bookbuilding = 32.0
            else:
                bookbuilding = 18.0
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
    status_map = {"priced": 95.0, "expected": 75.0, "filed": 50.0, "withdrawn": 0.0}
    status = status_map.get(str(row.get("status", "")).lower(), 50.0)
    exchange = str(row.get("exchange", "")).upper()
    if "ACCESS" in exchange:
        venue = 35.0
    elif "GROWTH" in exchange:
        venue = 55.0
    elif any(token in exchange for token in ("NASDAQ", "NYSE", "EURONEXT", "LSE", "LONDON", "OSLO")):
        venue = 85.0
    else:
        venue = 50.0
    liquidity = _offer_liquidity_score(row.get("offer_value")) or 50.0
    width = _range_width_pct(row)
    range_score = 50.0 if width is None else (90.0 if width <= 10 else 75.0 if width <= 20 else 55.0 if width <= 30 else 35.0)
    source_count = int(_as_float(row.get("source_count")) or 1)
    confirmation = 50.0 if source_count == 1 else 75.0 if source_count == 2 else 90.0
    return round(0.25 * status + 0.20 * venue + 0.25 * liquidity + 0.15 * range_score + 0.15 * confirmation, 2)


def score_dimension(values: dict[str, object], weights: dict[str, float]) -> tuple[float | None, float]:
    numerator = 0.0
    active_weight = 0.0
    total_weight = sum(float(weight) for weight in weights.values())
    for criterion, weight in weights.items():
        value = _as_float(values.get(criterion))
        if value is None:
            continue
        clipped = min(100.0, max(0.0, value))
        numerator += clipped * float(weight)
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
    if enrich.empty:
        return candidates
    rows = {str(row.get("candidate_id", "")): row.to_dict() for _, row in enrich.iterrows() if row.get("candidate_id")}
    for candidate in candidates:
        extra = rows.get(candidate["candidate_id"])
        if extra:
            for key, value in extra.items():
                if key != "candidate_id" and value != "":
                    candidate[key] = value
    return candidates


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
    opp_cov = _as_float(row.get("opportunity_coverage_pct")) or 0.0
    risk_cov = _as_float(row.get("risk_coverage_pct")) or 0.0
    min_cov = min(opp_cov, risk_cov)
    if opportunity is None or risk is None or net is None or min_cov < float(config["minimum_scored_weight_pct"]):
        return "WATCH_DATA_GAP"
    thresholds = config["decision_thresholds"]
    priority = thresholds["priority_dd"]
    if net >= priority["net_min"] and opportunity >= priority["opportunity_min"] and risk <= priority["risk_max"] and min_cov >= priority["coverage_min"]:
        return "PRIORITY_DD"
    deep = thresholds["deep_dd"]
    if net >= deep["net_min"] and opportunity >= deep["opportunity_min"] and risk <= deep["risk_max"] and min_cov >= deep["coverage_min"]:
        return "DEEP_DD"
    if risk >= thresholds["avoid_risk_min"]:
        return "AVOID_HIGH_RISK"
    watch = thresholds["watch"]
    if net >= watch["net_min"] and risk <= watch["risk_max"]:
        return "WATCH"
    return "AVOID_OR_LOW_EDGE"


def evaluate_candidates(candidates: list[dict], config: dict, history: pd.DataFrame) -> list[dict]:
    previous_map = _previous_by_candidate(history)
    output: list[dict] = []
    for candidate in candidates:
        signals = _derive_history_signals(candidate, previous_map.get(candidate["candidate_id"]))
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
            net_weights = config["net_score_weights"]
            candidate["net_ipo_score"] = round(
                float(net_weights["opportunity"]) * opportunity_score + float(net_weights["risk_inverse"]) * (100.0 - risk_score), 2
            )
        else:
            candidate["net_ipo_score"] = None
        candidate["pea_eligibility"] = pea_eligibility_status(candidate.get("issuer_country"))
        candidate["decision"] = classify_candidate(candidate, config)
        candidate["live_order_allowed"] = False
        output.append(candidate)
    return output


def _history_rows(rows: list[dict], observed_at: str) -> pd.DataFrame:
    columns = [
        "observed_at_utc", "candidate_id", "name", "symbol", "exchange", "expected_date", "status", "price_range", "price_low",
        "price_high", "price_mid", "number_of_shares", "offer_value", "sources", "source_count", "market_readiness_score",
        "opportunity_score", "risk_score", "net_ipo_score", "decision"
    ]
    records = []
    for row in rows:
        record = {column: row.get(column) for column in columns}
        record["observed_at_utc"] = observed_at
        records.append(record)
    return pd.DataFrame(records, columns=columns)


def run(root: Path = ROOT) -> dict:
    config_path = root / "config" / "IPO_RADAR_V1.json"
    config = _load_config(config_path)
    outdir = root / "outputs" / "ipo_radar"
    state_dir = root / "state" / "ipo_radar"
    enrichment_path = root / "inputs" / "IPO_ENRICHMENT.csv"
    outdir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    history_path = state_dir / "IPO_HISTORY.csv"

    start = datetime.now(timezone.utc).date()
    end = start + timedelta(days=int(config["lookahead_days"]))
    all_candidates: list[dict] = []
    source_status: list[dict] = []

    finnhub_rows, finnhub_status = collect_finnhub(start, end, os.environ.get("FINNHUB_API_KEY"))
    all_candidates.extend(finnhub_rows)
    source_status.append(finnhub_status)

    nasdaq_rows, nasdaq_status = collect_nasdaq(start, end)
    all_candidates.extend(nasdaq_rows)
    source_status.append(nasdaq_status)

    euronext_rows, euronext_status = collect_euronext(start, end)
    all_candidates.extend(euronext_rows)
    source_status.append(euronext_status)

    candidates = deduplicate_candidates(all_candidates, config["source_priority"])
    candidates = _merge_enrichment(candidates, enrichment_path)
    history = _read_history(history_path)
    evaluated = evaluate_candidates(candidates, config, history)

    ranking = pd.DataFrame(evaluated)
    if not ranking.empty:
        decision_rank = {"PRIORITY_DD": 0, "DEEP_DD": 1, "WATCH": 2, "WATCH_DATA_GAP": 3, "AVOID_OR_LOW_EDGE": 4, "AVOID_HIGH_RISK": 5, "AVOID_HARD_BLOCK": 6, "AVOID_WITHDRAWN": 7}
        ranking["_decision_rank"] = ranking["decision"].map(decision_rank).fillna(9)
        ranking = ranking.sort_values(["_decision_rank", "net_ipo_score", "market_readiness_score"], ascending=[True, False, False], na_position="last").drop(columns=["_decision_rank"])
    ranking.to_csv(outdir / "IPO_RANKING.csv", index=False)
    pd.DataFrame(source_status).to_csv(outdir / "IPO_SOURCE_STATUS.csv", index=False)

    observed_at = datetime.now(timezone.utc).isoformat()
    new_history = _history_rows(evaluated, observed_at)
    combined_history = pd.concat([history, new_history], ignore_index=True) if not history.empty else new_history
    if not combined_history.empty:
        combined_history.to_csv(history_path, index=False)

    decision_counts = ranking["decision"].value_counts().to_dict() if not ranking.empty and "decision" in ranking.columns else {}
    degraded_sources = [row["source"] for row in source_status if row["status"] not in {"SUCCESS", "SKIPPED_MISSING_KEY"}]
    summary = {
        "module_version": config["version"],
        "mode": config["mode"],
        "generated_at_utc": observed_at,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "candidate_count": int(len(ranking)),
        "decision_counts": decision_counts,
        "source_status": source_status,
        "degraded_sources": degraded_sources,
        "live_orders_enabled": False,
        "can_create_buy": False,
        "governance": config["governance"],
        "outputs": {
            "ranking": "outputs/ipo_radar/IPO_RANKING.csv",
            "source_status": "outputs/ipo_radar/IPO_SOURCE_STATUS.csv",
            "history": "state/ipo_radar/IPO_HISTORY.csv"
        }
    }
    (outdir / "IPO_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    summary = run(ROOT)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
