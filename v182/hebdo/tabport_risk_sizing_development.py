"""Development-only risk-adaptive position sizing research for TABPORT.

No signal is removed. Ranking, stop (9%), holding horizon (126 sessions), entry
rules and exits stay unchanged. Only the cash budget assigned to each candidate
varies as a function of PIT `prob_stop_9`. Thresholds and candidate policies are
fit/selected on 2010-2022 only; 2023-2026 is evaluation-only.
"""
from __future__ import annotations

import argparse, json
from math import floor
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START

POLICIES = {
    "BASELINE_4500": "BASELINE",
    "HIGH_RISK_3750": "HIGH3750",
    "HIGH_RISK_3000": "HIGH3000",
    "THREE_TIER_4500_3750_3000": "THREE",
    "UPSIDE_5000_4500_3750": "UPSIDE",
}


def learn_thresholds(confirmed: pd.DataFrame) -> dict[str, float]:
    x=confirmed.copy(); x["date"]=pd.to_datetime(x["date"],utc=True,errors="coerce")
    dev=x[x["date"]<=DEV_END].copy()
    p=pd.to_numeric(dev["prob_stop_9"],errors="coerce")
    if dev.empty or p.isna().any() or not np.isfinite(p).all():
        raise ValueError("BLOCK_RISK_SIZING_INVALID_DEV_PROB_STOP")
    return {"q33":float(p.quantile(1/3)),"q60":float(p.quantile(.60)),"q67":float(p.quantile(2/3))}


def assign_budget(x: pd.DataFrame, policy: str, t: dict[str,float]) -> pd.DataFrame:
    out=x.copy(); p=pd.to_numeric(out["prob_stop_9"],errors="coerce")
    if p.isna().any() or not np.isfinite(p).all(): raise ValueError("BLOCK_RISK_SIZING_MISSING_PROB_STOP")
    if policy=="BASELINE": b=np.full(len(out),4500.0)
    elif policy=="HIGH3750": b=np.where(p>=t["q60"],3750.0,4500.0)
    elif policy=="HIGH3000": b=np.where(p>=t["q60"],3000.0,4500.0)
    elif policy=="THREE": b=np.where(p<t["q33"],4500.0,np.where(p<t["q67"],3750.0,3000.0))
    elif policy=="UPSIDE": b=np.where(p<t["q33"],5000.0,np.where(p<t["q67"],4500.0,3750.0))
    else: raise ValueError("BLOCK_RISK_SIZING_UNKNOWN_POLICY")
    out["position_budget_eur"]=b.astype(float)
    if (out["position_budget_eur"]<=0).any(): raise ValueError("BLOCK_RISK_SIZING_BAD_BUDGET")
    return out


