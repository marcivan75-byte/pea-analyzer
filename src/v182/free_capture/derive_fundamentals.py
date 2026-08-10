from __future__ import annotations

from datetime import date
import os
import numpy as np
import pandas as pd

from .boursorama_public import capture as capture_boursorama
from .zonebourse_public_v3 import capture as capture_zonebourse
from .canonical_merge import write_merged
from .core import CaptureStore, is_observed, load_config, number, utcnow, write_csv


PLAUSIBILITY_BOUNDS = {
    "roe_v21_pct": (-300.0, 300.0),
    "roa_v21_pct": (-150.0, 150.0),
    "operating_margin_v21_pct": (-150.0, 150.0),
    "net_margin_v21_pct": (-200.0, 200.0),
    "gross_margin_v21_pct": (-100.0, 100.0),
    "current_ratio_v21": (0.0, 50.0),
    "interest_coverage_v21": (-1000.0, 1000.0),
    "debt_to_equity_v21": (0.0, 2000.0),
    "fcf_yield_v21": (-100.0, 100.0),
    "pb_v21": (0.0, 100.0),
    "debt_to_ebitda_v21": (0.0, 50.0),
    "revenue_growth_v21_pct": (-100.0, 1000.0),
    "earnings_growth_v21_pct": (-500.0, 1000.0),
}
MAX_RATIO_PERIOD_GAP_DAYS = 45
MIN_GROWTH_GAP_DAYS = 250
MAX_GROWTH_GAP_DAYS = 500


def _latest_series(facts: pd.DataFrame, isin: str, field: str) -> list[tuple[pd.Timestamp, float]]:
    x = facts[(facts["isin"].astype(str) == isin) & (facts["field"].astype(str) == field)].copy()
    if x.empty:
        return []
    x["_v"] = pd.to_numeric(x["value"], errors="coerce")
    x["_d"] = pd.to_datetime(x["as_of"], errors="coerce")
    x = x.dropna(subset=["_v", "_d"]).sort_values("_d", ascending=False)
    return [(d, float(v)) for d, v in zip(x["_d"], x["_v"], strict=False)]


def _base_num(row: pd.Series, field: str) -> float | None:
    return number(row.get(field)) if field in row and is_observed(row.get(field)) else None


def _paired_latest(
    facts: pd.DataFrame,
    isin: str,
    numerator: str,
    denominator: str,
    max_gap_days: int = MAX_RATIO_PERIOD_GAP_DAYS,
) -> tuple[float, float, str] | None:
    a = _latest_series(facts, isin, numerator)
    b = _latest_series(facts, isin, denominator)
    best: tuple[int, pd.Timestamp, float, float] | None = None
    for da, va in a[:4]:
        for db, vb in b[:4]:
            gap = abs((da - db).days)
            if gap > max_gap_days:
                continue
            recent = max(da, db)
            cand = (gap, recent, va, vb)
            if best is None or gap < best[0] or (gap == best[0] and recent > best[1]):
                best = cand
    if best is None:
        return None
    return best[2], best[3], best[1].date().isoformat()


def _annual_growth(facts: pd.DataFrame, isin: str, field: str) -> tuple[float, str] | None:
    series = _latest_series(facts, isin, field)
    if len(series) < 2:
        return None
    for i, (d0, v0) in enumerate(series[:4]):
        for d1, v1 in series[i + 1 : i + 5]:
            gap = (d0 - d1).days
            if MIN_GROWTH_GAP_DAYS <= gap <= MAX_GROWTH_GAP_DAYS and v1 != 0:
                return (v0 / abs(v1) - 1.0) * 100.0, d0.date().isoformat()
    return None


def _plausible(field: str, value: float) -> bool:
    if value is None or not np.isfinite(value):
        return False
    lo, hi = PLAUSIBILITY_BOUNDS.get(field, (-np.inf, np.inf))
    return lo <= float(value) <= hi


def _match_value_near(series: list[tuple[pd.Timestamp, float]], anchor: pd.Timestamp) -> float | None:
    candidates = [(abs((anchor - d).days), v) for d, v in series[:4] if abs((anchor - d).days) <= MAX_RATIO_PERIOD_GAP_DAYS]
    return sorted(candidates, key=lambda x: x[0])[0][1] if candidates else None


