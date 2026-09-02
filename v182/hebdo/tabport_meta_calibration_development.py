"""Strict development-only META calibration challenger for TABPORT.

Purpose: test whether the current score degeneracy is caused by the untrained
MetaLabeler. Production code is not changed here.

Governance:
- labels come only from real 126-session OHLC paths;
- a label is trainable only after its outcome is actually known;
- development scoring is yearly walk-forward (model trained on outcomes known
  before Jan-01 of the scored year);
- holdout 2023-2026 uses one model frozen at 2023-01-01;
- 2023-2026 never changes features, thresholds or model selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.backtests.v21_8_1_backtest_B_v2 import compute_true_26w_pnl
from v182.hebdo.expected_value_ranker import ExpectedValueRanker
from v182.hebdo.false_positive_filter import FalsePositiveFilter
from v182.hebdo.mae_predictor import MAEPredictor
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import _indicators_one, build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START

HORIZON = 126
FREEZE = pd.Timestamp("2023-01-01", tz="UTC")


def _weekly_rsi14_for_group(g: pd.DataFrame) -> pd.DataFrame:
    x=g.sort_values("date").copy()
    x["week"]=pd.to_datetime(x["date"],utc=True).dt.tz_localize(None).dt.to_period("W-FRI").astype(str)
    w=x.groupby("week",as_index=False).tail(1)[["week","close"]].copy()
    delta=pd.to_numeric(w["close"],errors="coerce").diff()
    gain=delta.clip(lower=0.0); loss=(-delta.clip(upper=0.0))
    avg_gain=gain.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    avg_loss=loss.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=avg_gain/avg_loss.replace(0,np.nan)
    w["rsi_14_hebdo"]=100.0-(100.0/(1.0+rs))
    # Flat/no-loss windows are valid high RSI rather than missing.
    w.loc[avg_loss.eq(0) & avg_gain.gt(0),"rsi_14_hebdo"]=100.0
    return w[["week","rsi_14_hebdo"]]


def build_pre_meta_candidates(ohlcv: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    parts=[]
    for ticker,g in ohlcv.groupby("ticker",sort=False):
        t=_indicators_one(g)
        t["week"]=pd.to_datetime(t["date"],utc=True).dt.tz_localize(None).dt.to_period("W-FRI").astype(str)
        r=_weekly_rsi14_for_group(g)
        t=t.merge(r,on="week",how="left",validate="many_to_one")
        parts.append(t)
    technical=pd.concat(parts,ignore_index=True).sort_values(["date","ticker"]).reset_index(drop=True)
    b=technical.loc[technical["B_signal"],["week","ticker","B_signal_type"]].sort_values(["week","ticker"]).drop_duplicates(["week","ticker"],keep="last")
    week_end=technical.sort_values("date").groupby(["week","ticker"],as_index=False).tail(1)
    c=week_end.merge(b,on=["week","ticker"],how="inner",suffixes=("","_trigger"))
    unique_dates=pd.Index(sorted(technical["date"].unique()))
    if len(unique_dates)<=HORIZON: raise ValueError("BLOCK_META_CAL_INSUFFICIENT_HISTORY")
    cutoff=pd.Timestamp(unique_dates[-(HORIZON+1)])
    c=c[c["date"]<=cutoff].copy()
    market_date=pd.to_datetime(c["date"],utc=True)
    c["market_snapshot_date"]=market_date
    c["date"]=market_date+pd.Timedelta(days=1)
    c["mom_26w_sector"]=0.0
    c["sector_momentum_status"]="UNAVAILABLE_CONSERVATIVE_ZERO"
    c["signal_family"]=c.get("B_signal_type_trigger",c.get("B_signal_type","B"))
    need=["close","sma200","vol_z","drawdown_4w","atr_14_pct","adv_20m_eur","rsi_14_hebdo"]
    c=c.dropna(subset=need).copy()
    c=FalsePositiveFilter().filter_batch(c)
    if c.empty: raise ValueError("BLOCK_META_CAL_NO_POST_FP_CANDIDATES")
    c=MAEPredictor().predict_batch(c)
    c["close_vs_sma200"]=(pd.to_numeric(c["close"])<pd.to_numeric(c["sma200"])).astype(int)
    return c.reset_index(drop=True),{"mature_market_cutoff":str(cutoff),"post_fp_candidates":int(len(c))}


def attach_real_outcomes(candidates: pd.DataFrame, ohlcv: pd.DataFrame) -> pd.DataFrame:
    groups={}
    for ticker,g in ohlcv.groupby("ticker",sort=False):
        z=g.sort_values("date").reset_index(drop=True).copy(); z["date"]=pd.to_datetime(z["date"],utc=True)
        groups[str(ticker)]=z
    rows=[]
    for _,r in candidates.iterrows():
        ticker=str(r["ticker"]); z=groups.get(ticker)
        if z is None: continue
        market=pd.Timestamp(r["market_snapshot_date"])
        dates=z["date"].to_numpy(dtype="datetime64[ns]")
        pos=int(np.searchsorted(dates,market.to_datetime64(),side="right"))
        path=z.iloc[pos:pos+HORIZON].copy()
        res=compute_true_26w_pnl(float(r["close"]),path,0.09,expected_days=HORIZON)
        if res.get("block_reason") is not None: continue
        day=int(res["day_stop"])
        available=pd.Timestamp(path.iloc[day-1]["date"])
        row=r.to_dict(); row.update({"mfe":res["mfe"],"mae":res["mae"],"hit_stop":res["hit_stop"],"outcome_available_at":available,"block_reason":None})
        rows.append(row)
    out=pd.DataFrame(rows)
    if out.empty: raise ValueError("BLOCK_META_CAL_NO_COMPLETE_OUTCOMES")
    out=MetaLabeler().build_meta_label(out)
    out["outcome_available_at"]=pd.to_datetime(out["outcome_available_at"],utc=True)
    out["date"]=pd.to_datetime(out["date"],utc=True)
    return out


def _train_before(labeled: pd.DataFrame, anchor: pd.Timestamp) -> tuple[MetaLabeler,dict]:
    train=labeled[labeled["outcome_available_at"]<anchor].copy()
    model=MetaLabeler()
    if train.empty:
        return model,{"status":"UNTRAINED_NO_PRIOR_OUTCOMES","n":0}
    info=model.train(train)
    return model,info


def score_walkforward(candidates: pd.DataFrame, labeled: pd.DataFrame) -> tuple[pd.DataFrame,list[dict]]:
    x=candidates.copy(); x["date"]=pd.to_datetime(x["date"],utc=True)
    parts=[]; audit=[]
    ranker=ExpectedValueRanker()
    years=sorted(x["date"].dt.year.unique())
    for year in years:
        grp=x[x["date"].dt.year==year].copy()
        if grp.empty: continue
        anchor=FREEZE if year>=2023 else pd.Timestamp(f"{year}-01-01",tz="UTC")
        model,info=_train_before(labeled,anchor)
        # Holdout always reuses exactly the same model trained before 2023.
        scored=model.predict_proba(grp)
        ranked=[]
        for decision,g in scored.groupby("date",sort=True):
            q=ranker.rank_batch(g.copy()); q["date"]=decision; ranked.append(q)
        out=pd.concat(ranked,ignore_index=True)
        out=out[out["tier"].isin(["TCT","CT_WATCH"]) & (pd.to_numeric(out["EV_net"],errors="coerce")>=0)].copy()
        parts.append(out)
        audit.append({"year":int(year),"anchor":str(anchor),"training_status":str(info.get("status")),"training_n":int(info.get("n",0)),"scored_candidates":int(len(grp)),"eligible_signals":int(len(out)),"prob_meta_unique":int(pd.to_numeric(out.get("prob_meta"),errors="coerce").nunique()) if len(out) else 0,"ev_unique":int(pd.to_numeric(out.get("EV_net"),errors="coerce").nunique()) if len(out) else 0})
    if not parts: raise ValueError("BLOCK_META_CAL_NO_SCORED_SIGNALS")
    return pd.concat(parts,ignore_index=True).sort_values(["date","EV_net","ticker"],ascending=[True,False,True]).reset_index(drop=True),audit


def _objective(yearly: pd.DataFrame) -> float:
    yrs=pd.to_numeric(yearly["periode"].astype(str).str.extract(r"(\d{4})",expand=False),errors="coerce")
    y=yearly[(yrs>=2011)&(yrs<=2022)].copy(); r=pd.to_numeric(y["rendement_portefeuille_pct"],errors="coerce").dropna()
    if r.empty:return -1e9
    return float(r.median()-.35*r.std(ddof=0)+.20*(r>0).mean()*100)


def _run_portfolio(signals:pd.DataFrame,ohlcv:pd.DataFrame,model:str)->tuple[dict,pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    features=add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed,audit=apply_j1_confirmation(signals,features)
    prices=ohlcv[["date","ticker","open","high","low","close"]].copy(); result=Tabport65k(TabportConfig()).run(confirmed,prices)
    ledger=result["ledger"].copy(); nav=result["equity"].copy(); ledger["model"]=model
    ledger["signal_date"]=pd.to_datetime(ledger["signal_date"],utc=True); nav["date"]=pd.to_datetime(nav["date"],utc=True)
    y=period_table(ledger,nav,"Y"); y.insert(0,"model",model)
    return result,ledger,nav,y


def run(pre2023:Path,manifest:Path,holdout_cache:Path,output_dir:Path)->dict:
    output_dir.mkdir(parents=True,exist_ok=True)
    ohlcv,quality=load_ohlcv(pre2023,manifest,holdout_cache)
    baseline,baseline_signal_audit=build_weekly_meta_signals(ohlcv)
    pre,audit0=build_pre_meta_candidates(ohlcv)
    labeled=attach_real_outcomes(pre,ohlcv)
    calibrated,training_audit=score_walkforward(pre,labeled)
    rows=[]; years=[]; ledgers=[]; dev_scores={}
    for model,signals in [("BASELINE_UNTRAINED_META",baseline),("CALIBRATED_META_WALKFORWARD",calibrated)]:
        result,ledger,nav,y=_run_portfolio(signals,ohlcv,model); ledgers.append(ledger); years.append(y); dev_scores[model]=_objective(y)
        for seg,lo,hi in [("DEVELOPMENT_2010_2022",pd.Timestamp("2010-01-01",tz="UTC"),DEV_END),("HOLDOUT_2023_2026",HOLDOUT_START,pd.Timestamp("2100-01-01",tz="UTC"))]:
            ls=ledger[(ledger["signal_date"]>=lo)&(ledger["signal_date"]<=hi)]; ns=nav[(nav["date"]>=lo)&(nav["date"]<=hi)]
            rows.append({"model":model,"segment":seg,"signals_pre_j1":int(((pd.to_datetime(signals["date"],utc=True)>=lo)&(pd.to_datetime(signals["date"],utc=True)<=hi)).sum()),**overall_summary(ls,ns,initial_cash=65000.0)})
    selected=max(dev_scores,key=dev_scores.get)
    pd.DataFrame(rows).to_csv(output_dir/"TABPORT_META_CAL_SEGMENTS.csv",index=False)
    pd.concat(years,ignore_index=True).to_csv(output_dir/"TABPORT_META_CAL_YEARLY.csv",index=False)
    pd.concat(ledgers,ignore_index=True).to_csv(output_dir/"TABPORT_META_CAL_LEDGERS.csv",index=False)
    calibrated.to_csv(output_dir/"TABPORT_META_CAL_SIGNALS.csv",index=False); labeled.to_csv(output_dir/"TABPORT_META_CAL_LABELS.csv",index=False); pd.DataFrame(training_audit).to_csv(output_dir/"TABPORT_META_CAL_TRAINING_AUDIT.csv",index=False)
    dev_base=baseline[pd.to_datetime(baseline["date"],utc=True)<=DEV_END]
    payload={"status":"SUCCESS","version":"TABPORT_META_CALIBRATION_WALKFORWARD_V1","selected_on_development_only":selected,"development_objective":dev_scores,"degeneracy_baseline":{"development_rows":int(len(dev_base)),"prob_meta_unique":int(pd.to_numeric(dev_base["prob_meta"],errors="coerce").nunique()),"prob_meta_half_pct":float(np.isclose(pd.to_numeric(dev_base["prob_meta"]),.5).mean()*100),"ev_0044_pct":float(np.isclose(pd.to_numeric(dev_base["EV_net"]),.044,atol=1e-12).mean()*100),"meta_status_counts":{str(k):int(v) for k,v in dev_base["meta_model_status"].value_counts(dropna=False).to_dict().items()}},"governance":{"fit_labels":"REAL_126_SESSION_OHLC_ONLY","development_scoring":"YEARLY_WALK_FORWARD_OUTCOMES_AVAILABLE_BEFORE_YEAR_START","holdout_model_freeze":"2023-01-01","holdout_used_for_training_or_selection":False,"meta_feature_family_existing":True,"production_code_changed":False,"production_promotion":False,"mae_model_unchanged":"HEURISTIC_UNCALIBRATED","ev_ranker_unchanged":"PARAMETRIC_UNCALIBRATED"},"candidate_audit":audit0,"baseline_signal_audit":baseline_signal_audit,"training_audit":training_audit,"quality":quality}
    (output_dir/"TABPORT_META_CAL_SUMMARY.json").write_text(json.dumps(payload,indent=2,default=str),encoding="utf-8")
    return payload


def main():
    p=argparse.ArgumentParser(); p.add_argument("--pre2023",required=True); p.add_argument("--manifest",required=True); p.add_argument("--holdout-cache",required=True); p.add_argument("--output-dir",required=True); a=p.parse_args(); print(json.dumps(run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir)),indent=2,default=str))
if __name__=="__main__": main()
