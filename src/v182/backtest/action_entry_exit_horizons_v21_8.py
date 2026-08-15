from __future__ import annotations

from pathlib import Path
import json
import math
import numpy as np
import pandas as pd

from v182.backtest.exceptional_pit_oos import HOLDOUT_START, _load_action_histories, _naive_index, _read_csv
from v182.sources.yfinance_bulk import download_history

ROOT = Path(__file__).resolve().parents[3]
PERIOD_START = pd.Timestamp("2018-01-01")
ENTRY_DELAYS = (1, 3, 5)
HORIZON_SESSIONS = {"TCT": 20, "CT": 63, "MT": 168}
CHECKPOINTS = {
    "TCT": (1, 2, 3, 5, 10, 15, 20),
    "CT": (5, 10, 21, 42, 63),
    "MT": (10, 21, 42, 63, 126, 168),
}
LOSS_LEVELS = {
    "TCT": (-0.02, -0.03, -0.05, -0.07),
    "CT": (-0.03, -0.05, -0.07, -0.10),
    "MT": (-0.03, -0.05, -0.07, -0.10),
}

CT_WEIGHTS = {
    "rsi14": (0.03, "HIGH"), "macd_hist": (0.06, "HIGH"), "rvol20": (0.05, "HIGH"),
    "perf_1m": (0.08, "HIGH"), "perf_3m": (0.05, "HIGH"), "vol20": (0.02, "LOW"),
    "max_dd_1y": (0.04, "HIGH"), "perf_6m": (0.02, "HIGH"), "volume": (0.06, "HIGH"),
    "relative_strength": (0.07, "HIGH"), "positive_reversal": (0.12, "HIGH"),
    "stoch_bull_cross": (0.03, "HIGH"),
}
MT_WEIGHTS = {
    "perf_3m": (0.04, "HIGH"), "perf_1y": (0.08, "HIGH"), "vol60": (0.03, "LOW"),
    "max_dd_1y": (0.06, "HIGH"), "perf_3y": (0.04, "HIGH"), "perf_6m": (0.07, "HIGH"),
    "volume": (0.01, "HIGH"), "relative_strength": (0.04, "HIGH"), "beta_1y": (0.02, "LOW"),
}


def _clean_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty or "Close" not in history.columns:
        return pd.DataFrame()
    out = history.copy().sort_index()
    idx = _naive_index(out.index)
    out = out.iloc[: len(idx)].copy()
    out.index = idx
    out = out[~out.index.duplicated(keep="last")]
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["Close"])


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    d = close.diff(); up = d.clip(lower=0); down = -d.clip(upper=0)
    rs = up.ewm(alpha=1/window, adjust=False, min_periods=window).mean() / down.ewm(alpha=1/window, adjust=False, min_periods=window).mean().replace(0, np.nan)
    return 100 - 100/(1+rs)


