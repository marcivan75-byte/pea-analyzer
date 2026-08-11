from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sys

from v182.io.frames import load_master
from v182.reporting.waves import resolve_etf_tickers
from v182.sources.yfinance_bulk import download_history
from v182.features.etf_mt_v2081 import (
    load_histories_from_cache,
    score_snapshot,
    write_outputs,
)

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "inputs"
CONFIG = ROOT / "config"
OUTPUTS = ROOT / "outputs" / "etf_mt_v2081"
CACHE = ROOT / "data" / "cache" / "etf_mt_v2081"


def run() -> dict:
    base_cfg = json.loads((CONFIG / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    mt_cfg = json.loads((CONFIG / "V20.8_ETF_MT_HIGH_PRECISION.json").read_text(encoding="utf-8"))

    etf_df = load_master(INPUTS / "V18.2_PEA_ETF_MASTER.csv")
    etf_with_tickers, gaps = resolve_etf_tickers(etf_df, CONFIG / "V18.2_ETF_TICKER_MAP.csv")
    valid = etf_with_tickers.dropna(subset=["yahoo_ticker"]).copy()
    valid = valid[valid["yahoo_ticker"].astype(str).str.strip().ne("")]

    tickers = valid["yahoo_ticker"].astype(str).tolist()
    if not tickers:
        raise RuntimeError("ETF_MT_V20_8_NO_VALID_TICKERS")

    yf_cfg = base_cfg["yfinance"]
    result = download_history(
        tickers=tickers,
        cache_dir=str(CACHE),
        period=yf_cfg.get("history_period", "5y"),
        interval=yf_cfg.get("interval", "1d"),
        batch_size=int(yf_cfg.get("etf_batch_size", 50)),
        auto_adjust=bool(yf_cfg.get("auto_adjust", True)),
    )

    ticker_to_isin = {
        str(ticker): str(isin)
        for ticker, isin in zip(valid["yahoo_ticker"], valid["isin"])
        if str(ticker).strip() and str(isin).strip()
    }
    histories = load_histories_from_cache(CACHE, ticker_to_isin)
    snapshot, summary = score_snapshot(histories, etf_with_tickers, mt_cfg)

    summary["run_id"] = os.environ.get("V2081_RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary["download"] = {
        "requested": result.requested,
        "successful": len(result.successful),
        "failed": len(result.failed),
        "ticker_mapping_gaps": int(len(gaps)),
    }
    summary["status"] = "ACTIVE_REFERENCE_SCORING"
    summary["real_orders_enabled"] = False

    paths = write_outputs(snapshot, summary, OUTPUTS)
    summary["outputs"] = paths

    print(
        "ETF MT V20.8.1 — "
        f"{summary['scorable_etfs']} ETF scorables, "
        f"regime_allowed={summary['regime']['allowed']}, "
        f"selected={len(summary['selected'])}"
    )
    for selected in summary["selected"]:
        print(
            f"SELECTED {selected['isin']} "
            f"score={selected['score_final']:.2f} "
            f"group={selected['exposure_group']}"
        )
    return summary


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"ECHEC ETF MT V20.8.1: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