def capture(base: pd.DataFrame, store: CaptureStore) -> dict:
    facts_all = store.facts()
    if not facts_all.empty and facts_all["source"].eq("INTERNAL_FROM_ESEF").any():
        facts_all = facts_all.loc[~facts_all["source"].eq("INTERNAL_FROM_ESEF")].copy()
        write_csv(facts_all, store.facts_path, ["isin", "field", "as_of", "source"])
    facts = facts_all
    if facts.empty or not facts["source"].eq("ESEF_XBRL_JSON").any():
        store.add_health("INTERNAL_FROM_ESEF", "NO_INPUT")
        return {"status": "NO_INPUT", "facts_added": 0}

    rows: list[dict] = []
    instruments = 0
    rejected_plausibility = 0
    rejected_period_mismatch = 0
    v2_fields_generated: dict[str, int] = {}
    today = date.today().isoformat()

    for _, base_row in base.iterrows():
        isin = str(base_row["isin"])
        e = facts[(facts["isin"].astype(str) == isin) & (facts["source"].eq("ESEF_XBRL_JSON"))]
        if e.empty:
            continue
        instruments += 1
        derived: dict[str, tuple[float, str]] = {}

        for out_field, num_field, den_field, scale in [
            ("roe_v21_pct", "net_income_esef", "equity_esef", 100.0),
            ("roa_v21_pct", "net_income_esef", "assets_esef", 100.0),
            ("operating_margin_v21_pct", "operating_income_esef", "revenue_esef", 100.0),
            ("net_margin_v21_pct", "net_income_esef", "revenue_esef", 100.0),
            ("gross_margin_v21_pct", "gross_profit_esef", "revenue_esef", 100.0),
            ("current_ratio_v21", "current_assets_esef", "current_liabilities_esef", 1.0),
            ("interest_coverage_v21", "operating_income_esef", "interest_expense_esef", 1.0),
        ]:
            pair = _paired_latest(e, isin, num_field, den_field)
            if pair is None:
                if out_field in {"gross_margin_v21_pct", "current_ratio_v21", "interest_coverage_v21"}:
                    continue
                rejected_period_mismatch += 1
                continue
            num, den, as_of = pair
            if den == 0:
                continue
            if out_field == "interest_coverage_v21":
                value = num / abs(den)
            elif out_field == "current_ratio_v21":
                if den <= 0:
                    continue
                value = num / den
            else:
                value = num / den * scale
            derived[out_field] = (value, as_of)

        # Gross margin fallback when the issuer reports cost of sales but not gross profit.
        if "gross_margin_v21_pct" not in derived:
            pair = _paired_latest(e, isin, "cost_of_sales_esef", "revenue_esef")
            if pair is not None:
                cost, revenue, as_of = pair
                if revenue != 0:
                    derived["gross_margin_v21_pct"] = ((revenue - abs(cost)) / revenue * 100.0, as_of)

        cfo = _latest_series(e, isin, "cfo_esef")
        capex = _latest_series(e, isin, "capex_esef")
        if cfo:
            cfo_date, cfo_value = cfo[0]
            capex_value = 0.0
            capex_date = cfo_date
            if capex:
                matches = [(abs((cfo_date - d).days), d, v) for d, v in capex[:4] if abs((cfo_date - d).days) <= MAX_RATIO_PERIOD_GAP_DAYS]
                if matches:
                    _, capex_date, capex_value = sorted(matches, key=lambda x: (x[0], -x[1].value))[0]
                else:
                    rejected_period_mismatch += 1
            fcf = cfo_value - abs(capex_value)
            as_of = max(cfo_date, capex_date).date().isoformat()
            derived["free_cash_flow_v21"] = (fcf, as_of)
            market_cap = _base_num(base_row, "market_cap_v21")
            if market_cap is not None and market_cap != 0:
                derived["fcf_yield_v21"] = (fcf / market_cap * 100.0, as_of)

        market_cap = _base_num(base_row, "market_cap_v21")
        equity = _latest_series(e, isin, "equity_esef")
        if market_cap not in {None, 0} and equity and equity[0][1] != 0:
            derived["pb_v21"] = (market_cap / equity[0][1], equity[0][0].date().isoformat())

        debt_c = _latest_series(e, isin, "borrowings_current_esef")
        debt_nc = _latest_series(e, isin, "borrowings_noncurrent_esef")

        # Explicit EBITDA first; otherwise operating profit + combined D&A on a coherent period.
        ebitda = _latest_series(e, isin, "ebitda_esef")
        ebitda_point: tuple[pd.Timestamp, float] | None = ebitda[0] if ebitda else None
        if ebitda_point is None:
            op = _latest_series(e, isin, "operating_income_esef")
            da = _latest_series(e, isin, "da_combined_esef")
            if op:
                odate, oval = op[0]
                daval = _match_value_near(da, odate)
                if daval is not None:
                    ebitda_point = (odate, oval + abs(daval))
                    derived["ebitda_v21"] = (ebitda_point[1], odate.date().isoformat())
        elif ebitda_point:
            derived["ebitda_v21"] = (ebitda_point[1], ebitda_point[0].date().isoformat())

        if ebitda_point and ebitda_point[1] != 0:
            edate, eval_ = ebitda_point
            total_debt = 0.0
            matched = False
            for series in (debt_c, debt_nc):
                val = _match_value_near(series, edate)
                if val is not None:
                    total_debt += val
                    matched = True
            if matched and total_debt >= 0:
                derived["total_debt_v21"] = (total_debt, edate.date().isoformat())
                derived["debt_to_ebitda_v21"] = (total_debt / eval_, edate.date().isoformat())

        # Debt/equity uses only balance-sheet items on compatible dates and positive equity.
        if equity:
            eq_date, eq_val = equity[0]
            if eq_val > 0:
                total_debt = 0.0
                matched = False
                for series in (debt_c, debt_nc):
                    val = _match_value_near(series, eq_date)
                    if val is not None:
                        total_debt += val
                        matched = True
                if matched and total_debt >= 0:
                    derived["debt_to_equity_v21"] = (total_debt / eq_val * 100.0, eq_date.date().isoformat())

        for out_field, raw_field in [
            ("revenue_growth_v21_pct", "revenue_esef"),
            ("earnings_growth_v21_pct", "net_income_esef"),
        ]:
            growth = _annual_growth(e, isin, raw_field)
            if growth is not None:
                derived[out_field] = growth

        for field, (value, as_of) in derived.items():
            if field not in {"free_cash_flow_v21", "ebitda_v21", "total_debt_v21"} and not _plausible(field, value):
                rejected_plausibility += 1
                continue
            if value is None or not np.isfinite(value):
                continue
            if field in {"net_margin_v21_pct", "gross_margin_v21_pct", "current_ratio_v21", "interest_coverage_v21", "ebitda_v21", "debt_to_equity_v21"}:
                v2_fields_generated[field] = v2_fields_generated.get(field, 0) + 1
            rows.append({
                "isin": isin,
                "field": field,
                "value": float(value),
                "value_text": "",
                "as_of": as_of or today,
                "source": "INTERNAL_FROM_ESEF",
                "evidence": "DERIVED_FROM_A_OFFICIAL_STRUCTURED_V2",
                "confidence": 0.90,
                "status": "VALIDATED_DERIVED",
                "observed_at_utc": utcnow(),
            })

    added = store.upsert_facts(rows)
    msg = (
        "period-coherent ESEF V2 derivations; "
        f"v2_fields={v2_fields_generated}; rejected_plausibility={rejected_plausibility}; "
        f"rejected_period_mismatch={rejected_period_mismatch}"
    )
    store.add_health(
        "INTERNAL_FROM_ESEF",
        "OK" if added else "NO_NEW_DATA",
        instruments,
        added,
        rejected_plausibility + rejected_period_mismatch,
        message=msg,
    )
    cfg = load_config()
    boursorama = capture_boursorama(
        base,
        store,
        cfg,
        max_symbols=int(os.getenv("V211_BOURSORAMA_MAX_SYMBOLS", "40")),
    )
    zonebourse = capture_zonebourse(
        base,
        store,
        cfg,
        max_symbols=int(os.getenv("V211_ZONEBOURSE_MAX_SYMBOLS", "80")),
    )
    merge_audit = write_merged(base, store, cfg)
    return {
        "status": "OK",
        "instruments": instruments,
        "facts_added": added,
        "v2_fields_generated": v2_fields_generated,
        "rejected_plausibility": rejected_plausibility,
        "rejected_period_mismatch": rejected_period_mismatch,
        "boursorama": boursorama,
        "zonebourse": zonebourse,
        "canonical_merge": merge_audit,
    }
