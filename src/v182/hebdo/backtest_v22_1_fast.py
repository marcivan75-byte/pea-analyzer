from __future__ import annotations

import numpy as np
import pandas as pd

from v182.hebdo import backtest_v22_1 as base


MAX_HORIZON = max(base.HORIZON_DAYS.values())


def _numeric_array(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float, copy=False)


def _row_stops(frame: pd.DataFrame, stop_pct: float, stop_policy: str) -> np.ndarray:
    if stop_policy == "fixed":
        return np.full(len(frame), float(stop_pct), dtype=float)
    if stop_policy == "atr":
        atr = pd.to_numeric(frame["atr_14_pct"], errors="coerce").to_numpy(dtype=float, copy=False)
        invalid = ~np.isfinite(atr) | (atr <= 0)
        if invalid.any():
            raise base.HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: invalid atr_14_pct for ATR stop")
        return np.clip(atr * 2.5, 0.06, 0.12)
    raise ValueError(f"unsupported stop_policy={stop_policy}")


def add_true_forward_returns_fast(
    features: pd.DataFrame,
    ohlcv: pd.DataFrame,
    *,
    stop_pct: float = 0.09,
    stop_policy: str = "fixed",
) -> pd.DataFrame:
    """Vectorized equivalent of the governed V22.1 forward ledger.

    Semantics intentionally match ``base.add_true_forward_returns``: the last market
    observation at/before the PIT date defines the signal, execution is next-session
    open, an intraday stop locks realised P&L, MAE/MFE uses the complete 126-session
    window, and horizons are 5/10/20/63/126 sessions.
    """
    f, o = base._validate_inputs(features, ohlcv)
    f = f.reset_index(drop=True)
    n = len(f)

    signal_market_date = np.full(n, None, dtype=object)
    entry_date = np.full(n, None, dtype=object)
    entry_price = np.full(n, np.nan, dtype=float)
    stop_used = _row_stops(f, stop_pct, stop_policy)
    aligned = np.zeros(n, dtype=bool)

    returns = {h: np.full(n, np.nan, dtype=float) for h in base.HORIZON_DAYS}
    label_end = np.full(n, None, dtype=object)
    hit_stop = np.full(n, None, dtype=object)
    day_stop = np.full(n, None, dtype=object)
    mae = np.full(n, np.nan, dtype=float)
    mfe = np.full(n, np.nan, dtype=float)

    market = {
        str(ticker): grp.sort_values("date", kind="stable").reset_index(drop=True)
        for ticker, grp in o.groupby("ticker", sort=False)
    }

    for ticker, positions in f.groupby(f["ticker"].astype(str), sort=False).groups.items():
        hist = market.get(str(ticker))
        if hist is None or hist.empty:
            continue

        pos = np.fromiter(positions, dtype=np.int64)
        dates = hist["date"].to_numpy(copy=False)
        asof = f.loc[pos, "as_of_date"].to_numpy(copy=False)
        signal_idx = np.searchsorted(dates, asof, side="right") - 1
        eidx = signal_idx + 1

        opens = _numeric_array(hist, "open")
        lows = _numeric_array(hist, "low")
        highs = _numeric_array(hist, "high")
        closes = _numeric_array(hist, "close")

        valid = (signal_idx >= 0) & (eidx < len(hist))
        if not valid.any():
            continue
        vp = pos[valid]
        ve = eidx[valid]
        vs = signal_idx[valid]
        valid_price = np.isfinite(opens[ve]) & (opens[ve] > 0)
        vp = vp[valid_price]
        ve = ve[valid_price]
        vs = vs[valid_price]
        if len(vp) == 0:
            continue

        aligned[vp] = True
        signal_market_date[vp] = dates[vs]
        entry_date[vp] = dates[ve]
        entry_price[vp] = opens[ve]

        # One 126-session index matrix per ticker. Shorter horizons are slices of
        # the same matrix; this removes the former repeated 5/10/20/63/126 scans.
        steps = np.arange(MAX_HORIZON, dtype=np.int64)
        raw_idx = ve[:, None] + steps[None, :]
        valid_step = raw_idx < len(hist)
        safe_idx = np.minimum(raw_idx, len(hist) - 1)
        low_window = lows[safe_idx]
        threshold = entry_price[vp, None] * (1.0 - stop_used[vp, None])
        touched = valid_step & np.isfinite(low_window) & (low_window <= threshold)

        for horizon, days in base.HORIZON_DAYS.items():
            complete = ve + days <= len(hist)
            if not complete.any():
                continue
            hp = vp[complete]
            he = ve[complete]
            stop_h = touched[complete, :days].any(axis=1)
            final_close = closes[he + days - 1]
            pnl = np.where(stop_h, -stop_used[hp], final_close / entry_price[hp] - 1.0)
            pnl[~stop_h & ~np.isfinite(final_close)] = np.nan
            returns[horizon][hp] = pnl

        complete_126 = ve + MAX_HORIZON <= len(hist)
        if complete_126.any():
            hp = vp[complete_126]
            he = ve[complete_126]
            touched_126 = touched[complete_126]
            any_stop = touched_126.any(axis=1)
            first = touched_126.argmax(axis=1)
            label_end[hp] = dates[he + MAX_HORIZON - 1]
            hit_stop[hp] = any_stop.astype(bool)
            day_stop[hp] = np.where(any_stop, first, None)

            low_126 = low_window[complete_126]
            high_126 = highs[safe_idx[complete_126]]
            finite_low = np.isfinite(low_126)
            finite_high = np.isfinite(high_126)
            min_low = np.where(finite_low, low_126, np.inf).min(axis=1)
            max_high = np.where(finite_high, high_126, -np.inf).max(axis=1)
            min_low[~finite_low.any(axis=1)] = np.nan
            max_high[~finite_high.any(axis=1)] = np.nan
            mae[hp] = min_low / entry_price[hp] - 1.0
            mfe[hp] = max_high / entry_price[hp] - 1.0

    if not aligned.any():
        raise base.HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: no alignable historical observations")

    keep = np.flatnonzero(aligned)
    result = f.iloc[keep].copy().reset_index(drop=True)
    result["signal_market_date"] = pd.to_datetime(signal_market_date[keep], errors="coerce")
    result["entry_date"] = pd.to_datetime(entry_date[keep], errors="coerce")
    result["entry_price"] = entry_price[keep]
    result["execution_policy"] = "NEXT_SESSION_OPEN_J1"
    result["stop_policy"] = stop_policy
    result["stop_pct_used"] = stop_used[keep]
    for horizon in base.HORIZON_DAYS:
        result[f"forward_ret_true_{horizon}"] = returns[horizon][keep]
    result["label_end_date_26w"] = pd.to_datetime(label_end[keep], errors="coerce")
    result["hit_stop"] = pd.array(hit_stop[keep], dtype="boolean")
    result["day_stop"] = pd.array(day_stop[keep], dtype="Int64")
    result["mae"] = mae[keep]
    result["mfe"] = mfe[keep]
    return result


def main() -> int:
    base.add_true_forward_returns = add_true_forward_returns_fast
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
