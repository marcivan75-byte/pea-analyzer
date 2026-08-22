from __future__ import annotations
from pathlib import Path
from typing import Iterable
import json
import pandas as pd
import numpy as np

HORIZONS = ("CT", "MT", "SHORT", "TOP_DOWN")

# Canonical V21 names may differ from the historical V18.2 storage schema.
# Only semantically equivalent aliases are allowed. Aliases never change a
# criterion weight/direction and source fields remain preserved in the master.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "per_forward_v21": ("per_forward", "per_forward_yf"),
    "pb_v21": ("pb",),
    "beta_v21": ("beta",),
    "roe_v21_pct": ("roe", "roe_api"),
    "roa_v21_pct": ("roa",),
    "operating_margin_v21_pct": ("marge_ebit",),
    "net_margin_v21_pct": ("marge_nette",),
    "revenue_growth_v21_pct": ("revenue_growth_yf",),
    "earnings_growth_v21_pct": ("earnings_growth_yf",),
    "debt_to_equity_v21": ("debt_to_equity",),
    "dividend_yield_v21_pct": ("dividend_yield_pct",),
    "payout_ratio_v21_pct": ("payout_ratio",),
    "consensus_score_100_v21": ("consensus_score_100", "consensus_score_yf"),
    "target_upside_pct_v21": ("target_upside_pct", "upside_pct", "upside_pct_yf"),
    "consensus_delta_4w": ("consensus_delta_4w", "consensus_delta", "consensus_delta_yf"),
    "net_upgrades_30d_v21": ("net_upgrades_30d",),
    "broker_weighted_revision_30d": ("broker_weighted_revision_30d",),
    "fcf_yield_v21": ("fcf_yield",),
    "debt_to_ebitda_v21": ("dette_ebitda", "debt_to_ebitda"),
    "diversification_direct_score": ("direct_diversification_score",),
    "direct_beta3y": ("beta_3y", "beta3y", "beta_3y_structural"),
}


