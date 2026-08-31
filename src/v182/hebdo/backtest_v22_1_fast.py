from __future__ import annotations

import numpy as np
import pandas as pd

from v182.hebdo import backtest_v22_1 as base


def _numeric_array(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float, copy=False)


def _row_stops(frame: pd.DataFrame, stop_pct: float, stop_policy: str) -> np.ndarray:
    if stop_policy == "fixed":
        return np.full(len(frame), float(stop_pct), dtype=float)
    if stop_policy == "atr":
        atr = pd.to_numeric(frame["atr_14_pct"], errors="coerce").to_numpy(dtype=float, copy=False)
        out = np.full(len(frame), np.nan, dtype=float)
        for i, value in enumerate(atr):
            try:
                out[i] = base.adaptive_atr_stop_pct(float(value))
            except (TypeError, ValueError):
                pass
        return out
    raise ValueError(f"unsupported stop_policy={stop_policy}")


def add_true_forward_returns_fast(
    features: pd.DataFrame,
    ohlcv: pd.DataFrame,
    *,
    stop_pct: float = 0.09,
    stop_policy: str = "fixed",
) -> pd.DataFrame:
    """Vectorized-equivalent V22.1 forward ledger.

    Economic semantics are intentionally identical to ``base.add_true_forward_returns``:
    signal at/before PIT date, execution at next session open, intraday stop locking P&L,
    complete-window MAE/MFE, and 5/10/20/63/126-session horizons.
    """
    f, o = base._validate_inputs(features, ohlcv)
    f = f.reset_index(drop=True)
    n = len(f)

    signal_market_date = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    entry_date = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    entry_price = np.full(n, np.nan, dtype=float)
    stop_used = _row_stops(f, stop_pct, stop_policy)
    aligned = np.zeros(n, dtype=bool)

    returns = {h: np.full(n, np.nan, dtype=float) for h in base.HORIZON_DAYS}
    label_end = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
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
        pos = np.asarray(list(positions), dtype=np.int64)
        dates = hist["date"].to_numpy(dtype="datetime64[ns]")
        asof = f.loc[pos, "as_of_date"].to_numpy(dtype="datetime64[ns]")
        signal_idx = np.searchsorted(dates, asof, side="right") - 1
        eidx = signal_idx + 1
        opens = _numeric_array(hist, "open")
        lows = _numeric_array(hist, "low")
        highs = _numeric_array(hist, "high")
        closes = _numeric_array(hist, "close")

        valid = (signal_idx >= 0) & (eidx < len(hist))
        if valid.any():
            vp = pos[valid]
            ve = eidx[valid]
            valid_price = np.isfinite(opens[ve]) & (opens[ve] > 0) & np.isfinite(stop_used[vp])
            vp = vp[valid_price]
            ve = ve[valid_price]
            vs = signal_idx[valid][valid_price]
            if len(vp) == 0:
                continue
            aligned[vp] = True
            signal_market_date[vp] = dates[vs]
            entry_date[vp] = dates[ve]
            entry_price[vp] = opens[ve]

            for horizon, days in base.HORIZON_DAYS.items():
                complete = ve + days <= len(hist)
                if not complete.any():
                    continue
                hp = vp[complete]
                he = ve[complete]
                idx = he[:, None] + np.arange(days, dtype=np.int64)
                low_window = lows[idx]
                threshold = entry_price[hp, None] * (1.0 - stop_used[hp, None])
                touched = np.isfinite(low_window) & (low_window <= threshold)
                any_stop = touched.any(axis=1)
                final_close = closes[he + days - 1]
                pnl = np.where(any_stop, -stop_used[hp], final_close / entry_price[hp] - 1.0)
                pnl[~any_stop & ~np.isfinite(final_close)] = np.nan
                returns[horizon][hp] = pnl

                if horizon == "26w":
                    high_window = highs[idx]
                    label_end[hp] = dates[he + days - 1]
                    hit_stop[hp] = any_stop.astype(bool)
                    first = touched.argmax(axis=1)
                    day_stop[hp] = np.where(any_stop, first, None)
                    with np.errstate(all="ignore"):
                        mae[hp] = np.nanmin(low_window, axis=1) / entry_price[hp] - 1.0
                        mfe[hp] = np.nanmax(high_window, axis=1) / entry_price[hp] - 1.0

    if not aligned.any():
        raise base.HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: no alignable historical observations")

    result = f.loc[aligned].copy()
    keep = np.flatnonzero(aligned)
    result["signal_market_date"] = signal_market_date[keep]
    result["entry_date"] = entry_date[keep]
    result["entry_price"] = entry_price[keep]
    result["execution_policy"] = "NEXT_SESSION_OPEN_J1"
    result["stop_policy"] = stop_policy
    result["stop_pct_used"] = stop_used[keep]
    for horizon in base.HORIZON_DAYS:
        result[f"forward_ret_true_{horizon}"] = returns[horizon][keep]
    result["label_end_date_26w"] = label_end[keep]
    result["hit_stop"] = pd.array(hit_stop[keep], dtype="boolean")
    result["day_stop"] = pd.array(day_stop[keep], dtype="Int64")
    result["mae"] = mae[keep]
    result["mfe"] = mfe[keep]
    return result.reset_index(drop=True)


def main() -> int:
    base.add_true_forward_returns = add_true_forward_returns_fast
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
