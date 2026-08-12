from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sys

from v182.io.frames import load_master
from v182.reporting.waves import resolve_etf_tickers
from v182.sources.yfinance_bulk import download_history
from v182.features.etf_mt_v2081 import load_histories_from_cache, score_snapshot, write_outputs
from v182.features.etf_mt_v2082_dynamic import apply_dynamic_weighting

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "inputs"
CONFIG = ROOT / "config"
OUTPUTS = ROOT / "outputs" / "etf_mt_v2081"
CACHE = ROOT / "data" / "cache" / "etf_mt_v2081"


def run() -> dict:
    base_cfg=json.loads((CONFIG/"V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8")); mt_cfg=json.loads((CONFIG/"V20.8_ETF_MT_HIGH_PRECISION.json").read_text(encoding="utf-8")); dynamic_cfg=json.loads((CONFIG/"V20.8.2_ETF_MT_DYNAMIC.json").read_text(encoding="utf-8"))
    etf_df=load_master(INPUTS/"V18.2_PEA_ETF_MASTER.csv"); etf_with_tickers,gaps=resolve_etf_tickers(etf_df,CONFIG/"V18.2_ETF_TICKER_MAP.csv"); valid=etf_with_tickers.dropna(subset=["yahoo_ticker"]).copy(); valid=valid[valid["yahoo_ticker"].astype(str).str.strip().ne("")]
    tickers=valid["yahoo_ticker"].astype(str).tolist()
    if not tickers: raise RuntimeError("ETF_MT_V20_8_NO_VALID_TICKERS")
    yf_cfg=base_cfg["yfinance"]; result=download_history(tickers=tickers,cache_dir=str(CACHE),period=yf_cfg.get("history_period","5y"),interval=yf_cfg.get("interval","1d"),batch_size=int(yf_cfg.get("etf_batch_size",50)),auto_adjust=bool(yf_cfg.get("auto_adjust",True)))
    ticker_to_isin={str(ticker):str(isin) for ticker,isin in zip(valid["yahoo_ticker"],valid["isin"]) if str(ticker).strip() and str(isin).strip()}; histories=load_histories_from_cache(CACHE,ticker_to_isin)

    strict_snapshot,strict_summary=score_snapshot(histories,etf_with_tickers,mt_cfg); run_id=os.environ.get("V2081_RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"); strict_summary["run_id"]=run_id; strict_summary["download"]={"requested":result.requested,"successful":len(result.successful),"failed":len(result.failed),"ticker_mapping_gaps":int(len(gaps))}; strict_summary["status"]="ACTIVE_REFERENCE_SCORING"; strict_summary["real_orders_enabled"]=False; strict_paths=write_outputs(strict_snapshot,strict_summary,OUTPUTS); strict_summary["outputs"]=strict_paths

    dynamic_snapshot,dynamic_summary=apply_dynamic_weighting(strict_snapshot,histories,etf_with_tickers,mt_cfg,dynamic_cfg); dynamic_summary["run_id"]=run_id; dynamic_summary["download"]=strict_summary["download"]; dynamic_summary["strict_reference"]={"version":strict_summary.get("version"),"scorable_etfs":strict_summary.get("scorable_etfs"),"selected":strict_summary.get("selected"),"historical_attribution":"90.91% OOS 2021-2023 exact complete-38 only"}
    OUTPUTS.mkdir(parents=True,exist_ok=True); dynamic_csv=OUTPUTS/"V20.8.2_ETF_MT_DYNAMIC_RANKING.csv"; dynamic_json=OUTPUTS/"V20.8.2_ETF_MT_DYNAMIC_SUMMARY.json"; dynamic_snapshot.to_csv(dynamic_csv,sep=";",index=False,encoding="utf-8-sig"); dynamic_json.write_text(json.dumps(dynamic_summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); dynamic_summary["outputs"]={"ranking_csv":str(dynamic_csv),"summary_json":str(dynamic_json),"strict_reference_ranking":strict_paths["ranking_csv"],"strict_reference_summary":strict_paths["summary_json"]}

    print(f"ETF MT V20.8.2 — {dynamic_summary['scorable_etfs']} ETF scorables après renormalisation, regime_allowed={dynamic_summary['regime']['allowed']}, selected={len(dynamic_summary['selected'])}; V20.8.1 strict conservée pour attribution historique")
    return dynamic_summary


if __name__ == "__main__":
    try: run()
    except Exception as exc:
        print(f"ECHEC ETF MT V20.8.2: {type(exc).__name__}: {exc}",file=sys.stderr); raise
