from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.metrics import roc_auc_score

ROUND_RATIOS = np.array([1.25, 1.3333333333, 1.5, 1.6666666667, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0])
MATCH_FEATURES = ["log_price", "log_volume", "vol20_pct", "ret20_pct"]
FEATURES = [
    "rsi14", "stoch_k14", "stoch_d3", "macd_hist", "macd_hist_d3", "macd_hist_d5",
    "ret1_pct", "ret3_pct", "ret5_pct", "ret10_pct", "ret20_pct",
    "close_sma20_pct", "close_sma50_pct", "sma20_slope5_pct", "sma50_slope10_pct",
    "rvol20", "volume_ratio5_20", "volume_change5_pct",
    "vol20_pct", "atr14_pct", "bb_width20_pct", "drawdown20_pct", "drawdown60_pct",
    "dist_high20_pct", "dist_high60_pct", "breakout20_flag", "range_pct", "body_pct",
    "rsi_d3", "rsi_d5", "rsi_d10", "stoch_k_d3", "stoch_k_d5",
]


def _round_ratio_suspect(r: pd.Series) -> pd.Series:
    x = r.to_numpy(float)
    out = np.zeros(len(x), dtype=bool)
    ok = np.isfinite(x)
    if ok.any():
        d = np.abs(x[ok, None] - ROUND_RATIOS[None, :]) / ROUND_RATIOS[None, :]
        out[ok] = d.min(axis=1) <= 0.0075
    return pd.Series(out, index=r.index)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True).copy()
    g = df.groupby("ticker", group_keys=False)
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    opn = pd.to_numeric(df["open"], errors="coerce")
    vol = pd.to_numeric(df["volume"], errors="coerce")

    df["date_t5"] = g["date"].shift(-5)
    df["close_t5"] = g["close"].shift(-5)
    df["ret_fwd5_pct"] = (df["close_t5"] / close - 1.0) * 100.0
    df["future_ratio"] = df["close_t5"] / close

    prev = g["close"].shift(1)
    daily_ret = close / prev - 1.0
    df["daily_ret"] = daily_ret
    for n in [1, 3, 5, 10, 20]:
        df[f"ret{n}_pct"] = (close / g["close"].shift(n) - 1.0) * 100.0

    sma20 = g["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    sma50 = g["close"].transform(lambda s: s.rolling(50, min_periods=50).mean())
    df["close_sma20_pct"] = (close / sma20 - 1.0) * 100.0
    df["close_sma50_pct"] = (close / sma50 - 1.0) * 100.0
    df["sma20_slope5_pct"] = (sma20 / sma20.groupby(df["ticker"]).shift(5) - 1.0) * 100.0
    df["sma50_slope10_pct"] = (sma50 / sma50.groupby(df["ticker"]).shift(10) - 1.0) * 100.0

    ema12 = g["close"].transform(lambda s: s.ewm(span=12, adjust=False, min_periods=12).mean())
    ema26 = g["close"].transform(lambda s: s.ewm(span=26, adjust=False, min_periods=26).mean())
    macd = ema12 - ema26
    macd_signal = macd.groupby(df["ticker"]).transform(lambda s: s.ewm(span=9, adjust=False, min_periods=9).mean())
    df["macd_hist"] = macd - macd_signal

    delta = close - prev
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.groupby(df["ticker"]).transform(lambda s: s.ewm(alpha=1/14, adjust=False, min_periods=14).mean())
    avg_loss = loss.groupby(df["ticker"]).transform(lambda s: s.ewm(alpha=1/14, adjust=False, min_periods=14).mean())
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi14"] = 100.0 - 100.0 / (1.0 + rs)

    low14 = g["low"].transform(lambda s: s.rolling(14, min_periods=14).min())
    high14 = g["high"].transform(lambda s: s.rolling(14, min_periods=14).max())
    denom = (high14 - low14).replace(0, np.nan)
    df["stoch_k14"] = 100.0 * (close - low14) / denom
    df["stoch_d3"] = df.groupby("ticker")["stoch_k14"].transform(lambda s: s.rolling(3, min_periods=3).mean())

    prev_close = prev
    tr = pd.concat([(high-low).abs(), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    atr14 = tr.groupby(df["ticker"]).transform(lambda s: s.rolling(14, min_periods=14).mean())
    df["atr14_pct"] = 100.0 * atr14 / close

    std20 = g["close"].transform(lambda s: s.rolling(20, min_periods=20).std(ddof=0))
    df["bb_width20_pct"] = 100.0 * (4.0 * std20) / sma20
    df["vol20_pct"] = daily_ret.groupby(df["ticker"]).transform(lambda s: s.rolling(20, min_periods=20).std(ddof=0)) * math.sqrt(252) * 100.0

    vol20 = g["volume"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    vol5 = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean())
    df["rvol20"] = vol / vol20.replace(0, np.nan)
    df["volume_ratio5_20"] = vol5 / vol20.replace(0, np.nan)
    df["volume_change5_pct"] = (vol5 / vol5.groupby(df["ticker"]).shift(5) - 1.0) * 100.0

    h20 = g["high"].transform(lambda s: s.shift(1).rolling(20, min_periods=20).max())
    h60 = g["high"].transform(lambda s: s.shift(1).rolling(60, min_periods=60).max())
    df["dist_high20_pct"] = (close / h20 - 1.0) * 100.0
    df["dist_high60_pct"] = (close / h60 - 1.0) * 100.0
    df["breakout20_flag"] = (close >= h20).astype(float)
    df["drawdown20_pct"] = df["dist_high20_pct"]
    df["drawdown60_pct"] = df["dist_high60_pct"]
    df["range_pct"] = 100.0 * (high-low) / close.replace(0, np.nan)
    df["body_pct"] = 100.0 * (close-opn) / opn.replace(0, np.nan)

    for name, lags in {"rsi14":[3,5,10], "stoch_k14":[3,5], "macd_hist":[3,5]}.items():
        for lag in lags:
            df[f"{name.replace('14','').replace('stoch_k','stoch_k').replace('macd_hist','macd_hist')}_d{lag}"] = df[name] - df.groupby("ticker")[name].shift(lag)
    # normalize generated names
    df["rsi_d3"] = df.pop("rsi_d3") if "rsi_d3" in df else df["rsi14"] - df.groupby("ticker")["rsi14"].shift(3)
    df["rsi_d5"] = df["rsi14"] - df.groupby("ticker")["rsi14"].shift(5)
    df["rsi_d10"] = df["rsi14"] - df.groupby("ticker")["rsi14"].shift(10)
    df["stoch_k_d3"] = df["stoch_k14"] - df.groupby("ticker")["stoch_k14"].shift(3)
    df["stoch_k_d5"] = df["stoch_k14"] - df.groupby("ticker")["stoch_k14"].shift(5)
    df["macd_hist_d3"] = df["macd_hist"] - df.groupby("ticker")["macd_hist"].shift(3)
    df["macd_hist_d5"] = df["macd_hist"] - df.groupby("ticker")["macd_hist"].shift(5)

    df["log_price"] = np.log(close.where(close>0))
    df["log_volume"] = np.log1p(vol.clip(lower=0))
    df["year"] = df["date"].dt.year
    return df


def retained_universe(df: pd.DataFrame) -> pd.DataFrame:
    core = np.isfinite(df[FEATURES].to_numpy(float)).all(axis=1)
    valid_future = np.isfinite(df["close_t5"]) & (df["close_t5"] > 0)
    clean_start = (df["close"] >= 1.0) & (df["volume"] >= 5000)
    no_extreme = df["ret_fwd5_pct"] <= 50.0
    no_round = ~_round_ratio_suspect(df["future_ratio"])
    out = df.loc[core & valid_future & clean_start & no_extreme & no_round].copy()
    out["winner_5d"] = out["ret_fwd5_pct"] > 20.0
    return out


def winner_episodes(univ: pd.DataFrame) -> pd.DataFrame:
    hits = univ.loc[univ["winner_5d"]].copy()
    hits["pos_in_ticker"] = hits.groupby("ticker").cumcount()
    # use actual ordinal position from source index for overlap clustering
    hits["source_pos"] = hits.index
    hits["prev_date"] = hits.groupby("ticker")["date"].shift(1)
    # cluster by source-row distance is unsafe across tickers; recompute ordinal on full universe ticker
    # dates within <= 10 calendar days are usually overlapping 5-session windows; use business-window proxy via row index map
    # construct per ticker ordinal from full retained universe first
    ord_map = univ.groupby("ticker").cumcount()
    hits["ord"] = ord_map.loc[hits.index].to_numpy()
    hits["prev_ord"] = hits.groupby("ticker")["ord"].shift(1)
    hits["new_episode"] = (hits["prev_ord"].isna() | ((hits["ord"]-hits["prev_ord"])>5)).astype(int)
    hits["episode_id"] = hits.groupby("ticker")["new_episode"].cumsum()
    idx = hits.groupby(["ticker","episode_id"])["ret_fwd5_pct"].idxmax()
    ep = hits.loc[idx].copy().sort_values(["date","ticker"]).reset_index(drop=True)
    return ep


def matched_controls(univ: pd.DataFrame, episodes: pd.DataFrame, n_controls: int = 3) -> pd.DataFrame:
    cols = ["date","ticker","winner_5d","ret_fwd5_pct"] + FEATURES + MATCH_FEATURES
    by_date = {d:g for d,g in univ.groupby("date", sort=False)}
    records=[]
    for _, w in episodes.iterrows():
        d=w["date"]
        pool=by_date.get(d)
        if pool is None:
            continue
        pool=pool.loc[~pool["winner_5d"] & (pool["ticker"] != w["ticker"])].copy()
        if len(pool)<n_controls:
            continue
        dist=np.zeros(len(pool), dtype=float)
        for f in MATCH_FEATURES:
            s=pool[f].astype(float)
            scale=float(s.std(ddof=0))
            if not np.isfinite(scale) or scale<1e-9:
                scale=1.0
            dist += ((s.to_numpy()-float(w[f]))/scale)**2
        pick=np.argpartition(dist, min(n_controls,len(dist))-1)[:n_controls]
        win={c:w[c] for c in cols}
        win.update({"case_id":f"{w['ticker']}|{pd.Timestamp(d).date()}","role":"WINNER","match_distance":0.0})
        records.append(win)
        p2=pool.iloc[pick].copy()
        for j,(ix,c) in enumerate(p2.iterrows()):
            rec={k:c[k] for k in cols}
            rec.update({"case_id":win["case_id"],"role":f"CONTROL_{j+1}","match_distance":float(math.sqrt(dist[pick[j]]))})
            records.append(rec)
    return pd.DataFrame(records)


def bh_fdr(pvals: np.ndarray) -> np.ndarray:
    p=np.asarray(pvals,float)
    n=len(p); order=np.argsort(p); ranked=p[order]
    q=ranked*n/np.arange(1,n+1)
    q=np.minimum.accumulate(q[::-1])[::-1]
    out=np.empty(n); out[order]=np.minimum(q,1.0)
    return out


def univariate(matched: pd.DataFrame, end_year: int = 2018) -> pd.DataFrame:
    d=matched.loc[matched["date"].dt.year<=end_year].copy()
    rows=[]
    for f in FEATURES:
        a=d.loc[d["winner_5d"],f].astype(float).dropna()
        b=d.loc[~d["winner_5d"],f].astype(float).dropna()
        if min(len(a),len(b))<30: continue
        pooled=math.sqrt((a.var(ddof=1)+b.var(ddof=1))/2) if a.var(ddof=1)>=0 and b.var(ddof=1)>=0 else np.nan
        smd=(a.mean()-b.mean())/pooled if pooled and np.isfinite(pooled) else np.nan
        y=np.r_[np.ones(len(a)),np.zeros(len(b))]
        x=np.r_[a.to_numpy(),b.to_numpy()]
        try: auc=roc_auc_score(y,x)
        except Exception: auc=np.nan
        try: p=float(mannwhitneyu(a,b,alternative="two-sided").pvalue)
        except Exception: p=np.nan
        rows.append({"feature":f,"winner_n":len(a),"control_n":len(b),"winner_mean":a.mean(),"control_mean":b.mean(),"winner_median":a.median(),"control_median":b.median(),"smd":smd,"auc_raw":auc,"auc_discrimination":max(auc,1-auc) if np.isfinite(auc) else np.nan,"direction":"HIGH" if a.median()>=b.median() else "LOW","p_value":p})
    out=pd.DataFrame(rows)
    out["q_value_bh"]=bh_fdr(out["p_value"].fillna(1.0).to_numpy())
    out["rank_score"]=(out["auc_discrimination"]-0.5).abs()*2 + out["smd"].abs().clip(0,3)/3
    return out.sort_values("rank_score",ascending=False).reset_index(drop=True)


def condition_mask(df: pd.DataFrame, cond: dict) -> pd.Series:
    s=df[cond["feature"]]
    return s>=cond["threshold"] if cond["op"]==">=" else s<=cond["threshold"]


def eval_mask(df: pd.DataFrame, mask: pd.Series) -> dict:
    x=df.loc[mask]
    n=len(x); wins=int(x["winner_5d"].sum())
    base=float(df["winner_5d"].mean()) if len(df) else np.nan
    rate=wins/n if n else np.nan
    return {"signals":n,"wins":wins,"win_rate":rate,"baseline_rate":base,"lift":rate/base if n and base>0 else np.nan}


def discover_conditions(univ: pd.DataFrame, uni: pd.DataFrame) -> list[dict]:
    disc=univ.loc[univ["year"]<=2018]
    top=uni.head(14)
    conds=[]
    for _,r in top.iterrows():
        f=r["feature"]; direction=r["direction"]
        s=disc[f].dropna()
        qs=[0.60,0.70,0.80] if direction=="HIGH" else [0.40,0.30,0.20]
        for q in qs:
            th=float(s.quantile(q))
            c={"feature":f,"op":">=" if direction=="HIGH" else "<=","threshold":th}
            m=condition_mask(disc,c); ev=eval_mask(disc,m)
            if ev["signals"]>=300 and ev["wins"]>=20 and ev["lift"]>=1.15:
                c.update({f"disc_{k}":v for k,v in ev.items()})
                conds.append(c)
    conds=sorted(conds,key=lambda c:(c["disc_lift"],c["disc_wins"]),reverse=True)
    # keep at most two thresholds per feature to control multiplicity
    kept=[]; counts={}
    for c in conds:
        if counts.get(c["feature"],0)>=2: continue
        kept.append(c); counts[c["feature"]]=counts.get(c["feature"],0)+1
    return kept[:24]


def discover_patterns(univ: pd.DataFrame, conds: list[dict]) -> pd.DataFrame:
    disc=univ.loc[univ["year"]<=2018]
    candidates=[]
    for size in [2,3]:
        source=conds[:20] if size==2 else conds[:14]
        for combo in combinations(source,size):
            feats=[c["feature"] for c in combo]
            if len(set(feats))<size: continue
            m=pd.Series(True,index=disc.index)
            for c in combo: m &= condition_mask(disc,c)
            ev=eval_mask(disc,m)
            if ev["signals"]<200 or ev["wins"]<20 or ev["lift"]<1.35: continue
            expr=" AND ".join(f"{c['feature']} {c['op']} {c['threshold']:.6g}" for c in combo)
            candidates.append({"pattern":expr,"n_conditions":size,"conditions_json":json.dumps(combo),**{f"disc_{k}":v for k,v in ev.items()}})
    out=pd.DataFrame(candidates)
    if out.empty: return out
    out["discovery_score"]=out["disc_lift"]*np.sqrt(out["disc_wins"])
    return out.sort_values("discovery_score",ascending=False).drop_duplicates("pattern").head(120).reset_index(drop=True)


def evaluate_patterns(univ: pd.DataFrame, pats: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame]:
    rows=[]; annual=[]
    periods={"DISCOVERY_2010_2018":(2010,2018),"VALIDATION_2019_2022":(2019,2022),"OOS_2023_2026":(2023,2026)}
    for pid,p in pats.iterrows():
        conds=json.loads(p["conditions_json"])
        rec={"pattern_id":int(pid+1),"pattern":p["pattern"],"n_conditions":int(p["n_conditions"]),"conditions_json":p["conditions_json"]}
        for label,(y0,y1) in periods.items():
            d=univ.loc[univ["year"].between(y0,y1)]
            m=pd.Series(True,index=d.index)
            for c in conds: m &= condition_mask(d,c)
            ev=eval_mask(d,m)
            for k,v in ev.items(): rec[f"{label}_{k}"]=v
        for y in range(2010,2027):
            d=univ.loc[univ["year"]==y]
            if d.empty: continue
            m=pd.Series(True,index=d.index)
            for c in conds: m &= condition_mask(d,c)
            ev=eval_mask(d,m)
            annual.append({"pattern_id":int(pid+1),"year":y,**ev})
        rec["dev_validated"]=(rec["VALIDATION_2019_2022_signals"]>=100 and rec["VALIDATION_2019_2022_wins"]>=10 and rec["VALIDATION_2019_2022_lift"]>=1.20)
        rec["oos_confirmed"]=(rec["OOS_2023_2026_signals"]>=50 and rec["OOS_2023_2026_wins"]>=5 and rec["OOS_2023_2026_lift"]>=1.20)
        rows.append(rec)
    res=pd.DataFrame(rows)
    ann=pd.DataFrame(annual)
    if not res.empty and not ann.empty:
        stable=[]
        for pid in res["pattern_id"]:
            a=ann[(ann.pattern_id==pid)&(ann.year.between(2010,2022))]
            valid=a.loc[a.signals>=20]
            stable.append(int((valid.lift>1.0).sum()) >= max(6, math.ceil(0.70*len(valid))) if len(valid) else False)
        res["dev_year_stable"]=stable
        res["status"]=np.where(res.dev_validated & res.dev_year_stable & res.oos_confirmed,"CONFIRMED_OOS",np.where(res.dev_validated & res.dev_year_stable,"DEV_VALIDATED_OOS_FAIL","REJECTED_DEV"))
        res=res.sort_values(["status","OOS_2023_2026_lift","VALIDATION_2019_2022_lift"],ascending=[True,False,False]).reset_index(drop=True)
    return res,ann


def main(df: pd.DataFrame, outdir: str | Path) -> None:
    out=Path(outdir); out.mkdir(parents=True,exist_ok=True)
    df=df[[c for c in ["date","ticker","open","high","low","close","volume","segment"] if c in df.columns]].copy()
    df["date"]=pd.to_datetime(df["date"],utc=True).dt.tz_localize(None)
    f=add_features(df)
    univ=retained_universe(f)
    episodes=winner_episodes(univ)
    dev_ep=episodes.loc[episodes["year"]<=2022]
    matched=matched_controls(univ,dev_ep,n_controls=3)
    matched["date"]=pd.to_datetime(matched["date"])
    uni=univariate(matched,end_year=2018)
    conds=discover_conditions(univ,uni)
    pats=discover_patterns(univ,conds)
    results,annual=evaluate_patterns(univ,pats)

    episode_cols=["date","ticker","ret_fwd5_pct","close","volume","year"]+FEATURES
    episodes[episode_cols].to_csv(out/"WINNER_5D_EPISODES_CLEAN.csv",index=False)
    matched.to_csv(out/"MATCHED_WINNERS_CONTROLS_DEV.csv",index=False)
    uni.to_csv(out/"UNIVARIATE_FACTORS_DISCOVERY.csv",index=False)
    pd.DataFrame(conds).to_csv(out/"CANDIDATE_SINGLE_CONDITIONS.csv",index=False)
    pats.to_csv(out/"CANDIDATE_PATTERNS_DISCOVERY.csv",index=False)
    results.to_csv(out/"PATTERN_VALIDATION_OOS.csv",index=False)
    annual.to_csv(out/"PATTERN_BY_YEAR.csv",index=False)
    confirmed=results.loc[results.get("status",pd.Series(dtype=str))=="CONFIRMED_OOS"].copy() if not results.empty else results
    confirmed.to_csv(out/"PATTERNS_CONFIRMED_OOS.csv",index=False)

    meta={
        "source_rows":int(len(df)),"source_tickers":int(df.ticker.nunique()),
        "analysis_eligible_rows":int(len(univ)),"clean_winner_windows":int(univ.winner_5d.sum()),
        "deduplicated_clean_winner_episodes":int(len(episodes)),"development_episodes_2010_2022":int(len(dev_ep)),
        "oos_episodes_2023_2026":int((episodes.year>=2023).sum()),"matched_rows":int(len(matched)),
        "candidate_conditions":int(len(conds)),"candidate_patterns":int(len(pats)),
        "confirmed_oos_patterns":int(len(confirmed)),"development_period":"2010-2022","discovery_subperiod":"2010-2018","validation_subperiod":"2019-2022","oos_period":"2023-2026",
        "target":"strictly >20% close-to-close over next 5 trading sessions, with QA retained rules",
        "qa":"start close>=1, volume>=5000, future gain<=50%, no round-ratio corporate-action suspicion",
        "consensus_history_used":False,"reason_consensus_not_used":"no genuine historical Boursorama snapshots before 2026-08-26",
        "pit_universe_certified":False,"survivorship_safe":False,
    }
    (out/"METADATA.json").write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding="utf-8")
    report=["# Retro-engineering 5 séances — patterns", "", json.dumps(meta,indent=2,ensure_ascii=False), ""]
    if len(confirmed):
        report += ["## Patterns confirmés OOS", "", confirmed.head(20).to_markdown(index=False)]
    else:
        report += ["## Verdict", "", "Aucun pattern ne franchit simultanément les gates de validation développement, stabilité annuelle et confirmation OOS."]
    (out/"REPORT.md").write_text("\n".join(report),encoding="utf-8")
    print(json.dumps(meta,indent=2,ensure_ascii=False))
    if not results.empty:
        print(results[["pattern_id","pattern","status","VALIDATION_2019_2022_lift","OOS_2023_2026_lift"]].head(20).to_string(index=False))


if __name__ == "__main__":
    from v182.hebdo.meta_price_history import load_2010_2026
    df=load_2010_2026(
        "inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet",
        "inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json",
        "data/cache/actions",
    )
    main(df,"outputs/retro_5d_patterns")
