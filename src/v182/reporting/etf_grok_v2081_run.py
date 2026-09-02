from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

from v182.io.frames import load_master
from v182.reporting.selected_source_enrichment import attach_master_identity, enrich_selected_rows
from v182.reporting.waves import resolve_etf_tickers
from v182.sources.yfinance_bulk import download_history
from v182.features.etf_grok_v2081 import load_histories_from_cache, write_outputs
from v182.features.etf_grok_history_integrity import sanitize_histories, score_snapshot_integrity
from v182.features.etf_grok_v2082_dynamic import apply_dynamic_weighting

ROOT = Path(__file__).resolve().parents[3]


def _attach_selected_source_context(dynamic_snapshot, etf_with_tickers, root: Path):
    if "dynamic_selected" not in dynamic_snapshot:
        return dynamic_snapshot, {"status": "NO_DYNAMIC_SELECTION_COLUMN", "decision_influence": False, "score_influence": 0.0}
    selected = dynamic_snapshot[dynamic_snapshot["dynamic_selected"].fillna(False).astype(bool)].copy()
    if selected.empty:
        return dynamic_snapshot, {"status": "NO_PRESELECTED_ROWS", "decision_influence": False, "score_influence": 0.0}
    if "isin" not in selected.columns:
        if "instrument_id" not in selected.columns:
            raise RuntimeError("ETF_GROK_SELECTED_IDENTITY_MISSING")
        selected["isin"] = selected["instrument_id"].astype(str)
    selected["asset_class"] = "ETF"
    selected["horizon"] = "GROK"
    selected["decision"] = "SHADOW_CANDIDATE"
    selected = attach_master_identity(selected, None, etf_with_tickers)
    enriched, context = enrich_selected_rows(selected, root, profile="ETF_GROK")
    source_columns = [c for c in enriched.columns if c.startswith("investing_") or c.startswith("boursorama_")]
    if not source_columns:
        return dynamic_snapshot, context
    context_rows = enriched[["isin"] + source_columns].drop_duplicates("isin").rename(columns={"isin": "instrument_id"})
    if "instrument_id" not in dynamic_snapshot.columns:
        raise RuntimeError("ETF_GROK_DYNAMIC_IDENTITY_MISSING")
    return dynamic_snapshot.merge(context_rows, on="instrument_id", how="left"), context


