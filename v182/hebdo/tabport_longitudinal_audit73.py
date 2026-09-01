"""Longitudinal TABPORT Audit73 study, 2010-2026.

This module replays the governed B_V2 -> META -> J1 -> TABPORT chain on quality-
controlled real OHLCV. Fundamental/consensus filters are applied before the
portfolio simulation. Missing non-replaceable criteria are *not imputed*: in the
operational comparison they pass through with an explicit unavailable counter,
per the study mandate, while strict comparable coverage is reported separately.

2010-2022 is development data. 2023+ is holdout/OOS and is never used to fit or
retune thresholds.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import math
import numpy as np
import pandas as pd

from v182.hebdo.meta_price_history import load_pre2023_development, load_holdout
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from src.v182.backtest.hebdo_meta_consensus_gate_audit73 import attach_latest_pit_snapshot

POSITIVE = {"BUY", "STRONG_BUY"}
ANALYST_THRESHOLDS = (5, 10, 15, 20)


def _read_csv_auto(path: Path) -> pd.DataFrame:
    for sep in (";", ","):
        try:
            frame = pd.read_csv(path, sep=sep, low_memory=False)
            if len(frame.columns) > 1:
                return frame
        except Exception:
            pass
    return pd.DataFrame()


def _quality_filter(frame: pd.DataFrame, segment: str) -> tuple[pd.DataFrame, dict]:
    need = ["date", "ticker", "open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in frame]
    if missing:
        raise ValueError(f"BLOCK_LONGITUDINAL_MISSING_OHLCV:{segment}:{missing}")
    x = frame[need].copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce", utc=True)
    x["ticker"] = x["ticker"].astype(str).str.strip().str.upper()
    for c in ["open", "high", "low", "close", "volume"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    before = len(x)
    duplicate_mask = x.duplicated(["date", "ticker"], keep=False)
    invalid = x["date"].isna() | x["ticker"].isin(["", "NAN", "NONE"])
    vals = x[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    invalid |= ~np.isfinite(vals).all(axis=1)
    invalid |= (x[["open", "high", "low", "close"]] <= 0).any(axis=1) | (x["volume"] < 0)
    invalid |= (x["low"] > x["high"]) | (x["open"] < x["low"]) | (x["open"] > x["high"])
    invalid |= (x["close"] < x["low"]) | (x["close"] > x["high"])
    rejected = invalid | duplicate_mask
    clean = x.loc[~rejected].sort_values(["ticker", "date"]).reset_index(drop=True)
    if clean.empty:
        raise ValueError(f"BLOCK_LONGITUDINAL_NO_RELIABLE_OHLCV:{segment}")
    audit = {
        "segment": segment,
        "rows_input": int(before),
        "rows_excluded_unreliable": int(rejected.sum()),
        "duplicate_rows_excluded": int(duplicate_mask.sum()),
        "rows_usable": int(len(clean)),
        "tickers_usable": int(clean["ticker"].nunique()),
        "min_date": str(clean["date"].min()),
        "max_date": str(clean["date"].max()),
        "imputation": False,
    }
    return clean, audit


def load_governed_ohlcv(pre2023: Path, manifest: Path, holdout_cache: Path) -> tuple[pd.DataFrame, list[dict]]:
    dev_raw = load_pre2023_development(pre2023, manifest)
    hold_raw = load_holdout(holdout_cache)
    dev, a = _quality_filter(dev_raw, "DEVELOPMENT_2010_2022")
    hold, b = _quality_filter(hold_raw, "HOLDOUT_2023_2026")
    if (dev["date"] >= pd.Timestamp("2023-01-01", tz="UTC")).any():
        raise ValueError("BLOCK_LONGITUDINAL_DEV_HOLDOUT_CONTAMINATION")
    if (hold["date"] < pd.Timestamp("2023-01-01", tz="UTC")).any():
        raise ValueError("BLOCK_LONGITUDINAL_HOLDOUT_PRE2023_CONTAMINATION")
    combined = pd.concat([dev, hold], ignore_index=True)
    dup = combined.duplicated(["date", "ticker"], keep=False)
    if dup.any():
        # Cross-corpus duplicates are not trusted; remove both sides rather than choose.
        combined = combined.loc[~dup].copy()
        b["cross_segment_duplicate_rows_excluded"] = int(dup.sum())
    return combined.sort_values(["ticker", "date"]).reset_index(drop=True), [a, b]


def _mapping_from_master(path: Path) -> dict[str, str]:
    frame = _read_csv_auto(path)
    if frame.empty or "isin" not in frame.columns:
        return {}
    ticker_col = next((c for c in ("yahoo_ticker", "ticker", "symbol") if c in frame.columns), None)
    if ticker_col is None:
        return {}
    out = {}
    for _, row in frame[["isin", ticker_col]].dropna().iterrows():
        isin = str(row["isin"]).strip().upper(); ticker = str(row[ticker_col]).strip().upper()
        if isin and ticker and ticker not in {"NAN", "NONE"}:
            out[isin] = ticker
    return out


def load_consensus_snapshots(snapshot_root: Path, master_mapping: Path) -> tuple[pd.DataFrame, dict]:
    mapping = _mapping_from_master(master_mapping)
    long_parts = []
    source_files = []
    for path in sorted(snapshot_root.rglob("*SOURCE_OBSERVATIONS.csv")) if snapshot_root.exists() else []:
        f = _read_csv_auto(path)
        if f.empty or "isin" not in f or "field" not in f or "value" not in f:
            continue
        stamp_col = next((c for c in ("collected_at", "fetched_at_utc", "available_at") if c in f.columns), None)
        if stamp_col is None:
            continue
        f = f.copy()
        f["available_at"] = pd.to_datetime(f[stamp_col], errors="coerce", utc=True)
        f = f.dropna(subset=["available_at"])
        if f.empty:
            continue
        f["symbol"] = f["isin"].astype(str).str.strip().str.upper().map(mapping)
        f = f.dropna(subset=["symbol"])
        wanted = {
            "boursorama_target_median": "target_median",
            "boursorama_consensus": "consensus",
            "boursorama_n_analysts": "n_analysts",
            "boursorama_consensus_delta_4w": "consensus_delta_4w",
        }
        f = f[f["field"].isin(wanted)].copy()
        if f.empty:
            continue
        f["canonical_field"] = f["field"].map(wanted)
        long_parts.append(f[["isin", "symbol", "available_at", "canonical_field", "value"]])
        source_files.append(str(path))
    if not long_parts:
        return pd.DataFrame(columns=["symbol","available_at","target_median","consensus","n_analysts","consensus_delta_4w","period_kind"]), {
            "usable_snapshots": 0, "source_files": 0, "mapping_isins": len(mapping), "status": "NO_CERTIFIED_PIT_CONSENSUS"
        }
    long = pd.concat(long_parts, ignore_index=True)
    # Keep each real collection event distinct. Identical repeated observations may
    # occur across fields; pivot only within the same ISIN/symbol/timestamp.
    piv = long.pivot_table(index=["isin","symbol","available_at"], columns="canonical_field", values="value", aggfunc="last").reset_index()
    for c in ("target_median", "n_analysts", "consensus_delta_4w"):
        if c not in piv: piv[c] = np.nan
        piv[c] = pd.to_numeric(piv[c], errors="coerce")
    if "consensus" not in piv: piv["consensus"] = np.nan
    piv["consensus"] = piv["consensus"].astype(str).str.upper().str.strip().replace({"NAN": np.nan, "NONE": np.nan})
    piv["period_kind"] = "CURRENT"
    piv = piv.sort_values(["symbol", "available_at"]).drop_duplicates(["symbol","available_at"], keep="last")
    return piv, {
        "usable_snapshots": int(len(piv)),
        "symbols": int(piv["symbol"].nunique()),
        "source_files": len(source_files),
        "mapping_isins": len(mapping),
        "min_available_at": None if piv.empty else str(piv["available_at"].min()),
        "max_available_at": None if piv.empty else str(piv["available_at"].max()),
        "status": "OK" if len(piv) else "NO_CERTIFIED_PIT_CONSENSUS",
        "relative_factset_dates_fabricated": False,
    }


def _next_entry_price(signals: pd.DataFrame, prices: pd.DataFrame, cfg: TabportConfig) -> pd.DataFrame:
    p = prices.sort_values(["ticker","date"])
    by_ticker = {t: g[["date","open"]].sort_values("date") for t,g in p.groupby("ticker")}
    rows = []
    for idx, sig in signals.reset_index(drop=True).iterrows():
        ticker = str(sig["ticker"]).upper(); d = pd.to_datetime(sig["date"], utc=True)
        g = by_ticker.get(ticker)
        if g is None:
            continue
        nxt = g[g["date"] > d].head(1)
        if nxt.empty:
            continue
        entry_price = float(nxt.iloc[0]["open"]) * (1.0 + cfg.slippage_rate)
        rows.append({"_signal_row": idx, "symbol": ticker, "decision_at": d, "entry_price": entry_price, "return_pct": 0.0})
    return pd.DataFrame(rows)


def _criterion_masks(signals: pd.DataFrame, prices: pd.DataFrame, observations: pd.DataFrame, cfg: TabportConfig) -> tuple[dict[str, pd.Series], dict[str, dict]]:
    base_mask = pd.Series(True, index=signals.index)
    diagnostics = {}
    if observations.empty:
        masks = {"BASELINE": base_mask}
        for name in ["TARGET_GT20", "TARGET_GT20_POSITIVE", "TARGET_GT20_POSITIVE_IMPROVING"] + [f"TARGET_GT20_POSITIVE_IMPROVING_ANALYSTS_GE_{x}" for x in ANALYST_THRESHOLDS]:
            masks[name] = base_mask.copy()
            diagnostics[name] = {"criterion_available_signals": 0, "criterion_unavailable_pass_through": int(len(signals)), "filter_rejections": 0, "policy": "PASS_THROUGH_UNAVAILABLE_NO_IMPUTATION"}
        diagnostics["BASELINE"] = {"criterion_available_signals": int(len(signals)), "criterion_unavailable_pass_through": 0, "filter_rejections": 0}
        return masks, diagnostics

    candidates = _next_entry_price(signals, prices, cfg)
    if candidates.empty:
        raise ValueError("BLOCK_LONGITUDINAL_NO_SIGNAL_ENTRY_PRICE")
    joined = attach_latest_pit_snapshot(candidates, observations)
    joined = joined.set_index("_signal_row").reindex(signals.index)
    has_snapshot = joined["pit_snapshot_available"].fillna(False)
    target_av = has_snapshot & joined["pit_target_median"].notna()
    target_pass = ~target_av | joined["pit_target_upside_pct"].gt(20.0)
    cons_av = has_snapshot & joined["pit_consensus"].notna()
    cons_pass = ~cons_av | joined["pit_consensus"].isin(POSITIVE)
    rev_av = has_snapshot & joined["pit_consensus_delta_4w"].notna()
    rev_pass = ~rev_av | joined["pit_consensus_delta_4w"].gt(0.0)
    analyst_av = has_snapshot & joined["pit_n_analysts"].notna()

    masks = {"BASELINE": base_mask, "TARGET_GT20": target_pass, "TARGET_GT20_POSITIVE": target_pass & cons_pass, "TARGET_GT20_POSITIVE_IMPROVING": target_pass & cons_pass & rev_pass}
    diagnostics["BASELINE"] = {"criterion_available_signals": int(len(signals)), "criterion_unavailable_pass_through": 0, "filter_rejections": 0}
    diagnostics["TARGET_GT20"] = {"criterion_available_signals": int(target_av.sum()), "criterion_unavailable_pass_through": int((~target_av).sum()), "filter_rejections": int((target_av & ~target_pass).sum())}
    diagnostics["TARGET_GT20_POSITIVE"] = {"criterion_available_signals": int((target_av & cons_av).sum()), "criterion_unavailable_pass_through": int((~target_av | ~cons_av).sum()), "filter_rejections": int((~(target_pass & cons_pass) & (target_av | cons_av)).sum())}
    diagnostics["TARGET_GT20_POSITIVE_IMPROVING"] = {"criterion_available_signals": int((target_av & cons_av & rev_av).sum()), "criterion_unavailable_pass_through": int((~target_av | ~cons_av | ~rev_av).sum()), "filter_rejections": int((~(target_pass & cons_pass & rev_pass) & (target_av | cons_av | rev_av)).sum())}
    for threshold in ANALYST_THRESHOLDS:
        name = f"TARGET_GT20_POSITIVE_IMPROVING_ANALYSTS_GE_{threshold}"
        analyst_pass = ~analyst_av | joined["pit_n_analysts"].ge(threshold)
        masks[name] = target_pass & cons_pass & rev_pass & analyst_pass
        diagnostics[name] = {"criterion_available_signals": int((target_av & cons_av & rev_av & analyst_av).sum()), "criterion_unavailable_pass_through": int((~target_av | ~cons_av | ~rev_av | ~analyst_av).sum()), "filter_rejections": int((~masks[name] & (target_av | cons_av | rev_av | analyst_av)).sum())}
    for d in diagnostics.values():
        d["policy"] = "PASS_THROUGH_UNAVAILABLE_NO_IMPUTATION"
    return masks, diagnostics


def _stability_from_yearly(yearly: pd.DataFrame) -> dict:
    if yearly.empty or "rendement_portefeuille_pct" not in yearly:
        return {"years": 0, "positive_years": 0, "return_mean_pct": None, "return_std_pct": None, "worst_year_pct": None}
    s = pd.to_numeric(yearly["rendement_portefeuille_pct"], errors="coerce").dropna()
    return {
        "years": int(len(s)), "positive_years": int((s > 0).sum()),
        "return_mean_pct": None if s.empty else float(s.mean()),
        "return_std_pct": None if len(s) < 2 else float(s.std(ddof=0)),
        "worst_year_pct": None if s.empty else float(s.min()),
    }


def run_study(pre2023: Path, manifest: Path, holdout_cache: Path, snapshots: Path, mapping_master: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_governed_ohlcv(pre2023, manifest, holdout_cache)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    features = add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed, confirmation_audit = apply_j1_confirmation(signals, features)
    if confirmed.empty:
        raise ValueError("BLOCK_LONGITUDINAL_NO_J1_CONFIRMED_SIGNALS")
    observations, consensus_audit = load_consensus_snapshots(snapshots, mapping_master)
    cfg = TabportConfig()
    masks, criterion_diag = _criterion_masks(confirmed.reset_index(drop=True), ohlcv, observations, cfg)

    models = {}
    q_rows = []; y_rows = []
    ledgers = []
    for model, mask in masks.items():
        chosen = confirmed.reset_index(drop=True).loc[mask.fillna(True)].copy()
        if chosen.empty:
            models[model] = {"status": "NO_SIGNALS_AFTER_FILTER", "criterion": criterion_diag[model]}
            continue
        result = Tabport65k(cfg).run(chosen, ohlcv[["date","ticker","open","high","low","close"]])
        ledger = result["ledger"].copy(); nav = result["equity"].copy()
        ledger["model"] = model; ledgers.append(ledger)
        summary = overall_summary(ledger, nav, initial_cash=cfg.initial_cash)
        q = period_table(ledger, nav, "Q"); y = period_table(ledger, nav, "Y")
        if not q.empty:
            q = q[q["periode"].astype(str).str[:4].astype(int).between(2023, 2026)].copy(); q.insert(0,"model",model); q_rows.append(q)
        if not y.empty:
            years = pd.to_numeric(y["periode"].astype(str).str[:4], errors="coerce")
            y = y[years.between(2010, 2023)].copy(); y.insert(0,"model",model); y_rows.append(y)
        models[model] = {"status":"OK", "overall":summary, "stability_2010_2023":_stability_from_yearly(y), "criterion":criterion_diag[model], "signals_selected":int(len(chosen)), "trades":int(len(ledger))}

    quarterly = pd.concat(q_rows, ignore_index=True) if q_rows else pd.DataFrame()
    yearly = pd.concat(y_rows, ignore_index=True) if y_rows else pd.DataFrame()
    all_ledgers = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    quarterly.to_csv(output_dir / "TABPORT_VARIANTS_2023_2026_TRIMESTRIEL.csv", index=False)
    yearly.to_csv(output_dir / "TABPORT_VARIANTS_2010_2023_ANNUEL.csv", index=False)
    all_ledgers.to_csv(output_dir / "TABPORT_VARIANTS_LEDGERS.csv", index=False)
    confirmed.to_csv(output_dir / "TABPORT_CONFIRMED_SIGNALS_LONGITUDINAL.csv", index=False)
    observations.to_csv(output_dir / "TABPORT_CERTIFIED_CONSENSUS_PIT.csv", index=False)
    confirmation_audit.to_csv(output_dir / "TABPORT_CONFIRMATION_J1_AUDIT.csv", index=False)

    # Descriptive selection only; no threshold/model retuning is performed. Rank by
    # robust dimensions: total return, PF, worst year, positive-year count, and
    # lower annual return dispersion. Missing dimensions receive no advantage.
    rank_rows = []
    for model, payload in models.items():
        if payload.get("status") != "OK": continue
        ov = payload["overall"]; st = payload["stability_2010_2023"]
        rank_rows.append({"model":model, "total_return_pct":ov.get("rendement_total_depuis_65000_pct"), "profit_factor":ov.get("profit_factor"), "max_drawdown_pct":ov.get("drawdown_max_pct"), "positive_years":st.get("positive_years"), "worst_year_pct":st.get("worst_year_pct"), "annual_return_std_pct":st.get("return_std_pct")})
    ranking = pd.DataFrame(rank_rows)
    if not ranking.empty:
        score = pd.Series(0.0, index=ranking.index)
        for col, asc in (("total_return_pct",False),("profit_factor",False),("max_drawdown_pct",False),("positive_years",False),("worst_year_pct",False),("annual_return_std_pct",True)):
            vals = pd.to_numeric(ranking[col], errors="coerce")
            score += vals.rank(method="average", ascending=asc, na_option="bottom")
        ranking["descriptive_rank_sum"] = score
        ranking = ranking.sort_values(["descriptive_rank_sum","model"]).reset_index(drop=True)
        ranking["descriptive_rank"] = np.arange(1,len(ranking)+1)
    ranking.to_csv(output_dir / "TABPORT_MODEL_STABILITY_RANKING.csv", index=False)
    best = None if ranking.empty else str(ranking.iloc[0]["model"])

    payload = {
        "status":"SUCCESS",
        "version":"TABPORT_LONGITUDINAL_AUDIT73_V1",
        "governance":{
            "development":"2010-2022", "holdout_oos":"2023-2026", "retuning_on_holdout":False,
            "incomplete_unreliable_rows_excluded":True, "synthetic_imputation":False,
            "missing_nonreplaceable_criterion_policy":"PASS_THROUGH_WITH_EXPLICIT_COMMENT",
            "current_consensus_backfill_forbidden":True, "factset_relative_dates_fabricated":False,
            "model_selection":"DESCRIPTIVE_ONLY_NOT_PRODUCTION_PROMOTION",
        },
        "quality":quality,
        "signal_audit":signal_audit,
        "consensus_audit":consensus_audit,
        "models":models,
        "best_observed_descriptive_model":best,
        "limitations":[
            "HISTORICAL_UNIVERSE_NOT_SURVIVORSHIP_SAFE",
            "HISTORICAL_PEA_ELIGIBILITY_NOT_CERTIFIED",
            "BOURSORAMA_NATIVE_COLLECTION_BEGAN_2026_08_22",
            "CONSENSUS_VARIANTS_BEFORE_CERTIFIED_PIT_COVERAGE_PASS_THROUGH_UNCHANGED",
            "BEST_OBSERVED_MODEL_IS_NOT_AUTOMATICALLY_PRODUCTION_APPROVED",
        ],
    }
    (output_dir / "TABPORT_LONGITUDINAL_AUDIT73_SUMMARY.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return payload


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--pre2023",required=True); p.add_argument("--manifest",required=True)
    p.add_argument("--holdout-cache",required=True); p.add_argument("--snapshots",required=True)
    p.add_argument("--mapping-master",required=True); p.add_argument("--output-dir",required=True)
    a=p.parse_args()
    payload=run_study(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.snapshots),Path(a.mapping_master),Path(a.output_dir))
    print(json.dumps({"status":payload["status"],"best":payload["best_observed_descriptive_model"],"consensus":payload["consensus_audit"]},indent=2,default=str))

if __name__ == "__main__": main()
