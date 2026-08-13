from __future__ import annotations

from pathlib import Path
import json
import math
import re

import numpy as np
import pandas as pd

from v182.decision.committee_master import classify_sector

VERSION = "TCT_SECTOR_CONTEXT_V1"
ROLE = "REPORTING_CONTEXT_ONLY"
SECTOR_FIELDS = (
    "sector_v21", "sector_yf", "sector", "sector_yahoo", "industry_yf", "industry",
)


def _missing(value) -> bool:
    if value is None:
        return True
    try:
        marker = pd.isna(value)
    except (TypeError, ValueError):
        marker = False
    if isinstance(marker, bool) and marker:
        return True
    return str(value).strip().lower() in {"", "nan", "none", "n/a", "na", "unknown", "non_observe"}


def _text(value, default: str = "") -> str:
    return default if _missing(value) else str(value).strip()


def _number(row: pd.Series | None, *fields: str) -> float | None:
    if row is None:
        return None
    for field in fields:
        if field not in row:
            continue
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def _first_text(row: pd.Series, *fields: str) -> tuple[str | None, str | None]:
    for field in fields:
        if field not in row:
            continue
        value = row.get(field)
        if not _missing(value):
            return field, str(value).strip()
    return None, None


def earnings_context(days_to_earnings) -> dict[str, object]:
    try:
        days = float(days_to_earnings)
    except (TypeError, ValueError):
        days = np.nan
    if not math.isfinite(days) or days < 0:
        return {
            "earnings_bucket": "EARNINGS_UNKNOWN",
            "event_risk_level": "UNKNOWN",
            "event_gap_risk_flag": False,
            "event_context": "EARNINGS_DATE_NOT_OBSERVED",
        }
    if days <= 1:
        return {
            "earnings_bucket": "EARNINGS_D0_1",
            "event_risk_level": "HIGH",
            "event_gap_risk_flag": True,
            "event_context": "EARNINGS_IMMINENT_GAP_RISK",
        }
    if days <= 5:
        return {
            "earnings_bucket": "EARNINGS_D2_5",
            "event_risk_level": "ELEVATED",
            "event_gap_risk_flag": False,
            "event_context": "EARNINGS_NEAR_2_5D",
        }
    if days <= 10:
        return {
            "earnings_bucket": "EARNINGS_D6_10",
            "event_risk_level": "MODERATE",
            "event_gap_risk_flag": False,
            "event_context": "EARNINGS_6_10D",
        }
    if days <= 20:
        return {
            "earnings_bucket": "EARNINGS_D11_20",
            "event_risk_level": "NORMAL",
            "event_gap_risk_flag": False,
            "event_context": "EARNINGS_11_20D",
        }
    return {
        "earnings_bucket": "EARNINGS_D21_PLUS",
        "event_risk_level": "LOW",
        "event_gap_risk_flag": False,
        "event_context": "EARNINGS_NOT_NEAR",
    }


def _timing_index(timing: pd.DataFrame) -> dict[str, pd.Series]:
    if timing is None or timing.empty or "isin" not in timing.columns:
        return {}
    work = timing.copy()
    work["_isin_key"] = work["isin"].astype(str).str.strip().str.upper()
    work = work[work["_isin_key"].ne("")].drop_duplicates("_isin_key", keep="last")
    return {str(row["_isin_key"]): row for _, row in work.iterrows()}


