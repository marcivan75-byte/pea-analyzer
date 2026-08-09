from __future__ import annotations

from datetime import date
import numpy as np
import pandas as pd

from .core import CaptureStore, is_observed, number, utcnow


def _latest_series(facts: pd.DataFrame, isin: str, field: str) -> list[tuple[str, float]]:
    x = facts[(facts["isin"].astype(str) == isin) & (facts["field"].astype(str) == field)].copy()
    if x.empty:
        return []
    x["_v"] = pd.to_numeric(x["value"], errors="coerce")
    x["_d"] = pd.to_datetime(x["as_of"], errors="coerce")
    x = x.dropna(subset=["_v", "_d"]).sort_values("_d", ascending=False)
    return [(d.date().isoformat(), float(v)) for d, v in zip(x["_d"], x["_v"], strict=False)]


def _base_num(row: pd.Series, field: str) -> float | None:
    return number(row.get(field)) if field in row and is_observed(row.get(field)) else None


def capture(base: pd.DataFrame, store: CaptureStore) -> dict:
    facts = store.facts()
    if facts.empty or not facts["source"].eq("ESEF_XBRL_JSON").any():
        store.add_health("INTERNAL_FROM_ESEF", "NO_INPUT")
        return {"status": "NO_INPUT", "facts_added": 0}
    rows: list[dict] = []
    instruments = 0
    today = date.today().isoformat()
    for _, base_row in base.iterrows():
        isin = str(base_row["isin"])
        e = facts[(facts["isin"].astype(str) == isin) & (facts["source"].eq("ESEF_XBRL_JSON"))]
        if e.empty:
            continue
        instruments += 1

        def latest(field: str) -> float | None:
            s = _latest_series(e, isin, field)
            return s[0][1] if s else None

        revenue = latest("revenue_esef")
        net_income = latest("net_income_esef")
        op_income = latest("operating_income_esef")
        assets = latest("assets_esef")
        equity = latest("equity_esef")
        cfo = latest("cfo_esef")
        capex = latest("capex_esef")
        debt_c = latest("borrowings_current_esef") or 0.0
        debt_nc = latest("borrowings_noncurrent_esef") or 0.0
        ebitda = latest("ebitda_esef")
        market_cap = _base_num(base_row, "market_cap_v21")
        if market_cap is None:
            ms = facts[(facts["isin"].astype(str) == isin) & (facts["field"].eq("market_cap_v21"))]
            if not ms.empty:
                market_cap = number(ms.iloc[-1]["value"])
        derived = {}
        if net_income is not None and equity not in {None, 0}:
            derived["roe_v21_pct"] = net_income / equity * 100.0
        if net_income is not None and assets not in {None, 0}:
            derived["roa_v21_pct"] = net_income / assets * 100.0
        if op_income is not None and revenue not in {None, 0}:
            derived["operating_margin_v21_pct"] = op_income / revenue * 100.0
        if cfo is not None:
            fcf = cfo - abs(capex or 0.0)
            derived["free_cash_flow_v21"] = fcf
            if market_cap not in {None, 0}:
                derived["fcf_yield_v21"] = fcf / market_cap * 100.0
        if market_cap not in {None, 0} and equity not in {None, 0}:
            derived["pb_v21"] = market_cap / equity
        total_debt = debt_c + debt_nc
        if ebitda not in {None, 0} and total_debt > 0:
            derived["debt_to_ebitda_v21"] = total_debt / ebitda
        revenues = _latest_series(e, isin, "revenue_esef")
        if len(revenues) >= 2 and revenues[1][1] != 0:
            derived["revenue_growth_v21_pct"] = (revenues[0][1] / revenues[1][1] - 1.0) * 100.0
        profits = _latest_series(e, isin, "net_income_esef")
        if len(profits) >= 2 and profits[1][1] != 0:
            derived["earnings_growth_v21_pct"] = (profits[0][1] / abs(profits[1][1]) - 1.0) * 100.0

        for field, value in derived.items():
            if value is None or not np.isfinite(value):
                continue
            rows.append({
                "isin": isin, "field": field, "value": float(value), "value_text": "", "as_of": today,
                "source": "INTERNAL_FROM_ESEF", "evidence": "DERIVED_FROM_A_OFFICIAL_STRUCTURED",
                "confidence": 0.90, "status": "DERIVED", "observed_at_utc": utcnow(),
            })
    added = store.upsert_facts(rows)
    store.add_health("INTERNAL_FROM_ESEF", "OK" if added else "NO_NEW_DATA", instruments, added, 0,
                     message="ROE/ROA/margins/growth/PB/FCF yield/debt-EBITDA derived locally")
    return {"status": "OK", "instruments": instruments, "facts_added": added}
