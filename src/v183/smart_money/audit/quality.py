from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SmartMoneyQualityResult:
    passed: bool
    checks: list[dict]


def _business_days_elapsed(start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Business days strictly after publication through as-of date.

    A Thursday publication observed on Sunday is one business day old (Friday),
    not three calendar days old. This prevents weekend false failures while
    still failing stale weekday data.
    """
    s = pd.Timestamp(start).normalize()
    e = pd.Timestamp(end).normalize()
    if e <= s:
        return 0
    return int(np.busday_count((s + pd.Timedelta(days=1)).date(), (e + pd.Timedelta(days=1)).date()))


def run(
    events: pd.DataFrame,
    scores: pd.DataFrame,
    as_of: str,
    cfg: dict,
    coverage: dict | None = None,
    calibration: dict | None = None,
) -> SmartMoneyQualityResult:
    q = cfg["smart_money_quality_gates"]
    checks = []

    def add(name, ok, value, threshold, detail=""):
        checks.append({"check": name, "passed": bool(ok), "value": value, "threshold": threshold, "detail": detail})

    if events.empty:
        add("events_non_empty", False, 0, ">0")
    else:
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
            censored = short_rows.get("public_censored_below_05", pd.Series(False, index=short_rows.index)).fillna(False).astype(bool)
            pct = pd.to_numeric(short_rows["short_position_pct"], errors="coerce")
            # Censorship means a positive position known only to be below the
            # public threshold. A source-published exact zero is not censored.
            invalid_zero = int((censored & pct.eq(0)).sum())
            add("censored_short_not_forced_zero", invalid_zero == 0, invalid_zero, 0)
            short_pub = pd.to_datetime(short_rows["publication_date"], errors="coerce").dropna()
            if not short_pub.empty:
                last_pub = short_pub.max()
                calendar_age = max(0, int((pd.Timestamp(as_of) - last_pub).days))
                business_age = _business_days_elapsed(last_pub, pd.Timestamp(as_of))
                max_age = int(q["max_short_data_age_days"])
                add(
                    "amf_short_freshness_business_days",
                    business_age <= max_age,
                    business_age,
                    max_age,
                    detail=f"calendar_days={calendar_age}; weekend-safe business-day freshness",
                )

    if not scores.empty:
        if "smart_money_confidence" in scores.columns:
            conf = pd.to_numeric(scores["smart_money_confidence"], errors="coerce").dropna()
            ok = not conf.empty and bool(((conf >= 0) & (conf <= 1)).all())
            bounds = None if conf.empty else [float(conf.min()), float(conf.max())]
            add("score_confidence_bounds", ok, bounds, "[0,1]")

        if {"score_base", "score_final"}.issubset(scores.columns):
            base = pd.to_numeric(scores["score_base"], errors="coerce")
            final = pd.to_numeric(scores["score_final"], errors="coerce")
            comparable = base.notna() & final.notna()
            match_pct = 100.0 if comparable.sum() == 0 else 100.0 * (base[comparable].sub(final[comparable]).abs() < 1e-9).mean()
            add(
                "shadow_score_final_matches_base_pct",
                match_pct >= float(q["shadow_score_final_match_min_pct"]),
                round(float(match_pct), 2),
                q["shadow_score_final_match_min_pct"],
            )

        if {"score_base", "score_shadow", "universe"}.issubset(scores.columns):
            base = pd.to_numeric(scores["score_base"], errors="coerce")
            shadow = pd.to_numeric(scores["score_shadow"], errors="coerce")
            delta = (shadow - base).abs()
            for universe, threshold_key in (("ACTION", "max_abs_action_shadow_delta"), ("ETF", "max_abs_etf_shadow_delta")):
                subset = delta[scores["universe"].astype(str).eq(universe) & delta.notna()]
                maximum = 0.0 if subset.empty else float(subset.max())
                limit = float(q[threshold_key])
                add(f"{universe.lower()}_shadow_delta_cap", maximum <= limit + 1e-9, round(maximum, 4), limit)

        if "smart_money_active_scoring_allowed" in scores.columns:
            active = scores["smart_money_active_scoring_allowed"].fillna(False).astype(bool)
            add("active_scoring_disabled_rc1", not bool(active.any()), int(active.sum()), 0)

    if coverage is not None:
        registry = float(coverage.get("registry_coverage_pct", 0.0))
        add("etf_provider_registry_coverage_pct", registry >= float(q["etf_provider_registry_min_pct"]), registry, q["etf_provider_registry_min_pct"])
        add("etf_flow_ready_coverage_reported", "flow_ready_20d_pct" in coverage, coverage.get("flow_ready_20d_pct"), "reported")

    if calibration is not None:
        required = q.get("calibration_status_required")
        add("calibration_contract_passed", bool(calibration.get("passed")), calibration.get("passed"), True)
        add("calibration_status", calibration.get("status") == required, calibration.get("status"), required)
        add(
            "empirical_fit_still_required_before_activation",
            calibration.get("empirical_walk_forward_required_for_active_scoring") is True,
            calibration.get("empirical_walk_forward_required_for_active_scoring"),
            True,
        )

    return SmartMoneyQualityResult(all(c["passed"] for c in checks), checks)
