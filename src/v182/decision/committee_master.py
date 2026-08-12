from __future__ import annotations
from pathlib import Path
from typing import Iterable
import json
import pandas as pd
import numpy as np

HORIZONS = ("CT", "MT", "LT", "SHORT", "TOP_DOWN")

# Canonical V21 names may differ from the historical V18.2 storage schema.
# Only semantically equivalent aliases are allowed here. An alias never
# changes a criterion's weight or direction and never deletes the source field.
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
    "fcf_yield_v21": ("fcf_yield",),
    "debt_to_ebitda_v21": ("dette_ebitda", "debt_to_ebitda"),
}


def load_registry(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _to_numeric(series: pd.Series, field: str) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    s = series.astype(str).str.strip()
    low = s.str.lower()
    bool_map = {"true":1.0,"false":0.0,"yes":1.0,"no":0.0,"oui":1.0,"non":0.0,"pass":1.0,"fail":0.0,"1":1.0,"0":0.0,"distribution":1.0,"distributing":1.0,"dist":1.0,"accumulation":0.5,"accumulating":0.5,"acc":0.5}
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


def resolve_field(frame: pd.DataFrame, name: str) -> tuple[pd.Series | None, str]:
    """Resolve one canonical criterion without semantic substitution.

    Resolution order: exact canonical field -> approved alias -> exact derived
    formula. Fields with different horizons/definitions are deliberately not
    aliased (for example 3-year growth is not used as current revenue growth).
    """
    if name in frame.columns:
        return _to_numeric(frame[name], name), f"DIRECT:{name}"

    for alias in FIELD_ALIASES.get(name, ()):
        if alias in frame.columns:
            values = _to_numeric(frame[alias], alias)
            if values.notna().any():
                return values, f"ALIAS:{alias}"

    if name == "consensus_score_100_v21":
        values, source = _first_numeric(frame, ("consensus_score",))
        if values is not None:
            # Finnhub consensus_score is a 1..5 weighted mean; scale is
            # monotonic and therefore preserves cross-sectional rank.
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
    """Audit active criterion availability and canonical resolution source."""
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
    if asset_class == "GOLD": return "METAUX PRECIEUX"
    fields = ("sector","sector_yf","sector_yahoo","industry","industry_yf","morningstar_category","category","geo_exposure","official_benchmark")
    raw = ""
    for f in fields:
        if f in row and pd.notna(row[f]) and str(row[f]).strip(): raw = str(row[f]).strip(); break
    if not raw: return "NON CLASSE"
    t = raw.lower()
    mapping = [(('bank','financial','finance','assurance','insurance'),'FINANCE'),(('health','pharma','biotech','santé','medical'),'SANTE'),(('technology','tech','software','semiconductor'),'TECHNOLOGIE'),(('industrial','construction','engineering','aerospace','machinery'),'INDUSTRIE'),(('energy','oil','gas','petrol','énergie'),'ENERGIE'),(('utility','utilities','electric','water','grid'),'UTILITIES'),(('telecom','communication','media','entertainment'),'COMMUNICATION / MEDIAS'),(('consumer','retail','luxury','food','beverage'),'CONSOMMATION'),(('real estate','immobilier','reit'),'IMMOBILIER'),(('material','chemical','mining','metal'),'MATERIAUX')]
    for tokens,label in mapping:
        if any(tok in t for tok in tokens): return label
    return "ETF MULTISECTORIEL / PAYS" if asset_class == "ETF" else raw.upper()[:80]


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
        out.append({"asset_class":"ETF","horizon":"MT","isin":isin,"name":str(rr.get(name_col,"") if name_col else mrow.get("name","")),"sector":classify_sector(mrow,"ETF"),"score":pd.to_numeric(rr.get(score_col,np.nan),errors="coerce") if score_col else np.nan,"coverage_pct":pd.to_numeric(rr.get(coverage_col,100.0),errors="coerce") if coverage_col else 100.0,"status":str(rr.get("status","SCORABLE")),"decision":str(rr.get(decision_col,"") if decision_col else rr.get("decision","")),"active_criteria":38,"available_criteria":38,"score_source":"V20.8.1_DYNAMIC_38_CORE","backtest_attribution":"Historical OOS validation 2021-2023: 90.91% for the 38 dynamic PIT core only.","notes":"Full ETF referential preserved separately; structural/qualitative criteria remain visible for committee review and reweighting."})
    return pd.DataFrame(out)


def tct_adapter() -> pd.DataFrame:
    return pd.DataFrame([{"asset_class":"ACTION","horizon":"TCT","isin":"","name":"ACTION TCT / T1-T2 MODULE","sector":"TRANSVERSAL","score":np.nan,"coverage_pct":0.0,"status":"SHADOW_INPUT_REQUIRED","decision":"SHADOW_INPUT_REQUIRED","active_criteria":0,"available_criteria":0,"score_source":"V24.1.7_T1_T2_V2","backtest_attribution":"","notes":"T1/T2 ACTION TCT only; 0 influence on base score; no ETF/non-TCT use; live execution forbidden."}])


def gold_adapter(gold_registry_path: str | Path) -> pd.DataFrame:
    p=Path(gold_registry_path)
    if not p.exists():
        return pd.DataFrame([{"asset_class":"GOLD","horizon":h,"isin":"","name":"OR","sector":"METAUX PRECIEUX","score":np.nan,"coverage_pct":0.0,"status":"BLOCKED_REFERENCE","decision":"ABSTAIN_BLOCKED_REFERENCE","active_criteria":102,"available_criteria":0,"score_source":"GOLD_V1_CONTRACT","backtest_attribution":"","notes":"Exact 102-criterion PIT registry not present. No score or weight is fabricated."} for h in ("TACTICAL_2_12W","STRATEGIC_6_24M")])
    cfg=json.loads(p.read_text(encoding="utf-8")); rows=[]
    for h,spec in cfg.get("current_scores",{}).items():
        rows.append({"asset_class":"GOLD","horizon":h,"isin":"","name":"OR","sector":"METAUX PRECIEUX","score":spec.get("score"),"coverage_pct":spec.get("coverage_pct",0),"status":spec.get("status","SCORABLE"),"decision":spec.get("decision","REVIEW"),"active_criteria":len(cfg.get("criteria",[])),"available_criteria":spec.get("available_criteria",0),"score_source":cfg.get("version","GOLD_V1"),"backtest_attribution":"","notes":"Exact GOLD registry supplied."})
    return pd.DataFrame(rows)


def sector_ranking(decisions: pd.DataFrame) -> pd.DataFrame:
    d=decisions.copy(); d["score_num"]=pd.to_numeric(d["score"],errors="coerce"); sc=d[d["score_num"].notna()].copy()
    if sc.empty: return pd.DataFrame(columns=["sector","asset_class","horizon","rank","name","isin","score","decision","coverage_pct"])
    sc["rank"]=sc.groupby(["sector","asset_class","horizon"])["score_num"].rank(method="first",ascending=False)
    sc=sc.sort_values(["sector","asset_class","horizon","rank"])
    return sc[["sector","asset_class","horizon","rank","name","isin","score_num","decision","coverage_pct"]].rename(columns={"score_num":"score"})