def run(
    root: Path = ROOT,
    *,
    history_cache_dir: str | Path | None = None,
    refresh_history: bool = True,
    refresh_if_reuse_cache_missing: bool = False,
) -> dict:
    inputs = root / "inputs"
    config = root / "config"
    outputs = root / "outputs" / "etf_grok_v2081"
    cache = Path(history_cache_dir) if history_cache_dir is not None else root / "data" / "cache" / "etf_grok_v2081"

    base_cfg = json.loads((config / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    grok_cfg = json.loads((config / "V20.8_ETF_GROK_HIGH_PRECISION.json").read_text(encoding="utf-8"))
    dynamic_cfg = json.loads((config / "V20.8.2_ETF_GROK_DYNAMIC.json").read_text(encoding="utf-8"))

    etf_df = load_master(inputs / "V18.2_PEA_ETF_MASTER.csv")
    etf_with_tickers, gaps = resolve_etf_tickers(etf_df, config / "V18.2_ETF_TICKER_MAP.csv")
    valid = etf_with_tickers.dropna(subset=["yahoo_ticker"]).copy()
    valid = valid[valid["yahoo_ticker"].astype(str).str.strip().ne("")]
    tickers = valid["yahoo_ticker"].astype(str).tolist()
    if not tickers:
        raise RuntimeError("ETF_GROK_NO_VALID_TICKERS")

    yf_cfg = base_cfg["yfinance"]
    result = None
    reuse_fallback = False
    cache_has_history = any(cache.glob("history_*.parquet"))
    if refresh_history or (refresh_if_reuse_cache_missing and not cache_has_history):
        reuse_fallback = not refresh_history
        result = download_history(
            tickers=tickers,
            cache_dir=str(cache),
            period=yf_cfg.get("history_period", "5y"),
            interval=yf_cfg.get("interval", "1d"),
            batch_size=int(yf_cfg.get("etf_batch_size", 50)),
            auto_adjust=bool(yf_cfg.get("auto_adjust", True)),
            start=yf_cfg.get("history_start", "2023-01-01"),
            rolling_months=int(yf_cfg.get("history_rolling_months", 60)),
        )
    elif not cache_has_history:
        raise RuntimeError(f"ETF_GROK_REUSE_CACHE_EMPTY:{cache}")

    ticker_to_isin = {
        str(ticker): str(isin)
        for ticker, isin in zip(valid["yahoo_ticker"], valid["isin"])
        if str(ticker).strip() and str(isin).strip()
    }
    histories = load_histories_from_cache(cache, ticker_to_isin)
    if not histories:
        raise RuntimeError(f"ETF_GROK_NO_USABLE_HISTORY:{cache}")
    scoring_histories = sanitize_histories(histories)

    if result is None:
        download_summary = {
            "requested": len(tickers), "successful": len(histories), "failed": max(0, len(tickers) - len(histories)),
            "ticker_mapping_gaps": int(len(gaps)), "mode": "REUSED_ETF_GROK_CACHE", "network_collection_executed": False,
            "network_requests_avoided": len(tickers), "cache_dir": str(cache),
        }
    else:
        mode = "GROK_CACHE_MISS_INCREMENTAL_REFRESH" if reuse_fallback else "ETF_GROK_INCREMENTAL_CACHE_REFRESH"
        download_summary = {
            "requested": result.requested, "successful": len(result.successful), "failed": len(result.failed),
            "ticker_mapping_gaps": int(len(gaps)), "mode": mode, "network_collection_executed": True,
            "network_requests_avoided": 0, "cache_dir": str(cache),
        }

    strict_snapshot, strict_summary = score_snapshot_integrity(histories, etf_with_tickers, grok_cfg)
    run_id = os.environ.get("ETF_GROK_RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    strict_summary["run_id"] = run_id
    strict_summary["download"] = download_summary
    strict_summary["status"] = "ACTIVE_REFERENCE_SCORING_NO_REAL_ORDERS"
    strict_summary["process"] = "ETF_GROK"
    strict_paths = write_outputs(strict_snapshot, strict_summary, outputs)
    strict_summary["outputs"] = strict_paths

    dynamic_snapshot, dynamic_summary = apply_dynamic_weighting(strict_snapshot, scoring_histories, etf_with_tickers, grok_cfg, dynamic_cfg)
    dynamic_summary["promotion_allowed"] = False
    dynamic_summary["real_orders_allowed"] = False
    dynamic_summary["history_session_policy"] = "OBSERVED_NUMERIC_CLOSE_ONLY"
    dynamic_summary["run_id"] = run_id
    dynamic_summary["download"] = strict_summary["download"]
    dynamic_summary["strict_reference"] = {
        "version": strict_summary.get("version"),
        "scorable_etfs": strict_summary.get("scorable_etfs"),
        "selected": strict_summary.get("selected"),
        "historical_attribution": "source ETF MT parity only while GROK clone remains unchanged",
        "history_session_policy": strict_summary.get("history_session_policy"),
        "real_orders_allowed": False,
    }
    dynamic_snapshot, source_context = _attach_selected_source_context(dynamic_snapshot, etf_with_tickers, root)
    dynamic_summary["selected_source_context"] = source_context
    dynamic_summary["source_context_score_influence"] = 0.0
    dynamic_summary["source_context_weights_unchanged"] = True

    outputs.mkdir(parents=True, exist_ok=True)
    dynamic_csv = outputs / "V20.8.2_ETF_GROK_DYNAMIC_RANKING.csv"
    dynamic_json = outputs / "V20.8.2_ETF_GROK_DYNAMIC_SUMMARY.json"
    dynamic_snapshot.to_csv(dynamic_csv, sep=";", index=False, encoding="utf-8-sig")
    dynamic_json.write_text(json.dumps(dynamic_summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    dynamic_summary["outputs"] = {
        "ranking_csv": str(dynamic_csv), "summary_json": str(dynamic_json),
        "strict_reference_ranking": strict_paths["ranking_csv"], "strict_reference_summary": strict_paths["summary_json"],
    }

    print(
        f"ETF GROK — {dynamic_summary['scorable_etfs']} ETF scorables, "
        f"regime_allowed={dynamic_summary['regime']['allowed']}, selected={len(dynamic_summary['selected'])}; "
        "strict clone kept separately for parity attribution"
    )
    return dynamic_summary


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"ECHEC ETF GROK: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
