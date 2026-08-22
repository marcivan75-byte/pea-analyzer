from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sys

from v182.io.frames import load_master
from v182.reporting.waves import resolve_etf_tickers
from v182.sources.yfinance_bulk import download_history
from v182.features.etf_mt_v2081 import load_histories_from_cache, write_outputs
from v182.features.etf_mt_history_integrity import sanitize_histories, score_snapshot_integrity
from v182.features.etf_mt_v2082_dynamic import apply_dynamic_weighting

ROOT = Path(__file__).resolve().parents[3]


def run(
    root: Path = ROOT,
    *,
    history_cache_dir: str | Path | None = None,
    refresh_history: bool = True,
    refresh_if_reuse_cache_missing: bool = False,
) -> dict:
    inputs=root/"inputs"; config=root/"config"; outputs=root/"outputs"/"etf_mt_v2081"
    cache=Path(history_cache_dir) if history_cache_dir is not None else root/"data"/"cache"/"etf_mt_v2081"
    base_cfg=json.loads((config/"V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8")); mt_cfg=json.loads((config/"V20.8_ETF_MT_HIGH_PRECISION.json").read_text(encoding="utf-8")); dynamic_cfg=json.loads((config/"V20.8.2_ETF_MT_DYNAMIC.json").read_text(encoding="utf-8"))
    etf_df=load_master(inputs/"V18.2_PEA_ETF_MASTER.csv"); etf_with_tickers,gaps=resolve_etf_tickers(etf_df,config/"V18.2_ETF_TICKER_MAP.csv"); valid=etf_with_tickers.dropna(subset=["yahoo_ticker"]).copy(); valid=valid[valid["yahoo_ticker"].astype(str).str.strip().ne("")]
    tickers=valid["yahoo_ticker"].astype(str).tolist()
    if not tickers: raise RuntimeError("ETF_MT_V20_8_NO_VALID_TICKERS")
    yf_cfg=base_cfg["yfinance"]
    result=None; reuse_fallback=False
    cache_has_history=any(cache.glob("history_*.parquet"))
    if refresh_history or (refresh_if_reuse_cache_missing and not cache_has_history):
        reuse_fallback=not refresh_history
        result=download_history(tickers=tickers,cache_dir=str(cache),period=yf_cfg.get("history_period","5y"),interval=yf_cfg.get("interval","1d"),batch_size=int(yf_cfg.get("etf_batch_size",50)),auto_adjust=bool(yf_cfg.get("auto_adjust",True)),start=yf_cfg.get("history_start","2023-01-01"),rolling_months=int(yf_cfg.get("history_rolling_months",60)))
    elif not cache_has_history:
        raise RuntimeError(f"ETF_MT_V20_8_REUSE_CACHE_EMPTY:{cache}")
    ticker_to_isin={str(ticker):str(isin) for ticker,isin in zip(valid["yahoo_ticker"],valid["isin"]) if str(ticker).strip() and str(isin).strip()}; histories=load_histories_from_cache(cache,ticker_to_isin)
    if not histories:
        raise RuntimeError(f"ETF_MT_V20_8_NO_USABLE_HISTORY:{cache}")
    scoring_histories=sanitize_histories(histories)

    if result is None:
        download_summary={"requested":len(tickers),"successful":len(histories),"failed":max(0,len(tickers)-len(histories)),"ticker_mapping_gaps":int(len(gaps)),"mode":"REUSED_PRIMARY_ETF_CACHE","network_collection_executed":False,"network_requests_avoided":len(tickers),"cache_dir":str(cache)}
    else:
        mode="PRIMARY_CACHE_MISS_INCREMENTAL_REFRESH" if reuse_fallback else "ETF_MT_INCREMENTAL_CACHE_REFRESH"
        download_summary={"requested":result.requested,"successful":len(result.successful),"failed":len(result.failed),"ticker_mapping_gaps":int(len(gaps)),"mode":mode,"network_collection_executed":True,"network_requests_avoided":0,"cache_dir":str(cache)}

    strict_snapshot,strict_summary=score_snapshot_integrity(histories,etf_with_tickers,mt_cfg); run_id=os.environ.get("V2081_RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"); strict_summary["run_id"]=run_id; strict_summary["download"]=download_summary; strict_summary["status"]="ACTIVE_REFERENCE_SCORING_NO_REAL_ORDERS"; strict_paths=write_outputs(strict_snapshot,strict_summary,outputs); strict_summary["outputs"]=strict_paths

    dynamic_snapshot,dynamic_summary=apply_dynamic_weighting(strict_snapshot,scoring_histories,etf_with_tickers,mt_cfg,dynamic_cfg); dynamic_snapshot.loc[dynamic_snapshot["dynamic_selected"].fillna(False).astype(bool),"dynamic_decision"]="SHADOW_CANDIDATE"; dynamic_summary["promotion_allowed"]=False; dynamic_summary["real_orders_allowed"]=False; dynamic_summary["history_session_policy"]="OBSERVED_NUMERIC_CLOSE_ONLY"; dynamic_summary["run_id"]=run_id; dynamic_summary["download"]=strict_summary["download"]; dynamic_summary["strict_reference"]={"version":strict_summary.get("version"),"scorable_etfs":strict_summary.get("scorable_etfs"),"selected":strict_summary.get("selected"),"historical_attribution":"90.91% OOS 2021-2023 exact complete-38 only","history_session_policy":strict_summary.get("history_session_policy"),"real_orders_allowed":False}
    outputs.mkdir(parents=True,exist_ok=True); dynamic_csv=outputs/"V20.8.2_ETF_MT_DYNAMIC_RANKING.csv"; dynamic_json=outputs/"V20.8.2_ETF_MT_DYNAMIC_SUMMARY.json"; dynamic_snapshot.to_csv(dynamic_csv,sep=";",index=False,encoding="utf-8-sig"); dynamic_json.write_text(json.dumps(dynamic_summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); dynamic_summary["outputs"]={"ranking_csv":str(dynamic_csv),"summary_json":str(dynamic_json),"strict_reference_ranking":strict_paths["ranking_csv"],"strict_reference_summary":strict_paths["summary_json"]}

    print(f"ETF MT V20.8.2 — {dynamic_summary['scorable_etfs']} ETF scorables après renormalisation, regime_allowed={dynamic_summary['regime']['allowed']}, selected={len(dynamic_summary['selected'])}; V20.8.1 strict conservée pour attribution historique")
    return dynamic_summary


if __name__ == "__main__":
    try: run()
    except Exception as exc:
        print(f"ECHEC ETF MT V20.8.2: {type(exc).__name__}: {exc}",file=sys.stderr); raise
