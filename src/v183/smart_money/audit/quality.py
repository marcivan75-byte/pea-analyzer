from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import pandas as pd

@dataclass(frozen=True)
class SmartMoneyQualityResult:
    passed: bool
    checks: list[dict]


def run(events: pd.DataFrame, scores: pd.DataFrame, as_of: str, cfg: dict) -> SmartMoneyQualityResult:
    q = cfg["smart_money_quality_gates"]
    checks = []
    def add(name, ok, value, threshold, detail=""):
        checks.append({"check": name, "passed": bool(ok), "value": value, "threshold": threshold, "detail": detail})

    if events.empty:
        add("events_non_empty", False, 0, ">0")
        return SmartMoneyQualityResult(False, checks)

    valid_isin = events["isin"].astype(str).str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$").mean() * 100
    add("event_isin_valid_pct", valid_isin >= q["event_isin_valid_min_pct"], round(valid_isin, 2), q["event_isin_valid_min_pct"])

    dup = int(events["event_id"].duplicated().sum()) if "event_id" in events.columns else len(events)
    add("duplicate_event_ids", dup <= q["duplicate_event_max"], dup, q["duplicate_event_max"])

    scored = events[events["validation_status"].isin(["VALIDATED", "ISIN_MATCHED", "AUTO_MATCH"])]
    d_scored = int((scored["evidence_level"] == "D").sum())
    add("evidence_d_scored", d_scored == 0, d_scored, 0)

    source_a_pct = 100.0 * (events["evidence_level"] == "A").mean()
    add("source_a_event_pct", source_a_pct >= q["source_a_event_min_pct"], round(source_a_pct, 2), q["source_a_event_min_pct"])

    pub = pd.to_datetime(events["publication_date"], errors="coerce")
    future = int((pub > pd.Timestamp(as_of)).sum())
    add("no_lookahead_future_publications", future == 0, future, 0)

    short_rows = events[events["event_type"] == "SHORT"]
    if not short_rows.empty and "short_position_pct" in short_rows.columns:
        invalid_zero = int(((short_rows.get("public_censored_below_05", False) == True) &
                            (pd.to_numeric(short_rows["short_position_pct"], errors="coerce") == 0)).sum())
        add("censored_short_not_forced_zero", invalid_zero == 0, invalid_zero, 0)

    if not scores.empty and "smart_money_confidence" in scores.columns:
        conf = pd.to_numeric(scores["smart_money_confidence"], errors="coerce")
        add("score_confidence_bounds", bool(((conf >= 0) & (conf <= 1)).all()),
            [float(conf.min()), float(conf.max())], "[0,1]")

    return SmartMoneyQualityResult(all(c["passed"] for c in checks), checks)
