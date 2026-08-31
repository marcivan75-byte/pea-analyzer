from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from v182.backtest.v21_8_1_backtest_B_v2 import compute_mae_mfe, compute_true_26w_pnl
from v182.hebdo.hebdo_at_chat_v22_1 import adaptive_atr_stop_pct
from v182.hebdo.mae_predictor import apply_mae_filter, train_stop_model
from v182.scoring.ic_lasso_selector import (
    build_governed_weights,
    compute_information_coefficient,
    lasso_select_features,
)

HORIZON_DAYS = {"1w": 5, "2w": 10, "4w": 20, "13w": 63, "26w": 126}
ENHANCED_FEATURE_COLUMNS = ("vol_z", "mom_26w_sector", "rsi_14_hebdo", "drawdown_4w", "atr_14_pct")
TECHNICAL_CORE_FEATURE_COLUMNS = ("vol_z", "mom_26w", "rsi_14_hebdo", "drawdown_4w")
DEFAULT_FEATURE_COLUMNS = ENHANCED_FEATURE_COLUMNS

class HistoricalPITUnavailable(RuntimeError):
    pass

def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet": return pd.read_parquet(path)
    if suffix == ".csv": return pd.read_csv(path)
    raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: unsupported historical input format {suffix}")

def _validate_inputs(features: pd.DataFrame, ohlcv: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required_features={"ticker","as_of_date"}; required_ohlcv={"ticker","date","open","low","high","close"}
    miss_f=required_features.difference(features.columns); miss_o=required_ohlcv.difference(ohlcv.columns)
    if miss_f: raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: feature columns missing {sorted(miss_f)}")
    if miss_o: raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: OHLCV columns missing {sorted(miss_o)}")
    f=features.copy(); o=ohlcv.copy(); f["as_of_date"]=pd.to_datetime(f["as_of_date"],errors="coerce"); o["date"]=pd.to_datetime(o["date"],errors="coerce")
    f=f.dropna(subset=["ticker","as_of_date"]); o=o.dropna(subset=["ticker","date"]).sort_values(["ticker","date"])
    if f.empty or o.empty: raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: empty historical PIT features or OHLCV")
    ts_col="pit_observed_at" if "pit_observed_at" in f.columns else "_pit_observed_at_utc" if "_pit_observed_at_utc" in f.columns else None
    if ts_col is None: raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: historical features lack PIT observation timestamp")
    observed=pd.to_datetime(f[ts_col],errors="coerce",utc=True); asof_utc=pd.to_datetime(f["as_of_date"],errors="coerce",utc=True)
    if observed.isna().any() or bool((observed>asof_utc).any()): raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: future/invalid PIT feature timestamp")
    return f,o

def _stop_for_row(row: pd.Series,fixed_stop_pct:float,stop_policy:str)->float:
    if stop_policy=="fixed": return float(fixed_stop_pct)
    if stop_policy=="atr":
        if "atr_14_pct" not in row.index: raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: atr_14_pct missing for ATR stop")
        try: return adaptive_atr_stop_pct(float(row["atr_14_pct"]))
        except (TypeError,ValueError) as exc: raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: invalid atr_14_pct for ATR stop") from exc
    raise ValueError(f"unsupported stop_policy={stop_policy}")

def add_true_forward_returns(features,ohlcv,*,stop_pct=0.09,stop_policy="fixed"):
    f,o=_validate_inputs(features,ohlcv); output=[]; grouped={str(t):g.set_index("date").sort_index() for t,g in o.groupby("ticker",sort=False)}
    for _,row in f.iterrows():
        ticker=str(row["ticker"]); hist=grouped.get(ticker)
        if hist is None or hist.empty: continue
        decision_dates=hist.index[hist.index<=row["as_of_date"]]
        if len(decision_dates)==0: continue
        signal_market_date=decision_dates[-1]; loc=hist.index.get_loc(signal_market_date)
        if not isinstance(loc,(int,np.integer)): continue
        entry_loc=int(loc)+1
        if entry_loc>=len(hist): continue
        entry_price=pd.to_numeric(pd.Series([hist.iloc[entry_loc]["open"]]),errors="coerce").iloc[0]
        if not np.isfinite(entry_price) or entry_price<=0: continue
        row_stop=_stop_for_row(row,stop_pct,stop_policy); record=row.to_dict(); record.update(signal_market_date=signal_market_date,entry_date=hist.index[entry_loc],entry_price=float(entry_price),execution_policy="NEXT_SESSION_OPEN_J1",stop_policy=stop_policy,stop_pct_used=row_stop)
        for horizon,days in HORIZON_DAYS.items():
            forward=hist.iloc[entry_loc:entry_loc+days]
            if len(forward)<days: record[f"forward_ret_true_{horizon}"]=np.nan; continue
            pnl,_,_,_=compute_true_26w_pnl(float(entry_price),forward,stop_pct=row_stop); record[f"forward_ret_true_{horizon}"]=pnl
        full=hist.iloc[entry_loc:entry_loc+HORIZON_DAYS["26w"]]
        if len(full)==HORIZON_DAYS["26w"]:
            pnl26,hit,day_stop,_=compute_true_26w_pnl(float(entry_price),full,stop_pct=row_stop); mae,mfe=compute_mae_mfe(float(entry_price),full)
            record.update(forward_ret_true_26w=pnl26,label_end_date_26w=full.index[-1],hit_stop=bool(hit),day_stop=day_stop,mae=mae,mfe=mfe)
        else: record.update(label_end_date_26w=pd.NaT,hit_stop=pd.NA,day_stop=pd.NA,mae=np.nan,mfe=np.nan)
        output.append(record)
    result=pd.DataFrame(output)
    if result.empty: raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: no alignable historical observations")
    return result

def train_governed_model(ledger:pd.DataFrame,feature_columns:tuple[str,...]=DEFAULT_FEATURE_COLUMNS):
    missing=sorted(set(feature_columns).difference(ledger.columns))
    if missing: raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: model features missing {missing}")
    y=pd.to_numeric(ledger["forward_ret_true_26w"],errors="coerce"); X=ledger.loc[:,feature_columns].apply(pd.to_numeric,errors="coerce")
    usable=pd.concat([X,y.rename("y")],axis=1).dropna()
    if len(usable)<100: raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: only {len(usable)} complete rows for Lasso")
    ic=compute_information_coefficient(usable[list(feature_columns)],usable["y"])
    selected,alpha,lasso=lasso_select_features(usable[list(feature_columns)],usable["y"])
    weights=build_governed_weights(selected)
    if not weights:
        coefs={name:float(coef) for name,coef in zip(feature_columns,lasso.coef_,strict=True)}
        ic_rows={str(r["feature"]):{"ic":None if pd.isna(r["IC"]) else float(r["IC"]),"p_value":None if pd.isna(r["p_value"]) else float(r["p_value"]),"n":int(r["n"])} for r in ic.to_dict("records")}
        corr=usable[list(feature_columns)].corr(method="spearman").round(6).to_dict()
        diag={"n_complete":int(len(usable)),"y_mean":float(usable["y"].mean()),"y_std":float(usable["y"].std()),"lasso_alpha":float(alpha),"min_abs_coef":1e-4,"lasso_coefficients_standardized":coefs,"nonzero_coefficients":int(np.count_nonzero(lasso.coef_)),"max_abs_coefficient":float(np.max(np.abs(lasso.coef_))),"ic":ic_rows,"feature_spearman_corr":corr}
        raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: Lasso selected no governed features | AUDIT="+json.dumps(diag,sort_keys=True,separators=(",",":")))
    meta={"n_complete":int(len(usable)),"lasso_alpha":alpha,"features":list(feature_columns)}
    return ic,weights,meta

def _score_with_frozen_weights(frame,weights):
    score=pd.Series(0.0,index=frame.index,dtype=float); valid=pd.Series(True,index=frame.index)
    for feature,spec in weights.items():
        if feature not in frame.columns: valid[:]=False; continue
        values=pd.to_numeric(frame[feature],errors="coerce")
        try: mean_=float(spec["training_mean"]); scale_=float(spec["training_scale"]); weight=float(spec["weight"])
        except (KeyError,TypeError,ValueError): valid[:]=False; continue
        observed=values.notna()&np.isfinite(values)&np.isfinite(mean_)&np.isfinite(scale_)&(scale_>0); valid&=observed
        direction=-1.0 if str(spec.get("direction","LONG")).upper()=="SHORT" else 1.0; score=score+direction*weight*((values-mean_)/scale_)
    score.loc[~valid]=np.nan; return score

def _oos_ic(frame,score_col="governed_score"):
    out={}; score=pd.to_numeric(frame[score_col],errors="coerce")
    for horizon in ("1w","4w","26w"):
        ret=pd.to_numeric(frame.get(f"forward_ret_true_{horizon}"),errors="coerce"); valid=score.notna()&ret.notna(); n=int(valid.sum()); out[f"ic_{horizon}"]=float(spearmanr(score[valid],ret[valid]).statistic) if n>=30 else None; out[f"ic_{horizon}_n"]=n
    return out

def _mean_stop(frame):
    if "stop_pct_used" not in frame.columns:return None
    v=pd.to_numeric(frame["stop_pct_used"],errors="coerce").dropna(); return float(v.mean()) if not v.empty else None

def acceptance_metrics(ledger):
    ret26=pd.to_numeric(ledger.get("forward_ret_true_26w"),errors="coerce").dropna(); mae=pd.to_numeric(ledger.get("mae"),errors="coerce").dropna(); mfe=pd.to_numeric(ledger.get("mfe"),errors="coerce").dropna(); stops=ledger.get("hit_stop"); stop_rate=None; stop_count=0
    if stops is not None:
        s=stops.dropna().astype(bool); stop_count=int(s.sum()); stop_rate=float(s.mean()) if not s.empty else None
    wins=ret26[ret26>0]; losses=ret26[ret26<=0]; gp=float(wins.sum()) if not wins.empty else 0.; gl=float((-losses).sum()) if not losses.empty else 0.; aw=float(wins.mean()) if not wins.empty else None; al=float(losses.mean()) if not losses.empty else None
    return {"rows":int(len(ledger)),"completed_26w":int(len(ret26)),"wins":int((ret26>0).sum()),"losses_or_flat":int((ret26<=0).sum()),"hit_rate_26w_true":float((ret26>0).mean()) if not ret26.empty else None,"avg_win_26w_true":aw,"avg_loss_26w_true":al,"expectancy_26w_true":float(ret26.mean()) if not ret26.empty else None,"profit_factor_26w_true":gp/gl if gl>0 else None,"payoff_ratio_26w_true":aw/abs(al) if aw is not None and al not in (None,0.) else None,"mae_mean":float(mae.mean()) if not mae.empty else None,"mfe_mean":float(mfe.mean()) if not mfe.empty else None,"stop_count":stop_count,"stop_rate":stop_rate,"mean_stop_pct":_mean_stop(ledger)}

def _period_metrics(frame,period_col):
    rows=[]
    for period,g in frame.groupby(period_col,sort=True): rows.append({"period":str(period),**acceptance_metrics(g)})
    return pd.DataFrame(rows)

def build_period_diagnostics(frame):
    x=frame.copy(); x["as_of_date"]=pd.to_datetime(x["as_of_date"],errors="coerce"); x=x.dropna(subset=["as_of_date"]); x["year"]=x["as_of_date"].dt.year.astype("Int64"); x["quarter"]=x["as_of_date"].dt.to_period("Q").astype(str); return _period_metrics(x,"year"),_period_metrics(x,"quarter")

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--features",type=Path,required=True); p.add_argument("--ohlcv",type=Path,required=True); p.add_argument("--out-dir",type=Path,default=Path("outputs/hebdo/backtest_v22_1")); p.add_argument("--start",default="2010-01-01"); p.add_argument("--end",default="2026-08-31"); p.add_argument("--holdout-start",default="2023-01-01"); p.add_argument("--feature-set",choices=("enhanced","technical-core"),default="technical-core"); p.add_argument("--stop-policy",choices=("fixed","atr"),default="fixed"); p.add_argument("--fixed-stop-pct",type=float,default=0.09); args=p.parse_args(argv)
    try:
        features=_read_frame(args.features); ohlcv=_read_frame(args.ohlcv); features["as_of_date"]=pd.to_datetime(features["as_of_date"],errors="coerce"); features=features[(features["as_of_date"]>=pd.Timestamp(args.start))&(features["as_of_date"]<=pd.Timestamp(args.end))].copy(); feature_columns=TECHNICAL_CORE_FEATURE_COLUMNS if args.feature_set=="technical-core" else ENHANCED_FEATURE_COLUMNS
        ledger=add_true_forward_returns(features,ohlcv,stop_pct=args.fixed_stop_pct,stop_policy=args.stop_policy); ledger["label_end_date_26w"]=pd.to_datetime(ledger["label_end_date_26w"],errors="coerce"); holdout_start=pd.Timestamp(args.holdout_start); train=ledger[ledger["label_end_date_26w"].notna()&(ledger["label_end_date_26w"]<holdout_start)].copy(); embargo=ledger[(ledger["as_of_date"]<holdout_start)&(ledger["label_end_date_26w"].notna())&(ledger["label_end_date_26w"]>=holdout_start)].copy(); holdout=ledger[ledger["as_of_date"]>=holdout_start].copy(); ic,weights,model_meta=train_governed_model(train,feature_columns); train["governed_score"]=_score_with_frozen_weights(train,weights); holdout["governed_score"]=_score_with_frozen_weights(holdout,weights); mae_model=train_stop_model(train); holdout_mae=apply_mae_filter(holdout,trained_artifact=mae_model,require_trained=True); holdout_mae_ok=holdout_mae[holdout_mae["mae_status"].eq("OK")].copy(); annual_full,quarterly_full=build_period_diagnostics(holdout); annual_mae,quarterly_mae=build_period_diagnostics(holdout_mae_ok); annual_full["variant"]="FULL"; quarterly_full["variant"]="FULL"; annual_mae["variant"]="MAE_FILTER"; quarterly_mae["variant"]="MAE_FILTER"; annual=pd.concat([annual_full,annual_mae],ignore_index=True); quarterly=pd.concat([quarterly_full,quarterly_mae],ignore_index=True)
        args.out_dir.mkdir(parents=True,exist_ok=True); ledger.to_csv(args.out_dir/"V22_1_FORWARD_LEDGER.csv",index=False); train.to_csv(args.out_dir/"V22_1_TRAIN.csv",index=False); embargo.to_csv(args.out_dir/"V22_1_EMBARGO.csv",index=False); holdout.to_csv(args.out_dir/"V22_1_HOLDOUT.csv",index=False); holdout_mae.to_csv(args.out_dir/"V22_1_HOLDOUT_MAE_FILTER.csv",index=False); ic.to_csv(args.out_dir/"V22_1_IC_TRAIN.csv",index=False); pd.DataFrame([{"feature":k,**v} for k,v in weights.items()]).to_csv(args.out_dir/"V22_1_GOVERNED_WEIGHTS.csv",index=False); annual.to_csv(args.out_dir/"V22_1_ANNUAL_METRICS.csv",index=False); quarterly.to_csv(args.out_dir/"V22_1_QUARTERLY_METRICS.csv",index=False)
        report={"version":"V22.1","feature_set":args.feature_set,"feature_columns":list(feature_columns),"stop_policy":args.stop_policy,"fixed_stop_pct":args.fixed_stop_pct if args.stop_policy=="fixed" else None,"train_start":str(train["as_of_date"].min()),"train_last_signal":str(train["as_of_date"].max()),"train_last_label_end":str(train["label_end_date_26w"].max()),"holdout_start":str(holdout_start.date()),"holdout_end":args.end,"embargo_rows":int(len(embargo)),"model":model_meta,"mae_model":mae_model,"train":acceptance_metrics(train),"holdout_full":acceptance_metrics(holdout),"holdout_mae_filter":acceptance_metrics(holdout_mae_ok),"oos_ic":_oos_ic(holdout),"governance":{"historical_features":"TECHNICAL_PIT_ONLY" if args.feature_set=="technical-core" else "ENHANCED_PIT_REQUIRED","execution":"FRIDAY_SIGNAL_NEXT_SESSION_OPEN_J1","label_embargo":"EXACT_26W_LABEL_END_BEFORE_HOLDOUT","portfolio_simulation":False,"annual_metrics_scope":"OVERLAPPING_26W_SIGNAL_OBSERVATIONS_NOT_CAPITAL_CONSTRAINED_PORTFOLIO","future_returns_embedded":False}}
        (args.out_dir/"V22_1_BACKTEST_REPORT.json").write_text(json.dumps(report,indent=2,sort_keys=True,default=str),encoding="utf-8"); print(json.dumps(report,indent=2,sort_keys=True,default=str)); return 0
    except (HistoricalPITUnavailable,ValueError) as exc: print(str(exc)); return 1

if __name__=="__main__": raise SystemExit(main())