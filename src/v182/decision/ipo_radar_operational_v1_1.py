from __future__ import annotations

from datetime import date, datetime, timezone
import importlib
import json
from pathlib import Path
import re

import pandas as pd

legacy = importlib.import_module("v182.decision.ipo_radar_v1")
ROOT = legacy.ROOT
DECISION_RANK = legacy.DECISION_RANK


def parse_date_strict(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "n/a", "na", "-"}:
        return None
    formats = ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d.%m.%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"]
    slash = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if slash:
        first, second = int(slash.group(1)), int(slash.group(2))
        if first > 12:
            formats.insert(0, "%d/%m/%Y")
        elif second > 12:
            formats.insert(0, "%m/%d/%Y")
        else:
            formats.insert(0, "%m/%d/%Y")
            formats.insert(1, "%d/%m/%Y")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def classify_candidate(row: dict, config: dict) -> str:
    status = str(row.get("status", "")).lower()
    if status == "withdrawn":
        return "AVOID_WITHDRAWN"
    flags = {flag.strip() for flag in str(row.get("hard_flags", "")).split("|") if flag.strip()}
    if flags.intersection(config["hard_block_flags"]):
        return "AVOID_HARD_BLOCK"

    opportunity = legacy._as_float(row.get("opportunity_score"))
    risk = legacy._as_float(row.get("risk_score"))
    net = legacy._as_float(row.get("net_ipo_score"))
    readiness = legacy._as_float(row.get("market_readiness_score")) or 0.0
    opp_cov = legacy._as_float(row.get("opportunity_coverage_pct")) or 0.0
    risk_cov = legacy._as_float(row.get("risk_coverage_pct")) or 0.0
    min_cov = min(opp_cov, risk_cov)

    if opportunity is None or risk is None or net is None or min_cov < float(config["minimum_scored_weight_pct"]):
        return "WATCH_EARLY_FILING" if row.get("sec_cik") and not row.get("expected_date") else "WATCH_DATA_GAP"

    thresholds = config["decision_thresholds"]
    priority = thresholds["priority_dd"]
    priority_readiness = float(priority.get("market_readiness_min", 60.0))
    if (
        readiness >= priority_readiness
        and net >= float(priority["net_min"])
        and opportunity >= float(priority["opportunity_min"])
        and risk <= float(priority["risk_max"])
        and min_cov >= float(priority["coverage_min"])
    ):
        return "PRIORITY_DD"

    deep = thresholds["deep_dd"]
    deep_readiness = float(deep.get("market_readiness_min", 50.0))
    if (
        readiness >= deep_readiness
        and net >= float(deep["net_min"])
        and opportunity >= float(deep["opportunity_min"])
        and risk <= float(deep["risk_max"])
        and min_cov >= float(deep["coverage_min"])
    ):
        return "DEEP_DD"

    if risk >= float(thresholds["avoid_risk_min"]):
        return "AVOID_HIGH_RISK"
    watch = thresholds["watch"]
    if net >= float(watch["net_min"]) and risk <= float(watch["risk_max"]):
        return "WATCH_EARLY_FILING" if readiness < deep_readiness and row.get("sec_cik") else "WATCH"
    return "AVOID_OR_LOW_EDGE"


def _alert_base(row: dict) -> dict:
    return {
        "identity_key": row.get("identity_key"),
        "candidate_id": row.get("candidate_id"),
        "name": row.get("name"),
        "decision": row.get("decision"),
    }