class RiskSizedTabport65k:
    def __init__(self,cfg:TabportConfig|None=None): self.cfg=cfg or TabportConfig()

    def run(self,signals:pd.DataFrame,prices:pd.DataFrame)->dict:
        cfg=self.cfg
        need={"date","ticker","EV_net","position_budget_eur"}
        if need-set(signals.columns): raise ValueError("BLOCK_RISK_SIZING_SIGNAL_COLUMNS")
        s=signals.copy(); s["date"]=pd.to_datetime(s["date"],utc=True,errors="coerce"); s["ticker"]=s["ticker"].astype(str).str.upper().str.strip()
        for c in ["EV_net","position_budget_eur"]: s[c]=pd.to_numeric(s[c],errors="coerce")
        if s[list(need)].isna().any().any() or s.duplicated(["date","ticker"]).any(): raise ValueError("BLOCK_RISK_SIZING_BAD_SIGNALS")
        if "tier" in s.columns: s=s[s["tier"].isin(cfg.allowed_tiers)].copy()
        s=s[s["EV_net"]>=0].sort_values(["date","EV_net","ticker"],ascending=[True,False,True]).reset_index(drop=True)
        p=prices.copy(); p["date"]=pd.to_datetime(p["date"],utc=True,errors="coerce"); p["ticker"]=p["ticker"].astype(str).str.upper().str.strip()
        for c in ["open","high","low","close"]: p[c]=pd.to_numeric(p[c],errors="coerce")
        if p[["date","ticker","open","high","low","close"]].isna().any().any() or p.duplicated(["date","ticker"]).any(): raise ValueError("BLOCK_RISK_SIZING_BAD_PRICES")
        p=p.sort_values(["date","ticker"]).reset_index(drop=True)
        price_dates=p.groupby("ticker")["date"].apply(list).to_dict(); last_date=p.groupby("ticker")["date"].max().to_dict()
        scheduled={}
        for _,r in s.iterrows():
            nxt=next((d for d in price_dates.get(r["ticker"],[]) if d>r["date"]),None)
            if nxt is not None: scheduled.setdefault(nxt,[]).append(r.to_dict())
        bars={d:g.set_index("ticker") for d,g in p.groupby("date",sort=True)}
        cash=float(cfg.initial_cash); pos={}; ledger=[]; equity=[]; em={}; ey={}
        def close(ticker,date,reason,raw):
            nonlocal cash
            z=pos.pop(ticker); sell=float(raw)*(1-cfg.slippage_rate); gross=sell*z["shares"]; fee=gross*cfg.fee_rate; cash+=gross-fee
            pnl=(gross-fee)-z["cash_out"]
            ledger.append({"ticker":ticker,"signal_date":z["signal_date"],"entry_date":z["entry_date"],"exit_date":date,"shares":z["shares"],"entry_price":z["entry_price"],"exit_price":sell,"entry_fee":z["entry_fee"],"exit_fee":fee,"fees_total":z["entry_fee"]+fee,"slippage_rate_side":cfg.slippage_rate,"cash_invested":z["cash_out"],"pnl_net":pnl,"return_net":pnl/z["cash_out"],"exit_reason":reason,"sessions_held":z["sessions"],"mae":z["mae"],"mfe":z["mfe"],"EV_net_signal":z["EV_net"],"position_budget_eur_signal":z["budget"]})
        for date in sorted(bars):
            day=bars[date]
            for ticker in list(pos):
                if ticker not in day.index: continue
                bar=day.loc[ticker]; z=pos[ticker]; z["sessions"]+=1; z["last_close"]=float(bar["close"]); z["mae"]=min(z["mae"],float(bar["low"])/z["entry_price"]-1); z["mfe"]=max(z["mfe"],float(bar["high"])/z["entry_price"]-1)
                stop=z["entry_price"]*(1-cfg.stop_pct); op=float(bar["open"])
                if float(bar["low"])<=stop: close(ticker,date,"STOP_GAP_THROUGH" if op<stop else "STOP_-9%",op if op<stop else stop)
                elif z["sessions"]>=cfg.max_hold_sessions: close(ticker,date,"TIME_26W",float(bar["close"]))
                elif date==last_date[ticker]: close(ticker,date,"EOP_DATA_END",float(bar["close"]))
            for sig in sorted(scheduled.get(date,[]),key=lambda r:(-float(r["EV_net"]),str(r["ticker"]))):
                tkr=str(sig["ticker"]); ym=(date.year,date.month)
                if tkr in pos or tkr not in day.index or len(pos)>=cfg.max_positions or em.get(ym,0)>=cfg.max_entries_month or ey.get(date.year,0)>=cfg.max_entries_year: continue
                bar=day.loc[tkr]; buy=float(bar["open"])*(1+cfg.slippage_rate); budget=min(float(sig["position_budget_eur"]),cash); shares=floor(budget/(buy*(1+cfg.fee_rate)))
                if shares<1: continue
                gross=shares*buy; fee=gross*cfg.fee_rate; out=gross+fee
                if out>cash+1e-9: continue
                cash-=out; pos[tkr]={"signal_date":sig["date"],"entry_date":date,"shares":shares,"entry_price":buy,"entry_fee":fee,"cash_out":out,"EV_net":float(sig["EV_net"]),"budget":float(sig["position_budget_eur"]),"sessions":1,"mae":0.0,"mfe":0.0,"last_close":float(bar["close"])}
                em[ym]=em.get(ym,0)+1; ey[date.year]=ey.get(date.year,0)+1
                z=pos[tkr]; z["mae"]=min(0.0,float(bar["low"])/buy-1); z["mfe"]=max(0.0,float(bar["high"])/buy-1); stop=buy*(1-cfg.stop_pct); op=float(bar["open"])
                if float(bar["low"])<=stop: close(tkr,date,"STOP_GAP_THROUGH" if op<stop else "STOP_-9%",op if op<stop else stop)
                elif date==last_date[tkr]: close(tkr,date,"EOP_DATA_END",float(bar["close"]))
            mv=sum(z["shares"]*z["last_close"] for z in pos.values()); equity.append({"date":date,"cash":cash,"market_value":mv,"equity":cash+mv,"open_positions":len(pos)})
        if pos: raise ValueError("BLOCK_RISK_SIZING_UNCLOSED")
        return {"ledger":pd.DataFrame(ledger),"equity":pd.DataFrame(equity)}


