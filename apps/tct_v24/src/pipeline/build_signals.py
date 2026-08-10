"""Build the TCT signal frame from real data sources.

Priority order:
1. Repository/explicit Free Capture snapshot (preferred integration contract).
2. Optional native universe -> OHLCV -> indicators -> exact T1/T2 chain.

No synthetic fallback is performed here. Exact T1/T2 detection requires OHLCV
history; a one-row Free Capture snapshot is never treated as sufficient evidence.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.data.features import compute_technical_indicators
from src.data.input_contract import validate_signal_contract
from src.data.repo_adapter import adapt_repo_free_capture
from src.signals.earnings_proximity import score_earnings_proximity
from src.signals.t1_t2 import check_tct_with_bonus
from src.utils.logger import setup_logger
from src.utils.persistence import load_last_t1, save_last_t1

logger = setup_logger("build_signals")


def _read_delimited(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        first = path.open("r", encoding="utf-8-sig", errors="replace").readline()
        sep = ";" if first.count(";") > first.count(",") else ","
        return pd.read_csv(path, sep=sep, dtype=object, encoding="utf-8-sig", low_memory=False)
    raise ValueError(f"Format non supporté: {path.suffix}")


def read_signal_snapshot(config: dict) -> pd.DataFrame:
    """Read the real Free Capture/native snapshot and fail closed if absent."""
    paths_cfg = config.get("paths", {})
    processed_dir = Path(paths_cfg.get("processed", "data/processed/"))
    explicit = os.getenv("TCT_FREE_CAPTURE_PATH") or paths_cfg.get("free_capture")

    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([
        processed_dir / "latest_signals.parquet",
        processed_dir / "latest_signals.csv",
    ])

    seen = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.exists():
            continue
        try:
            df = _read_delimited(path)
            logger.info(f"Snapshot réel chargé: {len(df)} lignes ({path})")
            return df
        except Exception as exc:
            logger.warning(f"Lecture snapshot impossible ({path}): {exc}")

    searched = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"Free Capture absent ({searched})")


def _refresh_exact_t1_t2(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Optionally compute exact T1/T2 from OHLCV history for a bounded subset.

    This is intentionally opt-in because the repository Free Capture is the source
    of truth and a hidden mass Yahoo refresh inside scoring would destroy run
    reproducibility. Set ``t1_t2.network_refresh.enabled`` explicitly when desired.
    """
    refresh_cfg = config.get("t1_t2", {}).get("network_refresh", {})
    enabled = bool(refresh_cfg.get("enabled", False))
    if not enabled or df is None or df.empty:
        return df

    out = df.copy()
    max_symbols = int(refresh_cfg.get("max_symbols", 0) or 0)
    period = str(refresh_cfg.get("period", "1y"))
    interval = str(refresh_cfg.get("interval", "1d"))
    ttl_sessions = int(config.get("t1_t2", {}).get("t1_ttl_sessions", 40) or 40)
    persistence_path = str(config.get("paths", {}).get("persistence", "data/persistence/last_T1_bandwidth.json"))

    work = out.copy()
    if "universe_status" in work.columns:
        work = work[work["universe_status"].astype(str).str.upper().eq("PASS")]
    rank_col = next((c for c in ("score_ct", "score_final", "score_ct_raw") if c in work.columns), None)
    if rank_col:
        work = work.assign(_rank=pd.to_numeric(work[rank_col], errors="coerce")).sort_values("_rank", ascending=False)
    if max_symbols > 0:
        work = work.head(max_symbols)

    from src.data.loader import DataLoader
    loader = DataLoader(finnhub_key=os.getenv("FINNHUB_API_KEY"), cache_dir=str(refresh_cfg.get("cache_dir", "data/raw/ohlcv")))
    last_t1 = load_last_t1(persistence_path, ttl_sessions=ttl_sessions)
    state = dict(last_t1)
    refreshed = 0
    t1_count = 0
    t2_count = 0

    for idx, row in work.iterrows():
        ticker = str(row.get("ticker") or row.get("symbol") or "").strip()
        isin = str(row.get("isin") or "").strip().upper()
        if not ticker or not isin:
            continue
        hist = loader.download_ohlcv(ticker, period=period, interval=interval)
        if hist is None or hist.empty:
            continue
        try:
            tech = compute_technical_indicators(hist)
            det = check_tct_with_bonus(
                tech,
                last_T1_bandwidth=state.get(isin),
                ratio=float(config.get("t1_t2", {}).get("ratio_t2_vs_t1", 1.07)),
                bonus_t1=int(config.get("t1_t2", {}).get("bonus_t1", 15)),
                bonus_t2=int(config.get("t1_t2", {}).get("bonus_t2", 30)),
            )
        except Exception as exc:
            logger.warning(f"T1/T2 exact ignoré {ticker}: {exc}")
            continue

        latest = tech.iloc[-1]
        out.at[idx, "setup"] = det.get("setup")
        out.at[idx, "bonus"] = float(det.get("bonus") or 0)
        out.at[idx, "setup_source"] = "EXACT_OHLCV_HISTORY"
        for src, dst in (
            ("bandwidth", "bandwidth"), ("rsi", "rsi"), ("macd", "macd"),
            ("macd_signal", "macd_signal"), ("stoch_k", "stoch_k"),
            ("stoch_d", "stoch_d"), ("bb_high", "bb_high"),
            ("bb_low", "bb_low"), ("bb_mid", "bb_mid"),
            ("atr_pct", "atr_pct"), ("vol_ratio", "vol_ratio"),
            ("mm50", "mm50"), ("sar", "sar"), ("close", "close"),
        ):
            if src in latest.index:
                out.at[idx, dst] = latest.get(src)
        if "volume" in tech.columns and "close" in tech.columns:
            adv = (pd.to_numeric(tech["volume"], errors="coerce") * pd.to_numeric(tech["close"], errors="coerce")).rolling(20).mean().iloc[-1]
            if pd.notna(adv):
                out.at[idx, "avg_dollar_volume_20d"] = float(adv)

        if det.get("setup") == "T1" and pd.notna(det.get("current_bandwidth")):
            state[isin] = float(det["current_bandwidth"])
            t1_count += 1
        elif det.get("setup") == "T2_CONFIRMATION":
            state.pop(isin, None)  # T2 consumes the preceding T1 state.
            t2_count += 1
        refreshed += 1

    save_last_t1(state, persistence_path)
    logger.info(f"Refresh exact T1/T2: {refreshed} historiques | T1={t1_count} | T2={t2_count}")
    return out