def load_registry(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _to_numeric(series: pd.Series, field: str) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    s = series.astype(str).str.strip()
    low = s.str.lower()
    bool_map = {
        "true":1.0,"false":0.0,"yes":1.0,"no":0.0,"oui":1.0,"non":0.0,
        "pass":1.0,"fail":0.0,"1":1.0,"0":0.0,
        "distribution":1.0,"distributing":1.0,"dist":1.0,
        "accumulation":0.5,"accumulating":0.5,"acc":0.5,
    }
    mapped = low.map(bool_map)
    numeric = pd.to_numeric(s.str.replace(",", ".", regex=False).str.replace("%","",regex=False), errors="coerce")
    return numeric.where(numeric.notna(), mapped)


def _first_numeric(frame: pd.DataFrame, fields: tuple[str, ...]) -> tuple[pd.Series | None, str | None]:
    for field in fields:
        if field in frame.columns:
            values = _to_numeric(frame[field], field)
            if values.notna().any():
                return values, field
    return None, None


def _sector_series(frame: pd.DataFrame) -> pd.Series:
    out = pd.Series("UNKNOWN", index=frame.index, dtype=object)
    fields = ("sector_v21", "sector_yf", "sector", "sector_yahoo", "industry_yf", "industry")
    for field in fields:
        if field not in frame.columns:
            continue
        raw = frame[field].astype(str).str.strip()
        valid = ~raw.str.lower().isin({"", "nan", "none", "n/a", "na"})
        out = out.where(~((out == "UNKNOWN") & valid), raw)
    return out


def _sector_percentile(values: pd.Series, sectors: pd.Series, *, direction: str) -> pd.Series:
    x = pd.to_numeric(values, errors="coerce")
    result = pd.Series(np.nan, index=x.index, dtype=float)
    for _sector, idx in sectors.groupby(sectors).groups.items():
        sub = x.loc[idx]
        if sub.notna().sum() < 2:
            continue
        ascending = direction != "LOW"
        result.loc[idx] = sub.rank(method="average", pct=True, ascending=ascending) * 100.0
    return result


def _valuation_discount_score(frame: pd.DataFrame) -> tuple[pd.Series | None, str]:
    """V21 formula: 45% inverse Forward PER + 20% inverse P/B + 35% FCF yield.

    Each component is ranked within sector and row weights are renormalised when
    one component is genuinely unavailable. A missing component is never
    replaced by a neutral score.
    """
    per, per_src = _first_numeric(frame, ("per_forward_v21", "per_forward", "per_forward_yf"))
    pb, pb_src = _first_numeric(frame, ("pb_v21", "pb"))
    fcf, fcf_src = resolve_field(frame, "fcf_yield_v21") if "fcf_yield_v21" not in frame.columns else (_to_numeric(frame["fcf_yield_v21"], "fcf_yield_v21"), "fcf_yield_v21")
    if per is None and pb is None and fcf is None:
        return None, "MISSING"
    sectors = _sector_series(frame)
    numer = pd.Series(0.0, index=frame.index)
    denom = pd.Series(0.0, index=frame.index)
    sources=[]
    for values, weight, direction, source in (
        (per, 0.45, "LOW", per_src),
        (pb, 0.20, "LOW", pb_src),
        (fcf, 0.35, "HIGH", fcf_src),
    ):
        if values is None:
            continue
        ranked = _sector_percentile(values, sectors, direction=direction)
        ok = ranked.notna()
        numer += ranked.fillna(0.0) * weight
        denom += ok.astype(float) * weight
        sources.append(str(source))
    score = numer / denom.replace(0, np.nan)
    if not score.notna().any():
        return None, "MISSING"
    return score, "DERIVED:SECTOR_NEUTRAL_VALUATION_45_20_35:" + ",".join(sources)


def resolve_field(frame: pd.DataFrame, name: str) -> tuple[pd.Series | None, str]:
    """Resolve canonical criteria without semantic substitution."""
    if name in frame.columns:
        values = _to_numeric(frame[name], name)
        if values.notna().any():
            return values, f"DIRECT:{name}"

    for alias in FIELD_ALIASES.get(name, ()):
        if alias in frame.columns:
            values = _to_numeric(frame[alias], alias)
            if values.notna().any():
                return values, f"ALIAS:{alias}"

    if name == "consensus_score_100_v21":
        values, source = _first_numeric(frame, ("consensus_score",))
        if values is not None:
            return values * 20.0, f"DERIVED:{source}*20"

    if name == "target_upside_pct_v21":
        target, target_source = _first_numeric(frame, ("target_price", "target_mean_yf"))
        price, price_source = _first_numeric(frame, ("last_close", "current_price_yf"))
        if target is not None and price is not None:
            valid = price.where(price != 0)
            return (target / valid - 1.0) * 100.0, f"DERIVED:{target_source}/{price_source}"

    if name == "fcf_yield_v21":
        fcf, fcf_source = _first_numeric(frame, ("free_cash_flow",))
        cap, cap_source = _first_numeric(frame, ("market_cap",))
        if fcf is not None and cap is not None:
            valid = cap.where(cap != 0)
            return fcf / valid * 100.0, f"DERIVED:{fcf_source}/{cap_source}"

    if name == "debt_to_ebitda_v21":
        debt, debt_source = _first_numeric(frame, ("total_debt_yf",))
        ebitda, ebitda_source = _first_numeric(frame, ("ebitda_yf",))
        if debt is not None and ebitda is not None:
            valid = ebitda.where(ebitda != 0)
            return debt / valid, f"DERIVED:{debt_source}/{ebitda_source}"

    if name == "valuation_discount_score":
        return _valuation_discount_score(frame)

    return None, "MISSING"


def _pct_score(series: pd.Series, direction: str) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    if direction == "LOW":
        return x.rank(method="average", pct=True, ascending=False) * 100.0
    return x.rank(method="average", pct=True, ascending=True) * 100.0


def active_criteria(registry: dict, horizon: str) -> list[tuple[str, float, str]]:
    active: list[tuple[str, float, str]] = []
    if registry.get("weights"):
        wmap = registry.get("weights", {}).get(horizon, {})
        dmap = registry.get("directions", {}).get(horizon, {})
        active = [(name, float(w or 0.0), dmap.get(name, "HIGH")) for name,w in wmap.items() if float(w or 0.0) > 0]
    else:
        for c in registry.get("criteria", []):
            w = float(c.get("weights", {}).get(horizon, 0.0) or 0.0)
            if w > 0:
                active.append((c["name"], w, c.get("directions", {}).get(horizon, "HIGH")))
    return active


def score_horizon(frame: pd.DataFrame, registry: dict, horizon: str) -> pd.DataFrame:
    """Cross-sectional weighted scoring with explicit coverage and no silent validity."""
    if horizon not in HORIZONS:
        raise ValueError(f"Unsupported horizon: {horizon}")
    active = active_criteria(registry, horizon)
    if not active:
        return pd.DataFrame(index=frame.index, data={"score":np.nan,"coverage_pct":0.0,"status":"BLOCKED_CONFIG","decision":"BLOCKED_CONFIG","active_criteria":0,"available_criteria":0})
    total_weight = sum(w for _,w,_ in active)
    numer = pd.Series(0.0, index=frame.index)
    denom = pd.Series(0.0, index=frame.index)
    available_count = pd.Series(0, index=frame.index, dtype=int)
    for name,w,direction in active:
        vals, _source = resolve_field(frame, name)
        if vals is None:
            continue
        scored = _pct_score(vals, direction)
        ok = scored.notna()
        numer = numer.add(scored.fillna(0.0) * w, fill_value=0.0)
        denom = denom.add(ok.astype(float) * w, fill_value=0.0)
        available_count = available_count.add(ok.astype(int), fill_value=0).astype(int)
    coverage = denom / total_weight
    score = numer / denom.replace(0, np.nan)
    minimum = float(registry.get("horizons",{}).get(horizon,{}).get("minimum_weighted_coverage",0.70))
    status = pd.Series(np.where(coverage >= minimum, "SCORABLE", "BLOCK_DATA"), index=frame.index)
    decision = _decision(score, status, registry.get("horizons",{}).get(horizon,{}), horizon)
    return pd.DataFrame({"score":score.round(4),"coverage_pct":(coverage*100).round(2),"status":status,"decision":decision,"active_criteria":len(active),"available_criteria":available_count}, index=frame.index)


def criterion_coverage_report(frame: pd.DataFrame, registry: dict, asset_class: str, horizons: Iterable[str]) -> pd.DataFrame:
    rows=[]
    n=max(len(frame),1)
    for horizon in horizons:
        for name,weight,direction in active_criteria(registry,horizon):
            values, source = resolve_field(frame,name)
            available = int(values.notna().sum()) if values is not None else 0
            rows.append({"asset_class":asset_class,"horizon":horizon,"criterion":name,"weight":weight,"direction":direction,"resolution":source,"available_rows":available,"universe_rows":len(frame),"availability_pct":round(available/n*100.0,2),"criterion_status":"AVAILABLE" if available else "MISSING"})
    return pd.DataFrame(rows)


def _decision(score: pd.Series, status: pd.Series, cfg: dict, horizon: str) -> pd.Series:
    out=[]
    for sc,st in zip(score,status):
        if st != "SCORABLE" or pd.isna(sc):
            out.append(st); continue
        if horizon == "SHORT":
            if sc >= float(cfg.get("short_candidate_threshold",77)): out.append("SHORT_RISK_CANDIDATE")
            elif sc >= float(cfg.get("watch_threshold",70)): out.append("WATCH_SHORT_RISK")
            else: out.append("NO_SHORT_RISK")
        elif horizon == "TOP_DOWN":
            out.append("FAVORABLE" if sc >= 60 else "NEUTRAL" if sc >= 40 else "DEFAVORABLE")
        else:
            if sc >= float(cfg.get("buy_threshold",77)): out.append("BUY_CANDIDATE")
            elif sc >= float(cfg.get("watch_threshold",70)): out.append("WATCH")
            elif sc >= float(cfg.get("review_threshold",60)): out.append("REVIEW")
            else: out.append("REJECT")
    return pd.Series(out,index=score.index)


def classify_sector(row: pd.Series, asset_class: str) -> str:
    fields = (
        "sector_v21","sector_yf","sector","sector_yahoo","industry_yf","industry",
        "morningstar_category","category","geo_exposure","official_benchmark","name",
    )
    candidates=[]
    for f in fields:
        if f in row and pd.notna(row[f]):
            raw=str(row[f]).strip()
            if raw and raw.lower() not in {"nan","none","n/a","na"}:
                candidates.append(raw)
    if not candidates:
        return "NON CLASSE"
    t=" | ".join(candidates).lower()
    mapping = [
        (("bank","financial","finance","assurance","insurance","asset management","capital markets"),"FINANCE"),
        (("health","pharma","biotech","santé","medical","life sciences"),"SANTE"),
        (("technology","tech","software","semiconductor","it services","electronics"),"TECHNOLOGIE"),
        (("industrial","construction","engineering","aerospace","machinery","transportation","logistics"),"INDUSTRIE"),
        (("energy","oil","gas","petrol","énergie","offshore","drilling"),"ENERGIE"),
        (("utility","utilities","electric","water","grid","renewable utilities"),"UTILITIES"),
        (("telecom","communication","media","entertainment","publishing","broadcast"),"COMMUNICATION / MEDIAS"),
        (("consumer","retail","luxury","food","beverage","restaurant","apparel","household"),"CONSOMMATION"),
        (("real estate","immobilier","reit","property"),"IMMOBILIER"),
        (("material","chemical","mining","metal","forest products","paper"),"MATERIAUX"),
    ]
    for tokens,label in mapping:
        if any(tok in t for tok in tokens):
            return label
    if asset_class == "ETF":
        return "ETF MULTISECTORIEL / PAYS"
    # Preserve a meaningful provider sector when it exists instead of reducing
    # everything to NON CLASSE.
    for raw in candidates:
        if raw.lower() not in {str(row.get("name","")).strip().lower()}:
            return raw.upper()[:80]
    return "NON CLASSE"


def decisions_from_scores(frame: pd.DataFrame, registry: dict, asset_class: str, horizons: Iterable[str]) -> pd.DataFrame:
    parts=[]
    for horizon in horizons:
        scored=score_horizon(frame, registry, horizon)
        for idx in frame.index:
            row=frame.loc[idx]
            parts.append({"asset_class":asset_class,"horizon":horizon,"isin":str(row.get("isin","") or ""),"name":str(row.get("name","") or ""),"sector":classify_sector(row,asset_class),"score":scored.loc[idx,"score"],"coverage_pct":scored.loc[idx,"coverage_pct"],"status":scored.loc[idx,"status"],"decision":scored.loc[idx,"decision"],"active_criteria":int(scored.loc[idx,"active_criteria"]),"available_criteria":int(scored.loc[idx,"available_criteria"]),"score_source":registry.get("version",""),"backtest_attribution":"","notes":""})
    return pd.DataFrame(parts)


def overlay_etf_mt(etf_frame: pd.DataFrame, mt_ranking: pd.DataFrame | None) -> pd.DataFrame:
    if mt_ranking is None or mt_ranking.empty:
        return pd.DataFrame([{"asset_class":"ETF","horizon":"MT","isin":"","name":"ETF MT MODULE","sector":"ETF MULTISECTORIEL / PAYS","score":np.nan,"coverage_pct":0.0,"status":"BLOCKED_MT_RUN","decision":"BLOCKED_MT_RUN","active_criteria":38,"available_criteria":0,"score_source":"V20.8.1_DYNAMIC_38_CORE","backtest_attribution":"Historical OOS validation 2021-2023: 90.91% for 38 dynamic PIT criteria only.","notes":"Run V20.8.1 ranking unavailable; no fallback score fabricated."}])
    r=mt_ranking.copy()
    isin_col=next((c for c in ["isin","ISIN"] if c in r.columns),None)
    name_col=next((c for c in ["name","Nom","etf_name"] if c in r.columns),None)
    score_col=next((c for c in ["final_score","score_final","score"] if c in r.columns),None)
    decision_col=next((c for c in ["decision","status_decision"] if c in r.columns),None)
    coverage_col=next((c for c in ["coverage_pct","data_coverage_pct"] if c in r.columns),None)
    master = etf_frame.set_index("isin",drop=False) if "isin" in etf_frame.columns else pd.DataFrame()
    out=[]
    for _,rr in r.iterrows():
        isin=str(rr.get(isin_col,"") if isin_col else "")
        mrow=master.loc[isin] if not master.empty and isin in master.index else rr
        if isinstance(mrow,pd.DataFrame): mrow=mrow.iloc[0]
        out.append({"asset_class":"ETF","horizon":"MT","isin":isin,"name":str(rr.get(name_col,"") if name_col else mrow.get("name","")),"sector":classify_sector(mrow,"ETF"),"score":pd.to_numeric(rr.get(score_col,np.nan),errors="coerce") if score_col else np.nan,"coverage_pct":pd.to_numeric(rr.get(coverage_col,100.0),errors="coerce") if coverage_col else 100.0,"status":str(rr.get("status","SCORABLE")),"decision":str(rr.get(decision_col,"") if decision_col else rr.get("decision","")),"active_criteria":38,"available_criteria":38,"score_source":"V20.8.1_DYNAMIC_38_CORE","backtest_attribution":"Historical OOS validation 2021-2023: 90.91% for the 38 dynamic PIT core only.","notes":"38 PIT dynamic core only. Full ETF structural/qualitative referential remains active at Committee layer and separately attributable."})
    return pd.DataFrame(out)


def tct_adapter(tct_shadow: pd.DataFrame | None = None) -> pd.DataFrame:
    if tct_shadow is not None and not tct_shadow.empty:
        return tct_shadow.copy()
    return pd.DataFrame([{"asset_class":"ACTION","horizon":"TCT","isin":"","name":"ACTION TCT / T1-T2 MODULE","sector":"TRANSVERSAL","score":np.nan,"coverage_pct":0.0,"status":"SHADOW_BASELINE_REQUIRED","decision":"SHADOW_BASELINE_REQUIRED","active_criteria":0,"available_criteria":0,"score_source":"V24.1.7_T1_T2_V2","backtest_attribution":"V24.1.7 V2 timing overlay not yet promoted; prior T1/T2 OOS failed promotion gates.","notes":"T1/T2 ACTION TCT only; timing overlay; 0 influence on base score; no ETF/non-TCT use; live execution forbidden."}])


def sector_ranking(decisions: pd.DataFrame) -> pd.DataFrame:
    d=decisions.copy(); d["score_num"]=pd.to_numeric(d["score"],errors="coerce"); sc=d[d["score_num"].notna()].copy()
    if sc.empty: return pd.DataFrame(columns=["sector","asset_class","horizon","rank","name","isin","score","decision","coverage_pct"])
    sc["rank"]=sc.groupby(["sector","asset_class","horizon"])["score_num"].rank(method="first",ascending=False)
    sc=sc.sort_values(["sector","asset_class","horizon","rank"])
    return sc[["sector","asset_class","horizon","rank","name","isin","score_num","decision","coverage_pct"]].rename(columns={"score_num":"score"})