def build_alerts(evaluated: list[dict], history: pd.DataFrame) -> list[dict]:
    previous = legacy._previous_map(history)
    alerts: list[dict] = []
    for row in evaluated:
        key = str(row.get("identity_key") or "")
        prior = previous.get(key)
        base = _alert_base(row)
        now_flags = {flag for flag in str(row.get("hard_flags", "")).split("|") if flag}

        if prior is None:
            alerts.append({**base, "severity": "MEDIUM", "alert": "NEW_CANDIDATE", "detail": "New IPO candidate detected"})
            if str(row.get("status", "")).lower() == "withdrawn":
                alerts.append({**base, "severity": "CRITICAL", "alert": "WITHDRAWN", "detail": "Newly detected candidate is already withdrawn"})
            if now_flags:
                alerts.append({**base, "severity": "CRITICAL", "alert": "NEW_HARD_FLAG", "detail": "|".join(sorted(now_flags))})
            continue

        prior_status = str(prior.get("status", "")).lower()
        now_status = str(row.get("status", "")).lower()
        if now_status == "withdrawn" and prior_status != "withdrawn":
            alerts.append({**base, "severity": "CRITICAL", "alert": "WITHDRAWN", "detail": "IPO status changed to withdrawn"})

        prev_mid = legacy._as_float(prior.get("price_mid"))
        now_mid = legacy._as_float(row.get("price_mid"))
        if prev_mid and now_mid:
            delta = (now_mid / prev_mid - 1.0) * 100.0
            if abs(delta) >= 5.0:
                alerts.append({
                    **base,
                    "severity": "HIGH" if delta <= -10.0 else "MEDIUM",
                    "alert": "PRICE_RANGE_REVISION",
                    "detail": f"Midpoint revision {delta:+.1f}%",
                })

        prev_date = parse_date_strict(prior.get("expected_date"))
        now_date = parse_date_strict(row.get("expected_date"))
        if prev_date and now_date and (now_date - prev_date).days >= 5:
            alerts.append({
                **base,
                "severity": "HIGH",
                "alert": "IPO_DELAY",
                "detail": f"Expected date delayed {(now_date - prev_date).days} days",
            })

        prev_risk = legacy._as_float(prior.get("risk_score"))
        now_risk = legacy._as_float(row.get("risk_score"))
        if prev_risk is not None and now_risk is not None and now_risk - prev_risk >= 10.0:
            alerts.append({
                **base,
                "severity": "HIGH",
                "alert": "RISK_DETERIORATION",
                "detail": f"Risk score +{now_risk - prev_risk:.1f} points",
            })

        prev_decision = str(prior.get("decision", ""))
        now_decision = str(row.get("decision", ""))
        if prev_decision and now_decision and prev_decision != now_decision:
            direction = "UPGRADE" if DECISION_RANK.get(now_decision, 99) < DECISION_RANK.get(prev_decision, 99) else "DOWNGRADE"
            alerts.append({
                **base,
                "severity": "MEDIUM" if direction == "UPGRADE" else "HIGH",
                "alert": f"DECISION_{direction}",
                "detail": f"{prev_decision} -> {now_decision}",
            })

        prev_accession = str(prior.get("sec_accession", ""))
        now_accession = str(row.get("sec_accession", ""))
        if prev_accession and now_accession and prev_accession != now_accession:
            alerts.append({**base, "severity": "MEDIUM", "alert": "PROSPECTUS_UPDATE", "detail": f"New SEC prospectus {row.get('sec_form', '')}"})

        prev_flags = {flag for flag in str(prior.get("hard_flags", "")).split("|") if flag}
        new_flags = sorted(now_flags - prev_flags)
        if new_flags:
            alerts.append({**base, "severity": "CRITICAL", "alert": "NEW_HARD_FLAG", "detail": "|".join(new_flags)})
    return alerts


def history_rows_full(rows: list[dict], observed_at: str) -> pd.DataFrame:
    preferred = [
        "observed_at_utc", "identity_key", "candidate_id", "name", "symbol", "isin", "exchange", "euronext_location",
        "issuer_country", "expected_date", "status", "price_range", "price_low", "price_high", "price_mid", "number_of_shares",
        "offer_value", "sources", "source_count", "market_readiness_score", "opportunity_score", "risk_score", "net_ipo_score",
        "opportunity_coverage_pct", "risk_coverage_pct", "decision", "hard_flags", "pea_eligibility", "sec_cik", "sec_sic",
        "sec_sic_description", "sec_state_of_incorporation", "sec_form", "sec_filing_date", "sec_accession", "sec_prospectus_url",
        "sec_analysis_status", "sec_companyfacts_status", "sec_lockup_days", "sec_underwriters_detected", "price_revision_pct",
        "date_shift_days", "live_order_allowed",
    ]
    records: list[dict] = []
    all_fields: set[str] = set()
    for row in rows:
        record = dict(row)
        record["observed_at_utc"] = observed_at
        records.append(record)
        all_fields.update(record)
    if not records:
        return pd.DataFrame(columns=preferred)
    ordered = [field for field in preferred if field in all_fields]
    ordered.extend(sorted(all_fields - set(ordered)))
    return pd.DataFrame(records).reindex(columns=ordered)


def install_runtime_hardening() -> None:
    legacy._parse_date = parse_date_strict
    legacy.classify_candidate = classify_candidate
    legacy.build_alerts = build_alerts
    legacy._history_rows = history_rows_full


def run(root: Path = ROOT) -> dict:
    install_runtime_hardening()
    summary = legacy.run(root)
    summary["runtime_layer"] = "IPO_RADAR_OPERATIONAL_V1.1"
    summary["history_policy"] = "FULL_PIT_EVIDENCE_SNAPSHOT"
    summary_path = root / "outputs" / "ipo_radar" / "IPO_SUMMARY.json"
    if summary_path.exists():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