def _build_native_from_universe(config: dict) -> pd.DataFrame:
    """Full native real-data chain for standalone deployments.

    The repository integration normally does not use this path because V21.1 Free
    Capture already provides the canonical universe and market snapshot.
    """
    native_cfg = config.get("runtime", {})
    if not bool(native_cfg.get("allow_network_build_signals", False)):
        raise FileNotFoundError("Snapshot absent et construction réseau native désactivée")

    universe_path = Path(config.get("paths", {}).get("universe", "data/raw/universe.csv"))
    if not universe_path.exists():
        raise FileNotFoundError(f"Univers natif absent: {universe_path}")
    universe = _read_delimited(universe_path)
    if "isin" not in universe.columns or not ({"ticker", "symbol"} & set(universe.columns)):
        raise ValueError("universe.csv doit contenir isin et ticker/symbol")

    from src.data.loader import DataLoader
    loader = DataLoader(finnhub_key=os.getenv("FINNHUB_API_KEY"), cache_dir="data/raw/ohlcv")
    persistence_path = str(config.get("paths", {}).get("persistence", "data/persistence/last_T1_bandwidth.json"))
    ttl_sessions = int(config.get("t1_t2", {}).get("t1_ttl_sessions", 40) or 40)
    state = load_last_t1(persistence_path, ttl_sessions=ttl_sessions)
    rows = []

    for _, u in universe.iterrows():
        ticker = str(u.get("ticker") or u.get("symbol") or "").strip()
        isin = str(u.get("isin") or "").strip().upper()
        if not ticker or not isin:
            continue
        hist = loader.download_ohlcv(ticker)
        if hist is None or hist.empty:
            continue
        try:
            tech = compute_technical_indicators(hist)
            det = check_tct_with_bonus(
                tech,
                last_T1_bandwidth=state.get(isin),
                ratio=float(config.get("t1_t2", {}).get("ratio_t2_vs_t1", 1.07)),
                bonus_t1=int(config.get("t1_t2", {}).get("bonus_t1", 15)),
                bonus_t2=int(config.get("t1_t2", {}).get("bonus_t2", 30)),
            )
        except Exception as exc:
            logger.warning(f"Indicateurs/T1T2 échoués {ticker}: {exc}")
            continue

        earnings = loader.get_earnings_info(ticker)
        latest = tech.iloc[-1]
        days = earnings.get("days_to_earnings", np.nan)
        eps = earnings.get("eps_revision_3m", np.nan)
        beat = earnings.get("beat_rate", np.nan)
        short = pd.to_numeric(pd.Series([u.get("short_interest")]), errors="coerce").iloc[0]
        adv = (pd.to_numeric(tech["volume"], errors="coerce") * pd.to_numeric(tech["close"], errors="coerce")).rolling(20).mean().iloc[-1]

        row = dict(u)
        row.update({
            "ticker": ticker,
            "isin": isin,
            "close": float(latest.get("close")),
            "avg_dollar_volume_20d": float(adv) if pd.notna(adv) else np.nan,
            "days_to_earnings": days,
            "eps_revision_3m": eps,
            "beat_rate": beat,
            "setup": det.get("setup"),
            "bonus": float(det.get("bonus") or 0),
            "setup_source": "EXACT_OHLCV_HISTORY",
            "score_earnings_proximity": score_earnings_proximity(days, eps, beat, short),
            "bandwidth": latest.get("bandwidth"),
            "rsi": latest.get("rsi"),
            "macd": latest.get("macd"),
            "macd_signal": latest.get("macd_signal"),
            "stoch_k": latest.get("stoch_k"),
            "stoch_d": latest.get("stoch_d"),
            "bb_high": latest.get("bb_high"),
            "bb_low": latest.get("bb_low"),
            "bb_mid": latest.get("bb_mid"),
            "atr_pct": latest.get("atr_pct"),
            "vol_ratio": latest.get("vol_ratio"),
            "mm50": latest.get("mm50"),
            "sar": latest.get("sar"),
        })
        if "pea_proof_level" not in row and "pea_eligible" not in row:
            row["pea_proof_level"] = "UNKNOWN"
        rows.append(row)

        if det.get("setup") == "T1" and pd.notna(det.get("current_bandwidth")):
            state[isin] = float(det["current_bandwidth"])
        elif det.get("setup") == "T2_CONFIRMATION":
            state.pop(isin, None)

    save_last_t1(state, persistence_path)
    if not rows:
        raise RuntimeError("Construction réseau native: aucun instrument exploitable")
    return pd.DataFrame(rows)


def build_signals(config: dict, raw_signals: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Return a validated TCT frame from real inputs only."""
    source = raw_signals
    if source is None:
        try:
            source = read_signal_snapshot(config)
        except FileNotFoundError:
            source = _build_native_from_universe(config)

    adapted = adapt_repo_free_capture(source)
    adapted = _refresh_exact_t1_t2(adapted, config)
    return validate_signal_contract(adapted)