def _year_number(s:pd.Series)->pd.Series: return pd.to_numeric(s.astype(str).str.extract(r"(\d{4})",expand=False),errors="coerce")
def objective(yearly:pd.DataFrame,summary:dict)->float:
    yrs=_year_number(yearly["periode"]); y=yearly[(yrs>=2011)&(yrs<=2022)]; r=pd.to_numeric(y["rendement_portefeuille_pct"],errors="coerce").dropna()
    if r.empty:return -1e9
    pf=float(summary.get("profit_factor",np.nan)); rr=float(summary.get("rr_payoff",np.nan)); dd=abs(float(summary.get("drawdown_max_pct",np.nan)))
    if not all(np.isfinite([pf,rr,dd])): return -1e9
    return float(r.median()-.35*r.std(ddof=0)+.20*(r>0).mean()*100+.35*pf+.20*rr-.25*dd)


def run(pre2023:Path,manifest:Path,holdout_cache:Path,output_dir:Path)->dict:
    output_dir.mkdir(parents=True,exist_ok=True); ohlcv,quality=load_ohlcv(pre2023,manifest,holdout_cache)
    signals,signal_audit=build_weekly_meta_signals(ohlcv); features=add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy()); confirmed,audit=apply_j1_confirmation(signals,features); confirmed=confirmed.reset_index(drop=True)
    thresholds=learn_thresholds(confirmed); prices=ohlcv[["date","ticker","open","high","low","close"]].copy(); cfg=TabportConfig()
    rows=[]; years=[]; ledgers=[]; scores={}
    for model,policy in POLICIES.items():
        chosen=assign_budget(confirmed,policy,thresholds); res=RiskSizedTabport65k(cfg).run(chosen,prices); ledger=res["ledger"].copy(); nav=res["equity"].copy(); ledger["model"]=model; ledgers.append(ledger)
        ledger["signal_date"]=pd.to_datetime(ledger["signal_date"],utc=True,errors="coerce"); nav["date"]=pd.to_datetime(nav["date"],utc=True,errors="coerce")
        y=period_table(ledger,nav,"Y"); y.insert(0,"model",model); years.append(y)
        devl=ledger[ledger["signal_date"]<=DEV_END]; devn=nav[nav["date"]<=DEV_END]; sm=overall_summary(devl,devn,initial_cash=cfg.initial_cash); scores[model]=objective(y,sm)
        for seg,lo,hi in [("DEVELOPMENT_2010_2022",pd.Timestamp("2010-01-01",tz="UTC"),DEV_END),("HOLDOUT_2023_2026",HOLDOUT_START,pd.Timestamp("2100-01-01",tz="UTC"))]:
            ls=ledger[(ledger["signal_date"]>=lo)&(ledger["signal_date"]<=hi)]; ns=nav[(nav["date"]>=lo)&(nav["date"]<=hi)]; rows.append({"model":model,"segment":seg,**overall_summary(ls,ns,initial_cash=cfg.initial_cash)})
    selected=max(scores,key=scores.get); pd.DataFrame(rows).to_csv(output_dir/"TABPORT_RISK_SIZING_SEGMENTS.csv",index=False); pd.concat(years,ignore_index=True).to_csv(output_dir/"TABPORT_RISK_SIZING_YEARLY.csv",index=False); pd.concat(ledgers,ignore_index=True).to_csv(output_dir/"TABPORT_RISK_SIZING_LEDGERS.csv",index=False)
    payload={"status":"SUCCESS","version":"TABPORT_RISK_SIZING_DEV_ONLY_V1","selected_on_development_only":selected,"development_objective":scores,"thresholds":thresholds,"policies":POLICIES,"governance":{"fit_window":"2010-2022_ONLY","holdout":"2023-2026_EVALUATION_ONLY","holdout_used_for_threshold_or_policy_selection":False,"same_signal_universe":True,"same_ranking":True,"same_stop_pct":cfg.stop_pct,"same_hold_horizon_sessions":cfg.max_hold_sessions,"no_signal_filtering":True,"only_parameter_changed":"position_budget_eur_by_PIT_prob_stop_9","production_promotion":False,"synthetic_imputation":False},"quality":quality,"signal_audit":signal_audit}
    (output_dir/"TABPORT_RISK_SIZING_SUMMARY.json").write_text(json.dumps(payload,indent=2,default=str),encoding="utf-8"); return payload

def main():
    p=argparse.ArgumentParser(); p.add_argument("--pre2023",required=True); p.add_argument("--manifest",required=True); p.add_argument("--holdout-cache",required=True); p.add_argument("--output-dir",required=True); a=p.parse_args(); print(json.dumps(run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir)),indent=2,default=str))
if __name__=="__main__": main()
