from __future__ import annotations

from datetime import date
import os
import numpy as np
import pandas as pd

from .boursorama_public import capture as capture_boursorama
from .canonical_merge import write_merged
from .core import CaptureStore, is_observed, load_config, number, utcnow, write_csv


PLAUSIBILITY_BOUNDS = {
    "roe_v21_pct": (-300.0, 300.0),
    "roa_v21_pct": (-150.0, 150.0),
    "operating_margin_v21_pct": (-150.0, 150.0),
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
        ]:
            pair = _paired_latest(e, isin, num_field, den_field)
            if pair is None:
                rejected_period_mismatch += 1
                continue
            num, den, as_of = pair
            if den == 0:
                continue
            derived[out_field] = (num / den * scale, as_of)

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

        ebitda = _latest_series(e, isin, "ebitda_esef")
        debt_c = _latest_series(e, isin, "borrowings_current_esef")
        debt_nc = _latest_series(e, isin, "borrowings_noncurrent_esef")
        if ebitda and ebitda[0][1] != 0:
            edate, eval_ = ebitda[0]
            total_debt = 0.0
            matched = False
            for series in (debt_c, debt_nc):
                if not series:
                    continue
                candidates = [(abs((edate - d).days), v) for d, v in series[:4] if abs((edate - d).days) <= MAX_RATIO_PERIOD_GAP_DAYS]
                if candidates:
                    total_debt += sorted(candidates, key=lambda x: x[0])[0][1]
                    matched = True
            if matched and total_debt >= 0:
                derived["debt_to_ebitda_v21"] = (total_debt / eval_, edate.date().isoformat())

        for out_field, raw_field in [
            ("revenue_growth_v21_pct", "revenue_esef"),
            ("earnings_growth_v21_pct", "net_income_esef"),
        ]:
            growth = _annual_growth(e, isin, raw_field)
            if growth is not None:
                derived[out_field] = growth

        for field, (value, as_of) in derived.items():
            if field != "free_cash_flow_v21" and not _plausible(field, value):
                rejected_plausibility += 1
                continue
            if value is None or not np.isfinite(value):
                continue
            rows.append({
                "isin": isin,
                "field": field,
                "value": float(value),
                "value_text": "",
                "as_of": as_of or today,
                "source": "INTERNAL_FROM_ESEF",
                "evidence": "DERIVED_FROM_A_OFFICIAL_STRUCTURED",
                "confidence": 0.90,
                "status": "VALIDATED_DERIVED",
                "observed_at_utc": utcnow(),
            })

    added = store.upsert_facts(rows)
    msg = (
        "period-coherent ROE/ROA/margins/growth/PB/FCF yield/debt-EBITDA; "
        f"rejected_plausibility={rejected_plausibility}; "
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
    merge_audit = write_merged(base, store, cfg)
    return {
        "status": "OK",
        "instruments": instruments,
        "facts_added": added,
        "rejected_plausibility": rejected_plausibility,
        "rejected_period_mismatch": rejected_period_mismatch,
        "boursorama": boursorama,
        "canonical_merge": merge_audit,
    }
