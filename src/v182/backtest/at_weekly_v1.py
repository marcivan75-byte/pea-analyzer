"""AT V1 weekly backtest: RSI + Stochastic + SMA20/50 + Parabolic SAR.
Research only. No production influence, no parameter optimisation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) >= 4 else Path.cwd()
CACHE_DIRS = {
    "ACTION": Path("data/cache/actions"),
    "ETF": Path("data/cache/etf"),
}
OUT_JSON = Path("outputs/backtest/AT_WEEKLY_V1_SUMMARY.json")
OUT_TRADES = Path("outputs/backtest/AT_WEEKLY_V1_TRADES.csv")
OUT_MD = Path("outputs/backtest/AT_WEEKLY_V1_SUMMARY.md")

RSI_PERIOD = 14
STOCH_PERIOD = 14
STOCH_SMOOTH_K = 3
STOCH_SMOOTH_D = 3
RSI_ENTRY_MAX = 60.0
RSI_EXIT_MIN = 75.0
STOCH_EXIT_MIN = 75.0
SAR_STEP = 0.02
SAR_MAX = 0.20
WEEK_RULE = "W-FRI"
MIN_WEEKLY_BARS = 55


def _finite(value) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except Exception:
        return False


def _load_history(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        else:
            frame = pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return pd.DataFrame()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(c[0] if isinstance(c, tuple) else c) for c in frame.columns]
    frame.columns = [str(c).strip().lower().replace("adj close", "adj_close") for c in frame.columns]
    if not isinstance(frame.index, pd.DatetimeIndex):
        date_col = next((c for c in ("date", "datetime", "timestamp") if c in frame.columns), None)
        if date_col is None:
            return pd.DataFrame()
        frame.index = pd.to_datetime(frame.pop(date_col), errors="coerce")
    else:
        frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame[frame.index.notna()].copy()
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_localize(None)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    required = ["open", "high", "low", "close"]
    if any(c not in frame.columns for c in required):
        return pd.DataFrame()
    for c in required + (["volume"] if "volume" in frame.columns else []):
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    return frame.dropna(subset=required)


def _to_weekly(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in frame.columns:
        agg["volume"] = "sum"
    weekly = frame.resample(WEEK_RULE, label="right", closed="right").agg(agg)
    weekly = weekly.dropna(subset=["open", "high", "low", "close"])
    last_observed = pd.Timestamp(frame.index.max()).normalize()
    weekly = weekly[weekly.index.normalize() <= last_observed]
    return weekly


def _rsi_wilder(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(avg_loss.ne(0.0), 100.0)
    return rsi


def _stochastic(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    lowest = frame["low"].rolling(STOCH_PERIOD, min_periods=STOCH_PERIOD).min()
    highest = frame["high"].rolling(STOCH_PERIOD, min_periods=STOCH_PERIOD).max()
    denom = (highest - lowest).replace(0.0, np.nan)
    raw_k = 100.0 * (frame["close"] - lowest) / denom
    k = raw_k.rolling(STOCH_SMOOTH_K, min_periods=STOCH_SMOOTH_K).mean()
    d = k.rolling(STOCH_SMOOTH_D, min_periods=STOCH_SMOOTH_D).mean()
    return k, d


def _parabolic_sar(high: pd.Series, low: pd.Series, step: float = SAR_STEP, max_step: float = SAR_MAX) -> pd.Series:
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    n = len(h)
    out = np.full(n, np.nan, dtype=float)
    if n == 0:
        return pd.Series(out, index=high.index, dtype=float)
    if n == 1:
        out[0] = l[0]
        return pd.Series(out, index=high.index, dtype=float)
    bull = (h[1] + l[1]) >= (h[0] + l[0])
    out[0] = l[0] if bull else h[0]
    ep = h[0] if bull else l[0]
    af = step
    for i in range(1, n):
        candidate = out[i - 1] + af * (ep - out[i - 1])
        if bull:
            candidate = min(candidate, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < candidate:
                bull = False
                candidate = ep
                ep = l[i]
                af = step
            elif h[i] > ep:
                ep = h[i]
                af = min(max_step, af + step)
        else:
            candidate = max(candidate, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > candidate:
                bull = True
                candidate = ep
                ep = h[i]
                af = step
            elif l[i] < ep:
                ep = l[i]
                af = min(max_step, af + step)
        out[i] = candidate
    return pd.Series(out, index=high.index, dtype=float)


def _indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    out = weekly.copy()
    out["rsi14"] = _rsi_wilder(out["close"])
    out["stoch_k"], out["stoch_d"] = _stochastic(out)
    out["sma20"] = out["close"].rolling(20, min_periods=20).mean()
    out["sma50"] = out["close"].rolling(50, min_periods=50).mean()
    out["psar"] = _parabolic_sar(out["high"], out["low"])
    out["stoch_cross_up"] = (out["stoch_k"] > out["stoch_d"]) & (out["stoch_k"].shift(1) <= out["stoch_d"].shift(1))
    out["entry_signal"] = (
        (out["rsi14"] < RSI_ENTRY_MAX)
        & out["stoch_cross_up"]
        & (out["close"] > out["sma20"])
        & (out["close"] > out["sma50"])
        & (out["close"] > out["psar"])
    )
    return out


def _exit_reasons(row: pd.Series) -> list[str]:
    reasons = []
    if _finite(row.get("rsi14")) and float(row["rsi14"]) > RSI_EXIT_MIN:
        reasons.append("RSI_GT_75")
    if _finite(row.get("stoch_k")) and float(row["stoch_k"]) > STOCH_EXIT_MIN:
        reasons.append("STOCH_K_GT_75")
    if _finite(row.get("sma20")) and float(row["close"]) < float(row["sma20"]):
        reasons.append("CLOSE_LT_SMA20")
    if _finite(row.get("sma50")) and float(row["close"]) < float(row["sma50"]):
        reasons.append("CLOSE_LT_SMA50")
    if _finite(row.get("psar")) and float(row["close"]) < float(row["psar"]):
        reasons.append("CLOSE_LT_PSAR")
    return reasons


def _backtest_one(asset: str, isin: str, weekly: pd.DataFrame) -> tuple[list[dict], dict, dict | None]:
    bars = _indicators(weekly)
    valid = bars.dropna(subset=["rsi14", "stoch_k", "stoch_d", "sma20", "sma50", "psar"])
    diagnostics = {
        "eligible_bar_weeks": int(len(valid)),
        "rsi_lt_60_weeks": int((valid["rsi14"] < RSI_ENTRY_MAX).sum()),
        "rsi_and_stoch_cross_weeks": int(((valid["rsi14"] < RSI_ENTRY_MAX) & valid["stoch_cross_up"]).sum()),
        "plus_ma20_50_weeks": int(((valid["rsi14"] < RSI_ENTRY_MAX) & valid["stoch_cross_up"] & (valid["close"] > valid["sma20"]) & (valid["close"] > valid["sma50"])).sum()),
        "full_entry_signal_weeks": int(valid["entry_signal"].sum()),
    }
    trades: list[dict] = []
    position: dict | None = None
    for i in range(len(bars) - 1):
        row = bars.iloc[i]
        next_row = bars.iloc[i + 1]
        if position is not None and i >= position["entry_idx"]:
            reasons = _exit_reasons(row)
            if reasons and _finite(next_row.get("open")):
                exit_price = float(next_row["open"])
                ret = (exit_price / position["entry_price"] - 1.0) * 100.0
                trades.append({
                    "asset_class": asset,
                    "isin": isin,
                    "entry_signal_date": position["entry_signal_date"],
                    "entry_date": position["entry_date"],
                    "entry_price": position["entry_price"],
                    "exit_signal_date": bars.index[i].date().isoformat(),
                    "exit_date": bars.index[i + 1].date().isoformat(),
                    "exit_price": exit_price,
                    "return_pct": ret,
                    "holding_weeks": int(i + 1 - position["entry_idx"]),
                    "exit_reasons": "|".join(reasons),
                    "entry_rsi": position["entry_rsi"],
                    "entry_stoch_k": position["entry_stoch_k"],
                    "entry_stoch_d": position["entry_stoch_d"],
                })
                position = None
                continue
        if position is None and bool(row.get("entry_signal", False)) and _finite(next_row.get("open")):
            position = {
                "entry_idx": i + 1,
                "entry_signal_date": bars.index[i].date().isoformat(),
                "entry_date": bars.index[i + 1].date().isoformat(),
                "entry_price": float(next_row["open"]),
                "entry_rsi": float(row["rsi14"]),
                "entry_stoch_k": float(row["stoch_k"]),
                "entry_stoch_d": float(row["stoch_d"]),
            }
    open_position = None
    if position is not None:
        last = bars.iloc[-1]
        open_position = {
            "asset_class": asset,
            "isin": isin,
            "entry_date": position["entry_date"],
            "entry_price": position["entry_price"],
            "mark_date": bars.index[-1].date().isoformat(),
            "mark_close": float(last["close"]),
            "unrealized_return_pct": (float(last["close"]) / position["entry_price"] - 1.0) * 100.0,
        }
    return trades, diagnostics, open_position


def _metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "trades": 0, "wins": 0, "win_rate_pct": None, "mean_return_pct": None,
            "median_return_pct": None, "profit_factor": None, "p10_return_pct": None,
            "best_trade_pct": None, "worst_trade_pct": None, "mean_holding_weeks": None,
        }
    r = pd.to_numeric(frame["return_pct"], errors="coerce").dropna()
    wins = r[r > 0]
    losses = r[r < 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    pf = gross_win / gross_loss if gross_loss > 0 else (math.inf if gross_win > 0 else None)
    return {
        "trades": int(len(r)),
        "wins": int((r > 0).sum()),
        "win_rate_pct": round(float((r > 0).mean() * 100.0), 2),
        "mean_return_pct": round(float(r.mean()), 3),
        "median_return_pct": round(float(r.median()), 3),
        "profit_factor": round(float(pf), 3) if pf is not None and math.isfinite(pf) else ("INF" if pf == math.inf else None),
        "p10_return_pct": round(float(r.quantile(0.10)), 3),
        "best_trade_pct": round(float(r.max()), 3),
        "worst_trade_pct": round(float(r.min()), 3),
        "mean_holding_weeks": round(float(pd.to_numeric(frame["holding_weeks"], errors="coerce").mean()), 2),
    }


def _window_metrics(trades: pd.DataFrame, end_date: pd.Timestamp) -> dict:
    out = {}
    if trades.empty:
        return {k: _metrics(trades) for k in ("12M", "18M", "24M", "36M", "ALL")}
    entries = pd.to_datetime(trades["entry_date"], errors="coerce")
    for label, months in (("12M", 12), ("18M", 18), ("24M", 24), ("36M", 36)):
        cutoff = end_date - pd.DateOffset(months=months)
        out[label] = _metrics(trades[entries >= cutoff])
    out["ALL"] = _metrics(trades)
    return out


def _markdown(payload: dict) -> str:
    lines = [
        "# AT WEEKLY V1 — backtest diagnostic",
        "",
        "Research only — aucune influence production/CI, aucune optimisation de seuils.",
        "",
        "## Règles verrouillées",
        "",
        "Entrée (AND): RSI14 < 60 ; Stoch 14,3,3 croise haussier ; close > SMA20 ; close > SMA50 ; close > PSAR(0.02,0.20).",
        "Sortie (OR): RSI14 > 75 ; Stoch %K > 75 ; close < SMA20 ; close < SMA50 ; close < PSAR.",
        "Signal sur clôture hebdomadaire terminée, exécution à l'ouverture hebdomadaire suivante. Hors frais/slippage.",
        "",
        "## Résultats",
        "",
        "| Univers | Instruments valides | Trades | Taux positif | Rendement moyen | Médiane | Profit factor | Durée moy. |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scope in ("ACTION", "ETF", "TOTAL"):
        block = payload["scopes"].get(scope, {})
        m = block.get("metrics", {}).get("ALL", {})
        lines.append(
            f"| {scope} | {block.get('valid_instruments', '')} | {m.get('trades')} | {m.get('win_rate_pct')}% | "
            f"{m.get('mean_return_pct')}% | {m.get('median_return_pct')}% | {m.get('profit_factor')} | {m.get('mean_holding_weeks')} sem. |"
        )
    lines += ["", "## Diagnostic filtres d'entrée", ""]
    d = payload.get("entry_filter_diagnostics", {})
    for key in ("eligible_bar_weeks", "rsi_lt_60_weeks", "rsi_and_stoch_cross_weeks", "plus_ma20_50_weeks", "full_entry_signal_weeks"):
        lines.append(f"- {key}: {d.get(key, 0)}")
    lines += ["", "## Limites", "", "Univers courant/cache courant: diagnostic pré-OOS, biais de survivance possible. Aucun résultat ne doit promouvoir automatiquement la stratégie."]
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    all_trades: list[dict] = []
    open_positions: list[dict] = []
    scope_meta = {}
    filter_totals = Counter()
    data_first = []
    data_last = []
    failures = Counter()

    for asset, relative in CACHE_DIRS.items():
        folder = root / relative
        files = []
        if folder.exists():
            files = sorted(list(folder.glob("*.parquet")) + list(folder.glob("*.csv")))
        valid_instruments = 0
        scope_trades = []
        scope_open = []
        for path in files:
            isin = "".join(ch for ch in path.stem.upper() if ch.isalnum())
            history = _load_history(path)
            if history.empty:
                failures[f"{asset}_INVALID_OHLCV"] += 1
                continue
            weekly = _to_weekly(history)
            if len(weekly) < MIN_WEEKLY_BARS:
                failures[f"{asset}_SHORT_HISTORY"] += 1
                continue
            valid_instruments += 1
            data_first.append(weekly.index.min())
            data_last.append(weekly.index.max())
            trades, diagnostics, open_position = _backtest_one(asset, isin, weekly)
            scope_trades.extend(trades)
            if open_position:
                scope_open.append(open_position)
            filter_totals.update(diagnostics)
        all_trades.extend(scope_trades)
        open_positions.extend(scope_open)
        scope_meta[asset] = {
            "cache_files": int(len(files)),
            "valid_instruments": int(valid_instruments),
            "completed_trades": int(len(scope_trades)),
            "open_positions": int(len(scope_open)),
        }

    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df = trades_df.sort_values(["entry_date", "asset_class", "isin"]).reset_index(drop=True)
    end_date = max(data_last) if data_last else pd.Timestamp.utcnow().tz_localize(None)
    for asset in ("ACTION", "ETF"):
        subset = trades_df[trades_df["asset_class"].eq(asset)] if not trades_df.empty else pd.DataFrame(columns=["return_pct", "holding_weeks", "entry_date"])
        scope_meta[asset]["metrics"] = _window_metrics(subset, pd.Timestamp(end_date))
    scope_meta["TOTAL"] = {
        "valid_instruments": int(scope_meta.get("ACTION", {}).get("valid_instruments", 0) + scope_meta.get("ETF", {}).get("valid_instruments", 0)),
        "completed_trades": int(len(trades_df)),
        "open_positions": int(len(open_positions)),
        "metrics": _window_metrics(trades_df if not trades_df.empty else pd.DataFrame(columns=["return_pct", "holding_weeks", "entry_date"]), pd.Timestamp(end_date)),
    }

    exit_counts = Counter()
    if not trades_df.empty:
        for text in trades_df["exit_reasons"].fillna(""):
            for reason in str(text).split("|"):
                if reason:
                    exit_counts[reason] += 1

    payload = {
        "status": "SUCCESS" if scope_meta["TOTAL"]["valid_instruments"] else "NO_USABLE_CACHE",
        "version": "AT_WEEKLY_V1_2026_08_29",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "promotion_eligible": False,
        "decision_influence": 0.0,
        "real_orders_enabled": False,
        "rules": {
            "timeframe": "weekly_W-FRI_completed_bars",
            "entry_all": ["RSI14_LT_60", "STOCH_14_3_3_K_CROSS_UP_D", "CLOSE_GT_SMA20", "CLOSE_GT_SMA50", "CLOSE_GT_PSAR_0_02_0_20"],
            "exit_any": ["RSI14_GT_75", "STOCH_K_GT_75", "CLOSE_LT_SMA20", "CLOSE_LT_SMA50", "CLOSE_LT_PSAR"],
            "execution": "NEXT_WEEK_OPEN",
            "fees_slippage": "NOT_APPLIED",
        },
        "data_window": {
            "first_completed_week": min(data_first).date().isoformat() if data_first else None,
            "last_completed_week": max(data_last).date().isoformat() if data_last else None,
        },
        "scopes": scope_meta,
        "entry_filter_diagnostics": dict(filter_totals),
        "exit_trigger_counts": dict(exit_counts),
        "failures": dict(failures),
        "limitations": [
            "CURRENT_CACHE_UNIVERSE_NOT_POINT_IN_TIME_MEMBERSHIP",
            "SURVIVORSHIP_BIAS_POSSIBLE",
            "NO_FEES_OR_SLIPPAGE",
            "PRE_OOS_DIAGNOSTIC_ONLY",
        ],
    }

    out_json = root / OUT_JSON
    out_csv = root / OUT_TRADES
    out_md = root / OUT_MD
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    if trades_df.empty:
        pd.DataFrame(columns=["asset_class", "isin", "entry_date", "exit_date", "return_pct", "holding_weeks", "exit_reasons"]).to_csv(out_csv, sep=";", index=False, encoding="utf-8-sig")
    else:
        trades_df.to_csv(out_csv, sep=";", index=False, encoding="utf-8-sig")
    out_md.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return payload


if __name__ == "__main__":
    run()
