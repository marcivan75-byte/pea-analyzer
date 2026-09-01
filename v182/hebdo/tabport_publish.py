"""Construit et publie TABPORT enrichi à partir du cache OHLCV gouverné réel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from v182.hebdo.hebdo_at_meta import HebdoATMeta
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_historical import _tuple_columns_if_encoded, _validate_row_level_pit, _sha256_source
from v182.hebdo.tabport_enriched import publish_enriched

FIELDS = {"open", "high", "low", "close", "volume"}
PARIS = ZoneInfo("Europe/Paris")


def _wide_ohlcv_to_long(df: pd.DataFrame, source: str) -> pd.DataFrame:
    work = _tuple_columns_if_encoded(df)
    if not isinstance(work.columns, pd.MultiIndex) or work.columns.nlevels != 2:
        raise ValueError(f"BLOCK_TABPORT_PUBLISH: {source} unsupported OHLCV layout")
    l0 = {str(x).strip().lower() for x in work.columns.get_level_values(0)}
    l1 = {str(x).strip().lower() for x in work.columns.get_level_values(1)}
    if FIELDS.issubset(l1):
        ticker_level, field_level = 0, 1
    elif FIELDS.issubset(l0):
        ticker_level, field_level = 1, 0
    else:
        raise ValueError(f"BLOCK_TABPORT_PUBLISH: {source} missing OHLCV fields")
    dates = pd.to_datetime(work.index, errors="coerce", utc=True)
    if len(dates) == 0 or dates.isna().any():
        raise ValueError(f"BLOCK_TABPORT_PUBLISH: {source} invalid Date index")
    pieces = []
    for ticker in pd.Index(work.columns.get_level_values(ticker_level)).unique():
        cols = [c for c in work.columns if c[ticker_level] == ticker]
        by_field = {str(c[field_level]).strip().lower(): c for c in cols}
        if not FIELDS.issubset(by_field):
            continue
        part = pd.DataFrame({"date": dates, "ticker": str(ticker).strip().upper()})
        for field in ["open", "high", "low", "close", "volume"]:
            part[field] = pd.to_numeric(work[by_field[field]].to_numpy(), errors="coerce")
        part = part.dropna(subset=["open", "high", "low", "close", "volume"])
        part = part[(part[["open", "high", "low", "close"]] > 0).all(axis=1) & (part["volume"] >= 0)]
        consistent = (
            (part["low"] <= part["high"])
            & (part["open"] >= part["low"])
            & (part["open"] <= part["high"])
            & (part["close"] >= part["low"])
            & (part["close"] <= part["high"])
        )
        part = part[consistent].copy()
        if not part.empty:
            pieces.append(part)
    if not pieces:
        raise ValueError(f"BLOCK_TABPORT_PUBLISH: {source} no usable OHLCV")
    return pd.concat(pieces, ignore_index=True)


def read_cache(cache_dir: str | Path) -> tuple[pd.DataFrame, list[str]]:
    root = Path(cache_dir)
    files = sorted(root.glob("history_*.parquet"))
    if not files:
        raise ValueError(f"BLOCK_TABPORT_PUBLISH: no history parquet in {root}")
    parts = [_wide_ohlcv_to_long(pd.read_parquet(p), str(p)) for p in files]
    out = pd.concat(parts, ignore_index=True)
    if out.duplicated(["date", "ticker"]).any():
        raise ValueError("BLOCK_TABPORT_PUBLISH: duplicate ticker/date across cache blocks")
    return out.sort_values(["ticker", "date"]).reset_index(drop=True), [str(p) for p in files]


def _indicators_one(g: pd.DataFrame) -> pd.DataFrame:
    x = g.sort_values("date").copy()
    close = x["close"].astype(float); vol = x["volume"].astype(float)
    x["volume_avg20"] = vol.rolling(20, min_periods=20).mean()
    vstd = vol.rolling(20, min_periods=20).std()
    x["vol_z"] = (vol - x["volume_avg20"]) / vstd.replace(0, np.nan)
    x["sma20"] = close.rolling(20, min_periods=20).mean()
    x["sma200"] = close.rolling(200, min_periods=200).mean()
    prev = close.shift(1)
    tr = pd.concat([(x["high"] - x["low"]), (x["high"] - prev).abs(), (x["low"] - prev).abs()], axis=1).max(axis=1)
    x["atr_14_pct"] = tr.rolling(14, min_periods=14).mean() / close.replace(0, np.nan)
    x["drawdown_4w"] = close / close.rolling(20, min_periods=20).max() - 1
    x["adv_20m_eur"] = (close * vol).rolling(20, min_periods=20).mean()
    x["ret_1d"] = close.pct_change()
    x["B1_vol"] = (x["vol_z"] > 3.0) & (x["ret_1d"] < -0.015) & (close < x["sma20"])
    x["B2_daily"] = x["B1_vol"].shift(1).fillna(False).astype(bool)
    x["B_signal"] = x["B1_vol"] | x["B2_daily"]
    x["B_signal_type"] = np.where(x["B1_vol"], "B1_VOL", np.where(x["B2_daily"], "B2_DAILY_J+1", "NONE"))
    return x


def build_weekly_meta_signals(ohlcv: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    technical = pd.concat([_indicators_one(g) for _, g in ohlcv.groupby("ticker", sort=False)], ignore_index=True)
    technical = technical.sort_values(["date", "ticker"]).reset_index(drop=True)
    technical["week"] = technical["date"].dt.tz_localize(None).dt.to_period("W-FRI").astype(str)

    b_weeks = technical.loc[technical["B_signal"], ["week", "ticker", "B_signal_type"]].copy()
    if b_weeks.empty:
        raise ValueError("BLOCK_TABPORT_PUBLISH: no B candidates in governed history")
    b_weeks = b_weeks.sort_values(["week", "ticker"]).drop_duplicates(["week", "ticker"], keep="last")
    week_end = technical.sort_values("date").groupby(["week", "ticker"], as_index=False).tail(1)
    candidates = week_end.merge(b_weeks, on=["week", "ticker"], how="inner", suffixes=("", "_trigger"))
    if candidates.empty:
        raise ValueError("BLOCK_TABPORT_PUBLISH: no weekly B candidates")

    unique_dates = pd.Index(sorted(technical["date"].unique()))
    if len(unique_dates) <= 126:
        raise ValueError("BLOCK_TABPORT_PUBLISH: insufficient history for 126-session maturation")
    mature_market_cutoff = pd.Timestamp(unique_dates[-127])
    candidates = candidates[candidates["date"] <= mature_market_cutoff].copy()
    if candidates.empty:
        raise ValueError("BLOCK_TABPORT_PUBLISH: no matured weekly candidates")

    market_date = pd.to_datetime(candidates["date"], utc=True)
    candidates["market_snapshot_date"] = market_date
    candidates["date"] = market_date + pd.Timedelta(days=1)
    snapshots = []
    for ts in market_date:
        d = ts.tz_convert(PARIS).date()
        snapshots.append(pd.Timestamp(f"{d} 21:59:00", tz=PARIS).tz_convert("UTC"))
    candidates["pit_snapshot_time"] = snapshots
    candidates["mom_26w_sector"] = 0.0
    candidates["sector_momentum_status"] = "UNAVAILABLE_CONSERVATIVE_ZERO"
    candidates["signal_family"] = candidates.get("B_signal_type_trigger", candidates.get("B_signal_type", "B"))

    need = ["close", "sma200", "vol_z", "drawdown_4w", "atr_14_pct", "adv_20m_eur"]
    candidates = candidates.dropna(subset=need).copy()
    if candidates.empty:
        raise ValueError("BLOCK_TABPORT_PUBLISH: candidates invalid after technical warmup")

    ranked_parts = []
    meta_rejected_groups = 0
    meta_rejected_candidates = 0
    for decision, grp in candidates.groupby("date", sort=True):
        base = grp.copy()
        try:
            ranked = HebdoATMeta().run(base)
        except ValueError as exc:
            if "universe fully rejected by false-positive filter" in str(exc):
                meta_rejected_groups += 1
                meta_rejected_candidates += int(len(grp))
                continue
            raise
        ranked["date"] = decision
        ranked["pit_snapshot_time"] = grp.set_index("ticker").reindex(ranked["ticker"])["pit_snapshot_time"].to_numpy()
        ranked_parts.append(ranked)
    if not ranked_parts:
        raise ValueError("BLOCK_TABPORT_PUBLISH: Meta rejected every matured weekly candidate")
    signals = pd.concat(ranked_parts, ignore_index=True)
    signals = signals[signals["tier"].isin(["TCT", "CT_WATCH"]) & (pd.to_numeric(signals["EV_net"], errors="coerce") >= 0)].copy()
    signals = signals.sort_values(["date", "EV_net", "ticker"], ascending=[True, False, True]).reset_index(drop=True)
    if signals.empty:
        raise ValueError("BLOCK_TABPORT_PUBLISH: Meta produced no eligible signals")
    audit = {
        "source_rows_ohlcv": int(len(ohlcv)),
        "source_tickers": int(ohlcv["ticker"].nunique()),
        "weekly_B_candidates_matured": int(len(candidates)),
        "meta_rejected_groups": int(meta_rejected_groups),
        "meta_rejected_candidates": int(meta_rejected_candidates),
        "meta_signals_eligible": int(len(signals)),
        "first_signal": str(pd.to_datetime(signals["date"], utc=True).min()),
        "last_signal": str(pd.to_datetime(signals["date"], utc=True).max()),
        "mature_market_cutoff": str(mature_market_cutoff),
        "sector_momentum_policy": "UNAVAILABLE_CONSERVATIVE_ZERO",
        "signal_family": "B_v2_weekly_consolidated",
        "synthetic_features": False,
    }
    return signals, audit


def publish(cache_dir: str | Path, output_dir: str | Path) -> dict:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    ohlcv, files = read_cache(cache_dir)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    signal_path = out / "TABPORT_META_SIGNALS_MATURES.csv"
    signals.to_csv(signal_path, index=False)
    feature_tickers = set(signals["ticker"].astype(str))
    features = add_antifp_features(ohlcv[ohlcv["ticker"].astype(str).isin(feature_tickers)].copy())
    confirmed, confirmation_audit = apply_j1_confirmation(signals, features)
    if confirmed.empty:
        raise ValueError("BLOCK_TABPORT_PUBLISH: J1 confirmation rejected every signal")
    confirmed_path = out / "TABPORT_META_SIGNALS_CONFIRMES_J1.csv"
    confirmed.to_csv(confirmed_path, index=False)
    confirmation_audit.to_csv(out / "TABPORT_CONFIRMATION_J1_AUDIT.csv", index=False)
    start = str(pd.to_datetime(confirmed["date"], utc=True).min().date())
    end = str(pd.to_datetime(ohlcv["date"], utc=True).max().date())
    cfg = TabportConfig()
    pit_min, pit_max = _validate_row_level_pit(confirmed)
    needed_tickers = set(confirmed["ticker"].astype(str))
    prices = ohlcv[ohlcv["ticker"].astype(str).isin(needed_tickers)][["date", "ticker", "open", "high", "low", "close"]].copy()
    if prices.empty:
        raise ValueError("BLOCK_TABPORT_PUBLISH: no OHLC for selected Meta signals")
    result = Tabport65k(cfg).run(confirmed, prices)
    result["manifest"] = {
        "status": "OK",
        "engine": "TABPORT_HEBDO_AT_META_ENRICHI",
        "window": {"start": start, "end": end},
        "inputs": {
            "signals": {
                "path": str(confirmed_path), "rows": int(len(confirmed)),
                "source_min_date": str(pd.to_datetime(confirmed["date"], utc=True).min()),
                "source_max_date": str(pd.to_datetime(confirmed["date"], utc=True).max()),
                "pit_snapshot_min": str(pit_min), "pit_snapshot_max": str(pit_max),
                "pit_validation": "ROW_LEVEL_T_MINUS_1_22H_EUROPE_PARIS",
                "selection_policy": "META_ELIGIBLE_THEN_J1_CONFIRMATION",
                "pre_confirmation_path": str(signal_path),
                "pre_confirmation_rows": int(len(signals)),
            },
            "ohlc": {
                "path": str(cache_dir), "sha256": _sha256_source(Path(cache_dir)),
                "rows": int(len(prices)),
                "source_min_date": str(pd.to_datetime(prices["date"], utc=True).min()),
                "source_max_date": str(pd.to_datetime(prices["date"], utc=True).max()),
                "source_files": files,
                "layout_normalization": "GOVERNED_WIDE_CACHE_TO_LONG",
                "ohlc_quality_policy": "DROP_MALFORMED_BARS_FAIL_CLOSED_NO_REPAIR",
            },
        },
        "config": cfg.__dict__,
        "metrics": result["metrics"],
        "synthetic_fallback": False,
        "retuning": False,
        "publication": {
            "name": "TABPORT_ENRICHI",
            "signal_audit": signal_audit,
            "cache_files": files,
            "primary_cohort": "MATURED_126_SESSIONS_J1_CONFIRMED",
            "confirmation_counts": {str(k): int(v) for k, v in confirmation_audit["status"].value_counts(dropna=False).to_dict().items()},
            "rejected_components": ["EARLY_EXIT_ALL", "STOP_RISK_VETO", "STOP_RISK_RANKING_REPLACEMENT", "ATR_ADAPTIVE_STOP", "CLOSE09_HARD15"],
            "retained_components": ["DETERMINISTIC_FP_FILTER", "META_RANKING", "J1_CONFIRMATION", "FIXED_STOP_09"],
        },
    }
    enriched = publish_enriched(result, out, initial_cash=cfg.initial_cash)
    publication = {
        "status": "PUBLISHED",
        "name": "TABPORT_ENRICHI",
        "primary_cohort": "MATURED_126_SESSIONS_J1_CONFIRMED",
        "signal_audit": signal_audit,
        "confirmation_counts": {str(k): int(v) for k, v in confirmation_audit["status"].value_counts(dropna=False).to_dict().items()},
        "decision_chain": "B_V2_TO_META_TO_J1_CONFIRM_TO_FIXED09_TABPORT",
        "summary": enriched["summary"],
        "files": sorted(p.name for p in out.iterdir() if p.is_file()),
    }
    (out / "TABPORT_PUBLICATION.json").write_text(json.dumps(publication, indent=2, default=str), encoding="utf-8")
    print(json.dumps(publication, default=str))
    return publication


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default="data/cache/actions")
    p.add_argument("--output-dir", default="outputs/tabport_enriched")
    args = p.parse_args()
    publish(args.cache, args.output_dir)


if __name__ == "__main__":
    main()