def _features(history: pd.DataFrame, market: pd.Series) -> pd.DataFrame:
    h = _clean_history(history)
    if h.empty:
        return pd.DataFrame()
    c = h["Close"]; v = h.get("Volume", pd.Series(index=h.index, dtype=float))
    f = pd.DataFrame(index=h.index)
    f["close"] = c
    f["volume"] = v
    f["perf_1m"] = c.pct_change(21); f["perf_3m"] = c.pct_change(63); f["perf_6m"] = c.pct_change(126)
    f["perf_1y"] = c.pct_change(252); f["perf_3y"] = c.pct_change(756)
    f["vol20"] = c.pct_change().rolling(20).std()*math.sqrt(252); f["vol60"] = c.pct_change().rolling(60).std()*math.sqrt(252)
    f["max_dd_1y"] = c/c.rolling(252).max()-1
    f["rsi14"] = _rsi(c)
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean(); macd=ema12-ema26; sig=macd.ewm(span=9,adjust=False).mean()
    f["macd_hist"] = macd-sig
    f["rvol20"] = v / v.shift(1).rolling(20).mean()
    sma20=c.rolling(20).mean(); std20=c.rolling(20).std(); upper=sma20+2*std20; lower=sma20-2*std20
    f["bb_upper"] = upper; f["bb_lower"] = lower; f["bandwidth"] = (upper-lower)/sma20.replace(0,np.nan)
    high=h.get("High",c); low=h.get("Low",c); prev=c.shift(1)
    tr=pd.concat([(high-low).abs(),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1); f["atr14"]=tr.rolling(14).mean()
    f["sma50"]=c.rolling(50).mean(); f["sma200"]=c.rolling(200).mean()
    f["dist_sma50"]=c/f["sma50"]-1; f["dist_sma200"]=c/f["sma200"]-1
    f["slope_sma50_20d"]=f["sma50"]/f["sma50"].shift(20)-1
    low14=low.rolling(14).min(); high14=high.rolling(14).max(); k=100*(c-low14)/(high14-low14).replace(0,np.nan); d=k.rolling(3).mean()
    f["stoch_bull_cross"]=(k.gt(d)&k.shift(1).le(d.shift(1))).astype(float)
    f["positive_reversal"]=((c>f["sma50"])&(f["perf_1m"]>0)&(f["macd_hist"]>f["macd_hist"].shift(1))).astype(float)
    m=market.reindex(f.index).ffill(); m1=m.pct_change(21); f["relative_strength"]=f["perf_1m"]-m1
    asset_ret=c.pct_change(); market_ret=m.pct_change(); f["beta_1y"]=asset_ret.rolling(252).cov(market_ret)/market_ret.rolling(252).var()
    f["ret_5d"]=c.pct_change(5); f["ret_21d"]=c.pct_change(21); f["drawdown_63d"]=c/c.rolling(63).max()-1
    return f


def _market_proxy(histories: dict[str,pd.DataFrame]) -> pd.Series:
    series=[]
    for h in histories.values():
        x=_clean_history(h)
        if not x.empty:
            s=x["Close"]/float(x["Close"].dropna().iloc[0]); series.append(s)
    if not series:
        return pd.Series(dtype=float)
    return pd.concat(series,axis=1).median(axis=1,skipna=True).sort_index()


def _score_cross_section(frame: pd.DataFrame, weights: dict[str,tuple[float,str]]) -> pd.Series:
    total=pd.Series(0.0,index=frame.index); avail=pd.Series(0.0,index=frame.index)
    for field,(w,direction) in weights.items():
        if field not in frame.columns: continue
        x=pd.to_numeric(frame[field],errors="coerce"); rank=x.rank(pct=True,method="average")
        if direction=="LOW": rank=1-rank
        ok=x.notna(); total.loc[ok]+=w*rank.loc[ok]*100; avail.loc[ok]+=w
    return total/avail.replace(0,np.nan)


def _tct_proxy_signals(features: dict[str,pd.DataFrame], market: pd.Series) -> pd.DataFrame:
    rows=[]
    all_dates=sorted(set().union(*(set(f.index) for f in features.values())))
    for dt in all_dates:
        if dt<PERIOD_START or dt>=HOLDOUT_START: continue
        candidates=[]
        for isin,f in features.items():
            if dt not in f.index: continue
            r=f.loc[dt]
            if pd.isna(r.get("bandwidth")) or pd.isna(r.get("rvol20")) or pd.isna(r.get("atr14")): continue
            hist=f.loc[:dt].tail(101); bw=hist["bandwidth"].dropna()
            if len(bw)<80: continue
            squeeze_thr=float(bw.iloc[:-1].quantile(0.15)) if len(bw)>1 else np.nan
            prior8=bw.iloc[-9:-1] if len(bw)>=9 else pd.Series(dtype=float)
            compression=float((prior8<=squeeze_thr).mean()) if len(prior8)>=5 else 0.0
            prev=f.shift(1).loc[dt]
            cross=bool(r["close"]>=r["bb_upper"] and prev["close"]<prev["bb_upper"]) if pd.notna(prev.get("bb_upper")) else False
            expanding=bool(r["bandwidth"]>prev["bandwidth"]) if pd.notna(prev.get("bandwidth")) else False
            extension_pct=(r["close"]/r["sma50"]-1) if pd.notna(r.get("sma50")) and r["sma50"] else np.nan
            extension_atr=(r["close"]-r["sma50"])/r["atr14"] if pd.notna(r.get("sma50")) and r["atr14"] else np.nan
            macd_rising=float((hist["macd_hist"].tail(3).diff().dropna()>0).mean()) if "macd_hist" in hist else 0.0
            t1=(compression>=0.80 and r["rvol20"]>=1.20 and cross and expanding and pd.notna(extension_pct) and extension_pct<=0.10 and pd.notna(extension_atr) and extension_atr<=1.5 and macd_rising>=0.50 and r["close"]>r["sma50"] and r["rsi14"]<70)
            if not t1: continue
            quality=sum([compression, min(float(r["rvol20"])/2,1), 1.0 if cross else 0, 1.0 if expanding else 0, max(0,min(1,(r["relative_strength"]+0.1)/0.2)) if pd.notna(r["relative_strength"]) else 0, 1.0 if extension_pct<=0.10 else 0])/6*100
            candidates.append((isin,quality))
        candidates=sorted(candidates,key=lambda x:x[1],reverse=True)[:20]
        for isin,q in candidates:
            if q>=70: rows.append({"signal_date":dt,"isin":isin,"horizon":"TCT","signal_score":q,"signal_source":"V24.1.7_T1_PRICE_EXECUTABLE_PROXY","t1_proxy":True,"t2_proxy":False})
    return pd.DataFrame(rows)


def _ct_mt_signals(features: dict[str,pd.DataFrame], horizon: str) -> pd.DataFrame:
    weights=CT_WEIGHTS if horizon=="CT" else MT_WEIGHTS
    freq="W-FRI" if horizon=="CT" else "ME"
    all_idx=sorted(set().union(*(set(f.index) for f in features.values())))
    if not all_idx: return pd.DataFrame()
    dates=pd.Series(pd.DatetimeIndex(all_idx),index=pd.DatetimeIndex(all_idx)).resample(freq).last().dropna().tolist()
    rows=[]
    for dt in dates:
        dt=pd.Timestamp(dt)
        if dt<PERIOD_START or dt>=HOLDOUT_START: continue
        snap=[]
        for isin,f in features.items():
            sub=f.loc[:dt]
            if sub.empty: continue
            r=sub.iloc[-1].copy(); r["isin"]=isin; snap.append(r)
        if not snap: continue
        frame=pd.DataFrame(snap).set_index("isin"); frame["score"]=_score_cross_section(frame,weights)
        selected=frame[frame["score"]>=77].sort_values("score",ascending=False).head(20)
        for isin,r in selected.iterrows(): rows.append({"signal_date":dt,"isin":isin,"horizon":horizon,"signal_score":float(r["score"]),"signal_source":f"V21.0_{horizon}_PRICE_CORE_PROXY_RENORMALIZED"})
    return pd.DataFrame(rows)


def _path(features: pd.DataFrame, signal_date: pd.Timestamp, horizon: str, delay: int) -> dict|None:
    future=features[features.index>signal_date]
    if len(future)<delay: return None
    start=delay-1; path=future.iloc[start:start+HORIZON_SESSIONS[horizon]]
    if path.empty or path.index[-1]>=HOLDOUT_START: return None
    entry=float(path.iloc[0]["close"]); returns=path["close"]/entry-1
    out={"entry_delay_sessions":delay,"entry_date":path.index[0].date().isoformat(),"entry_price":entry,"sessions_observed":len(path),"mfe":float(returns.max()),"mae":float(returns.min()),"final_return":float(returns.iloc[-1]),"time_to_mfe_sessions":int(np.argmax(returns.to_numpy()))+1,"time_to_mae_sessions":int(np.argmin(returns.to_numpy()))+1,"max_drawdown_from_peak":float((path["close"]/path["close"].cummax()-1).min())}
    for cp in CHECKPOINTS[horizon]:
        sub=returns.iloc[:min(cp,len(returns))]
        out[f"return_{cp}s"]=float(sub.iloc[-1]); out[f"mfe_{cp}s"]=float(sub.max()); out[f"mae_{cp}s"]=float(sub.min())
    for level in LOSS_LEVELS[horizon]:
        pct=int(abs(level)*100); hit=returns[returns<=level]
        out[f"hit_{pct}pct_loss"]=not hit.empty
        if not hit.empty:
            first=hit.index[0]; out[f"time_to_{pct}pct_loss"]=int(path.index.get_loc(first))+1
            after=returns.loc[first:]; out[f"recovered_after_{pct}pct_loss"]=bool((after>=0).any())
            br=path.loc[first]
            for fld in ("ret_5d","ret_21d","dist_sma50","dist_sma200","slope_sma50_20d","drawdown_63d","vol20"):
                out[f"breach_{pct}_{fld}"]=float(br[fld]) if fld in br and pd.notna(br[fld]) else None
    return out


def _period(dt: pd.Timestamp)->str:
    return "DEVELOPMENT" if dt.year<=2020 else "VALIDATION_OOS" if dt.year<=2023 else "DIAGNOSTIC_OOS"


def _aggregate(paths: pd.DataFrame)->pd.DataFrame:
    if paths.empty: return pd.DataFrame()
    rows=[]
    for (hz,period,delay),g in paths.groupby(["horizon","period","entry_delay_sessions"]):
        mfe=pd.to_numeric(g["mfe"],errors="coerce"); mae=pd.to_numeric(g["mae"],errors="coerce"); fin=pd.to_numeric(g["final_return"],errors="coerce")
        row={"horizon":hz,"period":period,"entry_delay_sessions":delay,"signals":len(g),"median_mfe_pct":100*mfe.median(),"median_mae_pct":100*mae.median(),"median_final_return_pct":100*fin.median(),"positive_final_rate":float((fin>0).mean()),"median_time_to_mfe_sessions":pd.to_numeric(g["time_to_mfe_sessions"],errors="coerce").median()}
        for level in LOSS_LEVELS[hz]:
            pct=int(abs(level)*100); col=f"hit_{pct}pct_loss"; rec=f"recovered_after_{pct}pct_loss"
            row[f"hit_{pct}pct_loss_rate"]=float(g[col].fillna(False).astype(bool).mean()) if col in g else None
            hitg=g[g[col].fillna(False).astype(bool)] if col in g else pd.DataFrame()
            row[f"recovery_after_{pct}pct_loss_rate"]=float(hitg[rec].fillna(False).astype(bool).mean()) if not hitg.empty and rec in hitg else None
        rows.append(row)
    return pd.DataFrame(rows)


def run(root: Path=ROOT)->dict:
    src=root/"inputs"/"V18.2_PEA_ACTIONS_MASTER.csv"; actions=_read_csv(src)
    if actions.empty or "isin" not in actions.columns: raise RuntimeError("ACTION_INPUT_MISSING")
    ticker_col="yahoo_ticker" if "yahoo_ticker" in actions.columns else "ticker_yahoo_final" if "ticker_yahoo_final" in actions.columns else None
    if ticker_col is None: raise RuntimeError("ACTION_TICKER_MAPPING_MISSING")
    ticker_to_isin={str(t).strip():str(i).strip() for t,i in zip(actions[ticker_col],actions["isin"]) if str(t).strip() and str(t).strip().lower() not in {"nan","none","<na>"}}
    cache=root/"data"/"cache"/"action_entry_exit_v21_8"; dl=download_history(list(ticker_to_isin),str(cache),period="max",interval="1d",batch_size=30,auto_adjust=True,include_actions=False)
    histories=_load_action_histories(cache,ticker_to_isin); market=_market_proxy(histories)
    feats={isin:_features(h,market) for isin,h in histories.items()}; feats={k:v for k,v in feats.items() if not v.empty}
    signals=pd.concat([_tct_proxy_signals(feats,market),_ct_mt_signals(feats,"CT"),_ct_mt_signals(feats,"MT")],ignore_index=True)
    rows=[]
    for _,s in signals.iterrows():
        dt=pd.Timestamp(s["signal_date"]); isin=str(s["isin"]); hz=str(s["horizon"])
        if isin not in feats: continue
        for delay in ENTRY_DELAYS:
            p=_path(feats[isin],dt,hz,delay)
            if p is not None: rows.append({**s.to_dict(),"signal_date":dt.date().isoformat(),"period":_period(dt),**p})
    paths=pd.DataFrame(rows); agg=_aggregate(paths)
    outdir=root/"outputs"/"research"/"action_entry_exit_horizons_v21_8"; outdir.mkdir(parents=True,exist_ok=True)
    signals.to_csv(outdir/"ACTION_SIGNALS_PROXY.csv",sep=";",index=False,encoding="utf-8-sig")
    paths.to_csv(outdir/"ACTION_SIGNAL_PATHS.csv",sep=";",index=False,encoding="utf-8-sig")
    agg.to_csv(outdir/"ACTION_HORIZON_AGGREGATES.csv",sep=";",index=False,encoding="utf-8-sig")
    payload={"status":"SUCCESS" if not paths.empty else "BLOCKED_NO_PATHS","study":"ACTION_TCT_CT_MT_ENTRY_EXIT_V21_8","holdout_opened":False,"real_orders_enabled":False,"t1_t2_scope":"ACTION_TCT_ONLY","take_profit_applied":False,"stop_loss_applied":False,"entry_delays_sessions":list(ENTRY_DELAYS),"horizon_sessions":HORIZON_SESSIONS,"download":{"requested":dl.requested,"successful":len(dl.successful),"failed":len(dl.failed)},"histories_loaded":len(histories),"signals":signals.groupby("horizon").size().to_dict() if not signals.empty else {},"paths":len(paths),"limitations":{"TCT":"V24.1.7 price-executable T1 proxy; full baseline pillars and exact T2 state cannot be reconstructed from price history alone.","CT":"V21.0 price/technical core proxy renormalized on reconstructible PIT fields; fundamentals/consensus/news are not reconstructed.","MT":"V21.0 price/technical core proxy renormalized on reconstructible PIT fields; fundamentals/consensus are not reconstructed.","certification":"DESCRIPTIVE_RESEARCH_ONLY_NOT_FULL_MODEL_PIT_OOS"}}
    (outdir/"SUMMARY.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2)); return payload

if __name__=="__main__": run()