def build_context(baseline: pd.DataFrame, timing: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    if baseline is None or baseline.empty:
        raise ValueError("TCT sector context requires a non-empty TCT baseline")
    if "isin" not in baseline.columns:
        raise ValueError("TCT sector context baseline must contain isin")
    if baseline["isin"].astype(str).str.strip().duplicated().any():
        raise ValueError("TCT sector context baseline contains duplicate ISINs")

    timing_by_isin = _timing_index(timing)
    rows: list[dict] = []

    for _, base in baseline.iterrows():
        isin = _text(base.get("isin")).upper()
        timing_row = timing_by_isin.get(isin)
        timing_influence = _number(timing_row, "t1_t2_score_influence")
        if timing_influence is not None and abs(timing_influence) > 1e-12:
            raise RuntimeError(f"TCT_TIMING_SCORE_INFLUENCE_NONZERO:{isin}:{timing_influence}")
        if timing_row is not None:
            live_value = _text(timing_row.get("t1_t2_live_execution_allowed")).lower()
            if live_value in {"true", "1", "yes", "oui"}:
                raise RuntimeError(f"TCT_LIVE_EXECUTION_FORBIDDEN:{isin}")

        sector_field, sector_value = _first_text(base, *SECTOR_FIELDS)
        sector = classify_sector(base, "ACTION")
        if sector_field:
            sector_quality = "ATTRIBUTED_OR_PROVIDER_FIELD"
        elif sector != "NON CLASSE":
            sector_quality = "HEURISTIC_ONLY"
        else:
            sector_quality = "MISSING"
        sector_gap = sector_quality != "ATTRIBUTED_OR_PROVIDER_FIELD"

        baseline_score = _number(base, "tct_baseline_score")
        baseline_coverage = _number(base, "tct_baseline_coverage")
        baseline_coverage_pct = None
        if baseline_coverage is not None:
            baseline_coverage_pct = baseline_coverage * 100.0 if baseline_coverage <= 1.000001 else baseline_coverage
        baseline_rank = _number(base, "tct_baseline_rank")
        days = _number(base, "days_to_earnings")
        event = earnings_context(days)

        timing_score = _number(timing_row, "score")
        timing_cov = _number(timing_row, "coverage_pct")
        timing_decision = _text(timing_row.get("decision"), "NO_T1_T2") if timing_row is not None else "NO_T1_T2"
        timing_status = _text(timing_row.get("status"), "TCT_TIMING_NOT_AVAILABLE") if timing_row is not None else "TCT_TIMING_NOT_AVAILABLE"
        timing_setup = _text(timing_row.get("setup"), "NONE") if timing_row is not None else "NONE"

        price = _number(base, "last_close", "current_price_yf", "close")
        _, currency = _first_text(base, "trading_currency", "currency")

        rows.append({
            "isin": isin,
            "name": _text(base.get("name")),
            "yahoo_ticker": _text(base.get("yahoo_ticker")),
            "sector": sector,
            "sector_source_field": sector_field or "",
            "sector_source_value": sector_value or "",
            "sector_classification_quality": sector_quality,
            "sector_gap_flag": sector_gap,
            "last_price": price,
            "price_currency": currency or "",
            "tct_baseline_score": baseline_score,
            "baseline_note_10": round(baseline_score / 10.0, 2) if baseline_score is not None else np.nan,
            "tct_baseline_coverage_pct": round(baseline_coverage_pct, 2) if baseline_coverage_pct is not None else np.nan,
            "tct_baseline_rank": int(baseline_rank) if baseline_rank is not None else pd.NA,
            "tct_baseline_status": _text(base.get("tct_baseline_status"), "UNKNOWN"),
            "tct_baseline_top20": bool(baseline_rank is not None and baseline_rank <= 20),
            "earnings_component_score": _number(base, "tct_baseline_component_earnings"),
            "days_to_earnings": days,
            "eps_revision_3m": _number(base, "eps_revision_3m"),
            "beat_rate": _number(base, "beat_rate"),
            "short_interest_pct": _number(base, "short_interest", "short_percent_float_pct", "public_short_pct"),
            **event,
            "timing_setup": timing_setup,
            "timing_status": timing_status,
            "timing_decision": timing_decision,
            "timing_quality_score": timing_score,
            "timing_quality_coverage_pct": timing_cov,
            "t1_quality_score": _number(timing_row, "t1_quality_score"),
            "t2_quality_score": _number(timing_row, "t2_quality_score"),
            "t1_age_sessions": _number(timing_row, "t1_age_sessions"),
            "timing_rejection_reason": _text(timing_row.get("t1_t2_rejection_reason")) if timing_row is not None else "",
            "reporting_role": ROLE,
            "t1_t2_score_influence": 0.0,
            "live_orders_enabled": False,
        })

    details = pd.DataFrame(rows)
    details["baseline_ranked"] = details["tct_baseline_rank"].notna()
    details["timing_t1_flag"] = details["timing_decision"].astype(str).str.startswith("T1_")
    details["timing_t2_flag"] = details["timing_decision"].astype(str).str.startswith("T2_")
    details["timing_data_gap_flag"] = details["timing_status"].isin({
        "SHADOW_HISTORY_MISSING", "SHADOW_HISTORY_ERROR", "SHADOW_TICKER_MISSING", "TCT_TIMING_NOT_AVAILABLE",
    })
    details["earnings_known_flag"] = pd.to_numeric(details["days_to_earnings"], errors="coerce").notna()
    details["earnings_d0_1_flag"] = details["earnings_bucket"].eq("EARNINGS_D0_1")
    details["earnings_d2_5_flag"] = details["earnings_bucket"].eq("EARNINGS_D2_5")

    dashboard = (
        details.groupby("sector", dropna=False)
        .agg(
            action_count=("isin", "count"),
            baseline_ranked_count=("baseline_ranked", "sum"),
            baseline_top20_count=("tct_baseline_top20", "sum"),
            t1_shadow_count=("timing_t1_flag", "sum"),
            t2_shadow_count=("timing_t2_flag", "sum"),
            timing_data_gap_count=("timing_data_gap_flag", "sum"),
            earnings_date_known_count=("earnings_known_flag", "sum"),
            earnings_d0_1_count=("earnings_d0_1_flag", "sum"),
            earnings_d2_5_count=("earnings_d2_5_flag", "sum"),
            event_gap_risk_count=("event_gap_risk_flag", "sum"),
            sector_metadata_gap_count=("sector_gap_flag", "sum"),
            mean_baseline_score=("tct_baseline_score", "mean"),
            median_baseline_score=("tct_baseline_score", "median"),
            mean_baseline_coverage_pct=("tct_baseline_coverage_pct", "mean"),
        )
        .reset_index()
    )
    for col in ("mean_baseline_score", "median_baseline_score", "mean_baseline_coverage_pct"):
        dashboard[col] = pd.to_numeric(dashboard[col], errors="coerce").round(2)
    dashboard = dashboard.sort_values(
        ["baseline_top20_count", "mean_baseline_score", "action_count"],
        ascending=[False, False, False],
        na_position="last",
    ).reset_index(drop=True)

    gaps = details.loc[
        details["sector_gap_flag"],
        [
            "isin", "name", "yahoo_ticker", "sector", "sector_source_field", "sector_source_value",
            "sector_classification_quality", "tct_baseline_rank", "tct_baseline_status",
        ],
    ].copy()
    gaps["gap_reason"] = np.where(
        gaps["sector_classification_quality"].eq("HEURISTIC_ONLY"),
        "NO_ATTRIBUTED_SECTOR_FIELD_HEURISTIC_ONLY",
        "NO_SECTOR_METADATA",
    )
    gaps["required_action"] = "HYDRATE_SECTOR_WITH_ATTRIBUTED_SOURCE"

    details = details.sort_values(
        ["sector", "tct_baseline_rank", "tct_baseline_score", "isin"],
        ascending=[True, True, False, True],
        na_position="last",
    ).reset_index(drop=True)

    summary = {
        "version": VERSION,
        "role": ROLE,
        "rows": int(len(details)),
        "classified_with_attributed_field": int((~details["sector_gap_flag"]).sum()),
        "sector_metadata_gap_rows": int(details["sector_gap_flag"].sum()),
        "classified_sector_count": int(details.loc[~details["sector_gap_flag"], "sector"].nunique()),
        "baseline_top20_rows": int(details["tct_baseline_top20"].sum()),
        "t1_shadow_rows": int(details["timing_t1_flag"].sum()),
        "t2_shadow_rows": int(details["timing_t2_flag"].sum()),
        "earnings_date_known_rows": int(details["earnings_known_flag"].sum()),
        "earnings_d0_1_rows": int(details["earnings_d0_1_flag"].sum()),
        "earnings_d2_5_rows": int(details["earnings_d2_5_flag"].sum()),
        "event_gap_risk_rows": int(details["event_gap_risk_flag"].sum()),
        "score_changes": False,
        "t1_t2_score_influence": 0.0,
        "new_earnings_weight_added": False,
        "historical_performance_attribution": "NONE_REPORTING_CONTEXT_ONLY",
        "live_orders_enabled": False,
    }
    return details, dashboard, gaps, summary


def _safe_sheet_name(value: str, used: set[str]) -> str:
    clean = re.sub(r"[\[\]:*?/\\]", "_", str(value)).strip() or "SECTEUR"
    base = clean[:31]
    candidate = base
    suffix = 1
    while candidate in used:
        token = f"_{suffix}"
        candidate = base[: 31 - len(token)] + token
        suffix += 1
    used.add(candidate)
    return candidate


def write_outputs(details: pd.DataFrame, dashboard: pd.DataFrame, gaps: pd.DataFrame, summary: dict, outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    details_csv = outdir / "TCT_SECTOR_COMMITTEE_DETAILS.csv"
    dashboard_csv = outdir / "TCT_SECTOR_DASHBOARD.csv"
    gaps_csv = outdir / "TCT_SECTOR_CLASSIFICATION_GAPS.csv"
    summary_json = outdir / "TCT_SECTOR_CONTEXT_SUMMARY.json"
    workbook = outdir / "TCT_SECTOR_COMMITTEE.xlsx"

    details.to_csv(details_csv, sep=";", index=False, encoding="utf-8-sig")
    dashboard.to_csv(dashboard_csv, sep=";", index=False, encoding="utf-8-sig")
    gaps.to_csv(gaps_csv, sep=";", index=False, encoding="utf-8-sig")
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    used: set[str] = set()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        dashboard.to_excel(writer, sheet_name=_safe_sheet_name("Dashboard_TCT", used), index=False)
        details.to_excel(writer, sheet_name=_safe_sheet_name("Detail_TCT", used), index=False)
        gaps.to_excel(writer, sheet_name=_safe_sheet_name("Secteurs_a_hydrater", used), index=False)
        for sector in dashboard["sector"].head(12):
            subset = details[details["sector"].eq(sector)].head(30)
            if not subset.empty:
                subset.to_excel(writer, sheet_name=_safe_sheet_name(f"S_{sector}", used), index=False)

    return {
        "status": "SUCCESS",
        "version": VERSION,
        "details_csv": str(details_csv),
        "dashboard_csv": str(dashboard_csv),
        "classification_gaps_csv": str(gaps_csv),
        "summary_json": str(summary_json),
        "workbook": str(workbook),
        **summary,
    }


def run(root: Path) -> dict:
    outdir = root / "outputs" / "committee_master"
    baseline_path = outdir / "TCT_BASELINE_V24_1_8.csv"
    timing_path = outdir / "TCT_SHADOW_V24_1_7.csv"
    if not baseline_path.exists():
        raise FileNotFoundError(f"Missing TCT baseline output: {baseline_path}")
    if not timing_path.exists():
        raise FileNotFoundError(f"Missing TCT timing output: {timing_path}")
    baseline = pd.read_csv(baseline_path, sep=";", encoding="utf-8-sig", low_memory=False)
    timing = pd.read_csv(timing_path, sep=";", encoding="utf-8-sig", low_memory=False)
    details, dashboard, gaps, summary = build_context(baseline, timing)
    return write_outputs(details, dashboard, gaps, summary, outdir)
