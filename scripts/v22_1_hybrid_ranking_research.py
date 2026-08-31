from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HOLDOUT_START = pd.Timestamp("2023-01-01")
STOP_DEFAULT = 0.09
TOP_K = (1, 3, 5)
BIG_WIN = 0.15


def n(s):
    return pd.to_numeric(s, errors="coerce")


def add_features(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["date"] = pd.to_datetime(x["as_of_date"], errors="coerce")
    close = n(x["close"])
    atr = n(x["atr_14_pct"])
    mom = n(x["mom_26w"])
    dd = n(x["drawdown_4w"])
    rsi = n(x["rsi_14_hebdo"])
    sma200 = n(x["sma200"])
    trend = close / sma200 - 1.0
    stop_pct = n(x["stop_pct_used"]) if "stop_pct_used" in x else pd.Series(STOP_DEFAULT, index=x.index)
    stop_pct = stop_pct.where(np.isfinite(stop_pct) & (stop_pct > 0), STOP_DEFAULT)

    # Strictly ex-ante technical target: highest prior close over 26 weeks, using only values
    # present at signal time. This is a conservative resistance proxy, not future MFE.
    target_col = next((c for c in ("rolling_high_26w", "high_26w", "prior_high_26w", "resistance_26w") if c in x.columns), None)
    if target_col:
        target = n(x[target_col])
        target_source = target_col
    else:
        # No invented target: RR is unavailable and must fail closed for RR-based rankings.
        target = pd.Series(np.nan, index=x.index)
        target_source = None

    upside = target / close - 1.0
    x["rr_ex_ante"] = upside / stop_pct
    x["h_mom_vol"] = mom / (atr.abs() + 0.01)
    x["h_trend_dd"] = trend - dd.abs()
    x["h_opportunity_risk"] = mom / (atr.abs() + dd.abs() + 0.01)
    x["h_rsi_trend"] = ((rsi - 50.0) / 25.0) * trend
    x["ret26"] = n(x["forward_ret_true_26w"])
    x["stop"] = x["hit_stop"].astype("boolean")
    x.attrs["rr_target_source"] = target_source
    return x


def pct_rank_by_date(df: pd.DataFrame, col: str, higher=True) -> pd.Series:
    return df.groupby("date")[col].rank(pct=True, ascending=higher is False, method="average")


def metrics(g: pd.DataFrame) -> dict:
    g = g[g["ret26"].notna() & g["stop"].notna()]
    if g.empty:
        return {"n": 0}
    r = g["ret26"].astype(float)
    w, l = r[r > 0], r[r <= 0]
    gp, gl = float(w.sum()), float((-l).sum())
    return {
        "n": int(len(g)),
        "win_rate": float((r > 0).mean()),
        "stop_rate": float(g["stop"].astype(bool).mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "payoff_ratio": float(w.mean() / abs(l.mean())) if len(w) and len(l) and l.mean() != 0 else None,
        "big_winner_rate": float((r >= BIG_WIN).mean()),
    }


def evaluate(df: pd.DataFrame, score_col: str, label: str) -> list[dict]:
    rows=[]
    z=df.dropna(subset=["date", score_col, "ret26", "stop"]).copy()
    z=z.sort_values(["date", score_col], ascending=[True, False], kind="stable")
    for k in TOP_K:
        top=z.groupby("date", sort=False).head(k)
        rows.append({"ranking": label, "top_k_per_signal_date": k, **metrics(top)})
    return rows


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args=ap.parse_args()
    train=add_features(pd.read_csv(args.input_dir/"V22_1_TRAIN.csv", low_memory=False))
    hold=add_features(pd.read_csv(args.input_dir/"V22_1_HOLDOUT.csv", low_memory=False))
    source=train.attrs.get("rr_target_source")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if source is None:
        report={"version":"V22.1_HYBRID_RANKING_RR_1","status":"BLOCKED_RR_TARGET_UNAVAILABLE","reason":"No governed ex-ante technical target column exists in signal ledger; RR was not invented from future MFE/return.","holdout_used_for_tuning":False}
        (args.out_dir/"HYBRID_RANKING_REPORT.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps(report,indent=2))
        return 0

    train=train[train["date"]<HOLDOUT_START].copy()
    split=int(len(train)*0.80)
    valid=train.iloc[split:].copy()
    # Within-date percentile ranks avoid arbitrary scale weights.
    for df in (valid, hold):
        df["p_rr"]=pct_rank_by_date(df,"rr_ex_ante",True)
        df["p_momvol"]=pct_rank_by_date(df,"h_mom_vol",True)
        df["p_trenddd"]=pct_rank_by_date(df,"h_trend_dd",True)
        df["p_opp"]=pct_rank_by_date(df,"h_opportunity_risk",True)
        df["p_rsi_trend"]=pct_rank_by_date(df,"h_rsi_trend",True)
        df["rank_rr_only"]=df["p_rr"]
        df["rank_rr_momvol"]=np.sqrt(df["p_rr"].clip(0,1)*df["p_momvol"].clip(0,1))
        df["rank_rr_trenddd"]=np.sqrt(df["p_rr"].clip(0,1)*df["p_trenddd"].clip(0,1))
        df["rank_rr_opportunity"]=np.sqrt(df["p_rr"].clip(0,1)*df["p_opp"].clip(0,1))
        df["rank_rr_quality4"]=(df["p_rr"]*df["p_momvol"]*df["p_trenddd"]*df["p_opp"]).clip(lower=0)**0.25

    candidates=["rank_rr_only","rank_rr_momvol","rank_rr_trenddd","rank_rr_opportunity","rank_rr_quality4"]
    val_rows=[]
    for c in candidates: val_rows += evaluate(valid,c,c)
    val=pd.DataFrame(val_rows)
    # Choose on pre-2023 validation only: Top5 expectancy, then fewer stops, PF, big winners.
    v5=val[val["top_k_per_signal_date"]==5].copy()
    v5["pf_sort"]=pd.to_numeric(v5["profit_factor"],errors="coerce").fillna(0)
    v5=v5.sort_values(["expectancy","stop_rate","pf_sort","big_winner_rate"],ascending=[False,True,False,False])
    chosen=str(v5.iloc[0]["ranking"])
    hold_rows=[]
    for c in ["rank_rr_only",chosen]: hold_rows += evaluate(hold,c,c)
    out=pd.DataFrame(hold_rows)
    report={"version":"V22.1_HYBRID_RANKING_RR_1","status":"READY","rr_target_source":source,"chosen_ranking_pre2023":chosen,"holdout_used_for_tuning":False,"selection_rule":"pre2023 validation Top5: expectancy desc, stop rate asc, PF desc, big-winner rate desc","note":"RR uses ex-ante target/stop only; no MFE or future return in ranking."}
    val.to_csv(args.out_dir/"HYBRID_RANKING_VALIDATION.csv",index=False)
    out.to_csv(args.out_dir/"HYBRID_RANKING_HOLDOUT.csv",index=False)
    (args.out_dir/"HYBRID_RANKING_REPORT.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2)); print(out.to_string(index=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
