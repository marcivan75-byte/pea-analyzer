"""Decisive research campaign: benchmark vs concentrated stock picking.

This intentionally stops model-complexity research. It reuses the governed OHLCV
and current untrained/J1-confirmed signal family, changes only concentration, and
compares against a real PEA World ETF benchmark (CW8.PA).

Governance:
- Development 2010-2022 only for choosing TOP1 vs TOP2 as the satellite.
- Holdout 2023-2026 is evaluation-only.
- No new predictive feature, ML model, payoff calibration, stop tuning or data imputation.
- Benchmark is fetched as adjusted daily CW8.PA OHLC from Yahoo Finance at run time.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_longitudinal_audit73 import load_governed_ohlcv
from v182.hebdo.tabport_publish import build_weekly_meta_signals

HOLDOUT_START = pd.Timestamp("2023-01-01", tz="UTC")
BENCHMARK_TICKER = "CW8.PA"
INITIAL = 65_000.0
CORE_WEIGHT = 0.75
SAT_WEIGHT = 0.25
FEE = 0.002
SLIPPAGE = 0.001


def top_n_per_signal_date(signals: pd.DataFrame, n: int) -> pd.DataFrame:
    if n < 1:
        raise ValueError("n must be >=1")
    s = signals.copy()
    s["date"] = pd.to_datetime(s["date"], utc=True)
    s = s.sort_values(["date", "EV_net", "ticker"], ascending=[True, False, True])
    return s.groupby("date", sort=False, group_keys=False).head(n).reset_index(drop=True)


def fetch_cw8() -> pd.DataFrame:
    x = yf.download(BENCHMARK_TICKER, start="2009-01-01", auto_adjust=True, progress=False, actions=False)
    if x is None or x.empty:
        raise RuntimeError("BLOCK_BENCHMARK_CW8_DOWNLOAD_EMPTY")
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(0)
    need = {"Open", "Close"}
    if not need.issubset(x.columns):
        raise RuntimeError(f"BLOCK_BENCHMARK_CW8_COLUMNS:{sorted(x.columns)}")
    out = x[["Open", "Close"]].reset_index().rename(columns={"Date":"date","Open":"open","Close":"close"})
    out["date"] = pd.to_datetime(out["date"], utc=True)
    out["open"] = pd.to_numeric(out["open"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna().sort_values("date")
    out = out[(out["open"] > 0) & (out["close"] > 0)].copy()
    if out["date"].min() > pd.Timestamp("2010-01-10", tz="UTC"):
        raise RuntimeError("BLOCK_BENCHMARK_CW8_HISTORY_TOO_SHORT")
    return out


def buyhold_equity(cw8: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp | None, capital: float) -> tuple[pd.DataFrame, dict]:
    x = cw8[cw8["date"] >= start].copy()
    if end is not None:
        x = x[x["date"] < end].copy()
    if len(x) < 2:
        raise RuntimeError("BLOCK_BENCHMARK_SEGMENT_TOO_SHORT")
    buy = float(x.iloc[0]["open"]) * (1 + SLIPPAGE)
    shares = int(capital // (buy * (1 + FEE)))
    if shares < 1:
        raise RuntimeError("BLOCK_BENCHMARK_INSUFFICIENT_CAPITAL")
    entry_gross = shares * buy
    entry_fee = entry_gross * FEE
    cash = capital - entry_gross - entry_fee
    eq = x[["date", "close"]].copy()
    eq["equity"] = cash + shares * eq["close"]
    raw_exit = float(x.iloc[-1]["close"])
    sell = raw_exit * (1 - SLIPPAGE)
    final = cash + shares * sell * (1 - FEE)
    eq.loc[eq.index[-1], "equity"] = final
    vals = eq["equity"].to_numpy(float)
    peak = np.maximum.accumulate(np.r_[capital, vals])[1:]
    dd = vals / peak - 1
    days = max(1, (eq["date"].max() - eq["date"].min()).days)
    metrics = {
        "initial_capital": capital,
        "final_value": final,
        "return_pct": final / capital - 1,
        "cagr": (final / capital) ** (365.25 / days) - 1,
        "max_drawdown": float(dd.min()),
        "trades": 1,
        "fees_eur": float(entry_fee + shares * sell * FEE),
        "benchmark_ticker": BENCHMARK_TICKER,
    }
    return eq[["date", "equity"]].reset_index(drop=True), metrics


def run_stock(signals: pd.DataFrame, ohlcv: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp | None, capital: float) -> tuple[pd.DataFrame, dict]:
    s = signals[signals["date"] >= start].copy()
    if end is not None:
        s = s[s["date"] < end].copy()
    tickers = set(s["ticker"].astype(str))
    p = ohlcv[ohlcv["ticker"].astype(str).isin(tickers)].copy()
    p = p[p["date"] >= start]
    if end is not None:
        p = p[p["date"] < end]
    cfg = TabportConfig(
        initial_cash=capital,
        max_positions=12,
        max_position_eur=4500.0 * capital / INITIAL,
        max_entries_month=5,
        max_entries_year=40,
        fee_rate=FEE,
        slippage_rate=SLIPPAGE,
        stop_pct=0.09,
        max_hold_sessions=126,
    )
    r = Tabport65k(cfg).run(s, p[["date","ticker","open","high","low","close"]])
    return r["equity"][["date","equity"]].copy(), r["metrics"]


def combine_sleeves(core: pd.DataFrame, sat: pd.DataFrame, core_cap: float, sat_cap: float) -> tuple[pd.DataFrame, dict]:
    dates = pd.DataFrame({"date": sorted(set(core["date"]) | set(sat["date"]))})
    c = dates.merge(core.rename(columns={"equity":"core"}), on="date", how="left")
    c = c.merge(sat.rename(columns={"equity":"sat"}), on="date", how="left")
    c["core"] = c["core"].ffill().fillna(core_cap)
    c["sat"] = c["sat"].ffill().fillna(sat_cap)
    c["equity"] = c["core"] + c["sat"]
    vals = c["equity"].to_numpy(float)
    peak = np.maximum.accumulate(np.r_[core_cap + sat_cap, vals])[1:]
    dd = vals / peak - 1
    days = max(1, (c["date"].max() - c["date"].min()).days)
    final = float(vals[-1])
    metrics = {
        "initial_capital": core_cap + sat_cap,
        "final_value": final,
        "return_pct": final / (core_cap + sat_cap) - 1,
        "cagr": (final / (core_cap + sat_cap)) ** (365.25 / days) - 1,
        "max_drawdown": float(dd.min()),
    }
    return c[["date","equity"]], metrics


def score_vs_benchmark(m: dict, b: dict) -> float:
    # Development-only selection objective: reward excess CAGR and shallower DD.
    return float((m["cagr"] - b["cagr"]) + 0.25 * (abs(b["max_drawdown"]) - abs(m["max_drawdown"])))


def decision(m: dict, b: dict) -> dict:
    excess = float(m["cagr"] - b["cagr"])
    dd_ratio = abs(m["max_drawdown"]) / max(abs(b["max_drawdown"]), 1e-12)
    qualifies = bool((excess >= 0.03 and dd_ratio <= 1.10) or (excess >= -0.005 and dd_ratio <= 0.75))
    return {"excess_cagr": excess, "drawdown_ratio": dd_ratio, "qualifies": qualifies}


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_governed_ohlcv(pre2023, manifest, holdout_cache)
    raw, baseline_audit = build_weekly_meta_signals(ohlcv)
    features = add_antifp_features(ohlcv.copy())
    confirmed, j1 = apply_j1_confirmation(raw, features)
    confirmed["date"] = pd.to_datetime(confirmed["date"], utc=True)
    variants = {
        "BASELINE": confirmed,
        "TOP1": top_n_per_signal_date(confirmed, 1),
        "TOP2": top_n_per_signal_date(confirmed, 2),
    }
    cw8 = fetch_cw8()
    segments = {
        "DEVELOPMENT_2010_2022": (pd.Timestamp("2010-01-01", tz="UTC"), HOLDOUT_START),
        "HOLDOUT_2023_2026": (HOLDOUT_START, None),
    }
    rows = []
    eq_store: dict[tuple[str,str], pd.DataFrame] = {}
    bench_store = {}
    for seg,(start,end) in segments.items():
        beq,bm = buyhold_equity(cw8,start,end,INITIAL)
        bench_store[seg]=(beq,bm)
        rows.append({**bm,"segment":seg,"model":"CW8_BUY_HOLD"})
        for name,sig in variants.items():
            eq,m = run_stock(sig,ohlcv,start,end,INITIAL)
            eq_store[(seg,name)] = eq
            rows.append({**m,"segment":seg,"model":name})

    comp = pd.DataFrame(rows)
    dev_b = comp[(comp.segment=="DEVELOPMENT_2010_2022") & (comp.model=="CW8_BUY_HOLD")].iloc[0].to_dict()
    dev_candidates=[]
    for name in ["TOP1","TOP2"]:
        m=comp[(comp.segment=="DEVELOPMENT_2010_2022") & (comp.model==name)].iloc[0].to_dict()
        dev_candidates.append((score_vs_benchmark(m,dev_b),name))
    selected_satellite=max(dev_candidates)[1]

    # Add fixed 75/25 core-satellite variants. Selection remains development-only.
    core_cap=INITIAL*CORE_WEIGHT; sat_cap=INITIAL*SAT_WEIGHT
    for seg,(start,end) in segments.items():
        core_eq,core_m=buyhold_equity(cw8,start,end,core_cap)
        for name in ["TOP1","TOP2"]:
            sat_eq,sat_m=run_stock(variants[name],ohlcv,start,end,sat_cap)
            mix_eq,mix_m=combine_sleeves(core_eq,sat_eq,core_cap,sat_cap)
            model=f"CORE75_CW8_SAT25_{name}"
            rows.append({**mix_m,"segment":seg,"model":model,"satellite_trades":sat_m.get("trades")})
            eq_store[(seg,model)] = mix_eq
    comp=pd.DataFrame(rows)

    selected_model=f"CORE75_CW8_SAT25_{selected_satellite}"
    decisions=[]
    for seg in segments:
        b=comp[(comp.segment==seg)&(comp.model=="CW8_BUY_HOLD")].iloc[0].to_dict()
        for model in ["BASELINE","TOP1","TOP2",f"CORE75_CW8_SAT25_TOP1",f"CORE75_CW8_SAT25_TOP2"]:
            m=comp[(comp.segment==seg)&(comp.model==model)].iloc[0].to_dict()
            decisions.append({"segment":seg,"model":model,**decision(m,b)})
    dec=pd.DataFrame(decisions)
    holdout_selected=dec[(dec.segment=="HOLDOUT_2023_2026")&(dec.model==selected_model)].iloc[0].to_dict()
    verdict="RETAIN_STOCK_PICKING_AS_SATELLITE" if bool(holdout_selected["qualifies"]) else "STOP_STOCK_PICKING_AS_PERFORMANCE_ENGINE"

    summary={
        "status":"SUCCESS",
        "version":"TABPORT_DECISIVE_BENCHMARK_V1",
        "production_promotion":False,
        "benchmark":BENCHMARK_TICKER,
        "governance":{
            "development":"2010_2022_SELECTION_ONLY",
            "holdout":"2023_2026_EVALUATION_ONLY",
            "holdout_used_for_tuning":False,
            "new_predictive_features":False,
            "new_ml":False,
            "new_stop_tuning":False,
            "core_weight":CORE_WEIGHT,
            "satellite_weight":SAT_WEIGHT,
            "selection_family":["TOP1","TOP2"],
            "qualification_rule":"excess CAGR >=3pp with DD <=110% benchmark OR CAGR within -0.5pp with DD <=75% benchmark",
        },
        "quality":quality,
        "baseline_audit":baseline_audit,
        "j1_rows":int(len(j1)),
        "confirmed_signals":int(len(confirmed)),
        "selected_satellite_on_development":selected_satellite,
        "selected_model":selected_model,
        "holdout_selected_decision":holdout_selected,
        "verdict":verdict,
    }
    comp.to_csv(output_dir/"TABPORT_DECISIVE_COMPARISON.csv",index=False)
    dec.to_csv(output_dir/"TABPORT_DECISIVE_DECISIONS.csv",index=False)
    cw8.to_csv(output_dir/"TABPORT_CW8_BENCHMARK_OHLC.csv",index=False)
    (output_dir/"TABPORT_DECISIVE_SUMMARY.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8")
    return summary


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--pre2023",type=Path,required=True)
    ap.add_argument("--manifest",type=Path,required=True)
    ap.add_argument("--holdout-cache",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    a=ap.parse_args()
    print(json.dumps(run(a.pre2023,a.manifest,a.holdout_cache,a.output_dir),indent=2,default=str))

if __name__=="__main__":
    main()
