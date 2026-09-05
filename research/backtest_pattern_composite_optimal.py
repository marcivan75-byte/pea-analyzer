from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from v182.hebdo.meta_price_history import load_2010_2026

OUT = Path("outputs/pattern_composite_optimal")
OUT.mkdir(parents=True, exist_ok=True)


def load_physical_base() -> pd.DataFrame:
    df = load_2010_2026(
        "inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet",
        "inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json",
        "data/cache/actions",
    )[["date", "ticker", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "ticker", "open", "close", "volume"])
    df = df[(df.open > 0) & (df.close > 0) & (df.volume >= 0)]
    return df.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last").reset_index(drop=True)


def load_benchmark(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    # EURO STOXX 50, benchmark explicitement proposé dans le module utilisateur.
    b = yf.download(
        "^STOXX50E",
        start=(start - pd.Timedelta(days=180)).strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        auto_adjust=False,
        repair=False,
        progress=False,
        threads=False,
    )
    if b.empty:
        raise RuntimeError("EURO_STOXX_50_DOWNLOAD_EMPTY")
    if isinstance(b.columns, pd.MultiIndex):
        b.columns = b.columns.get_level_values(0)
    close = pd.to_numeric(b["Close"], errors="coerce")
    z = pd.DataFrame({"date": pd.to_datetime(b.index).tz_localize(None), "bench_close": close.to_numpy()})
    z = z.dropna().drop_duplicates("date").sort_values("date")
    z["bench_mm20"] = z.bench_close.rolling(20, min_periods=20).mean()
    z["market_ok"] = z.bench_close > z.bench_mm20
    z["bench_ret90"] = z.bench_close.pct_change(90, fill_method=None)
    return z


def engineer(df: pd.DataFrame, bench: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    g = x.groupby("ticker", sort=False, group_keys=False)

    prev_close = g.close.shift(1)
    x["gap_pct"] = x.open / prev_close - 1.0
    x["has_gap"] = x.gap_pct >= 0.20

    # Reproduction du module : rolling(20) inclut la séance courante.
    x["volume_avg20"] = g.volume.transform(lambda s: s.rolling(20, min_periods=20).mean())
    x["volume_ratio"] = x.volume / x.volume_avg20.replace(0, np.nan)
    x["has_volume"] = x.volume_ratio >= 8.0

    x["ret90"] = g.close.pct_change(90, fill_method=None)

    roll_mean = g.close.transform(lambda s: s.rolling(20, min_periods=20).mean())
    roll_std = g.close.transform(lambda s: s.rolling(20, min_periods=20).std())
    x["volatility"] = roll_std / roll_mean.replace(0, np.nan)
    x["volatility_avg"] = x.groupby("ticker", sort=False).volatility.transform(
        lambda s: s.rolling(20, min_periods=20).mean()
    )
    x["has_base"] = x.volatility < x.volatility_avg

    x = x.merge(bench[["date", "bench_ret90", "market_ok", "bench_close", "bench_mm20"]], on="date", how="left")
    x = x.sort_values(["ticker", "date"]).reset_index(drop=True)
    x["rs"] = (1.0 + x.ret90) / (1.0 + x.bench_ret90) - 1.0
    # L'intention du commentaire 'Top 30%' impose un percentile cross-sectionnel par date.
    x["rs_rank"] = x.groupby("date").rs.rank(pct=True, method="average")
    x["has_rs70"] = x.rs_rank >= 0.70

    x["pattern_ok"] = x.has_gap & x.has_volume & x.has_rs70 & x.has_base & x.market_ok.fillna(False)

    # Sorties futures, strictement par ticker.
    gg = x.groupby("ticker", sort=False, group_keys=False)
    x["raw_entry_date"] = x.date
    x["raw_entry"] = x.open
    x["pit_entry_date"] = gg.date.shift(-1)
    x["pit_entry"] = gg.open.shift(-1)
    x["exit_date"] = gg.date.shift(-5)
    x["exit_close_5"] = gg.close.shift(-5)
    x["raw_ret5"] = x.exit_close_5 / x.raw_entry - 1.0
    # Signal connu après clôture J ; entrée Open J+1 ; clôture J+5 = 5 séances incluant J+1.
    x["pit_ret5"] = x.exit_close_5 / x.pit_entry - 1.0
    return x


def yearly(trades: pd.DataFrame, mode: str) -> pd.DataFrame:
    rcol = "raw_ret5" if mode == "RAW" else "pit_ret5"
    dcol = "raw_entry_date" if mode == "RAW" else "pit_entry_date"
    q = trades.dropna(subset=[rcol, dcol]).copy()
    q["year"] = pd.to_datetime(q[dcol]).dt.year
    rows = []
    for year, z in q.groupby("year"):
        r = z[rcol].astype(float)
        pnl = float((10000.0 * r).sum())
        rows.append({
            "mode": mode,
            "year": int(year),
            "trades": int(len(z)),
            "wins": int((r > 0).sum()),
            "losses": int((r <= 0).sum()),
            "win_rate_pct": float(100 * (r > 0).mean()),
            "trades_ge_20pct": int((r >= 0.20).sum()),
            "proba_ge_20pct": float(100 * (r >= 0.20).mean()),
            "mean_return_pct": float(100 * r.mean()),
            "median_return_pct": float(100 * r.median()),
            "best_return_pct": float(100 * r.max()),
            "worst_return_pct": float(100 * r.min()),
            "fixed_10k_pnl_eur": pnl,
            "annual_10k_plus_pnl_eur": 10000.0 + pnl,
        })
    return pd.DataFrame(rows)


def main() -> None:
    raw = load_physical_base()
    bench = load_benchmark(raw.date.min(), raw.date.max())
    x = engineer(raw, bench)
    trades = x.loc[x.pattern_ok].copy()

    raw_y = yearly(trades, "RAW")
    pit_y = yearly(trades, "PIT")
    annual = pd.concat([raw_y, pit_y], ignore_index=True).sort_values(["mode", "year"])
    annual.to_csv(OUT / "RESULTATS_ANNUELS_RAW_ET_PIT.csv", index=False)

    trade_cols = [
        "date", "ticker", "gap_pct", "volume", "volume_avg20", "volume_ratio",
        "ret90", "bench_ret90", "rs", "rs_rank", "volatility", "volatility_avg",
        "has_base", "market_ok", "raw_entry_date", "raw_entry", "pit_entry_date",
        "pit_entry", "exit_date", "exit_close_5", "raw_ret5", "pit_ret5"
    ]
    trades[trade_cols].to_csv(OUT / "TRADES_DETAIL.csv", index=False)

    conditions = {
        "rows_physical_base": int(len(raw)),
        "tickers": int(raw.ticker.nunique()),
        "date_min": str(raw.date.min().date()),
        "date_max": str(raw.date.max().date()),
        "benchmark": "^STOXX50E / EURO STOXX 50",
        "rows_gap_ge_20": int(x.has_gap.sum()),
        "rows_volume_ge_8x": int(x.has_volume.sum()),
        "rows_rs_top30": int(x.has_rs70.sum()),
        "rows_base": int(x.has_base.sum()),
        "rows_market_ok": int(x.market_ok.fillna(False).sum()),
        "pattern_signals": int(x.pattern_ok.sum()),
        "raw_executable_trades": int(trades.raw_ret5.notna().sum()),
        "pit_executable_trades": int(trades.pit_ret5.notna().sum()),
        "methodology": {
            "RAW": "Signal et filtres de J, entree Open J (look-ahead conserve pour comparaison avec le module fourni), sortie Close J+5.",
            "PIT": "Signal calcule apres Close J, entree Open J+1, sortie Close J+5.",
            "RS": "retour 90 seances relatif a EURO STOXX 50, percentile cross-sectionnel par date; top 30% >= 0.70.",
            "base": "definition exacte du code fourni: volatilite 20j < moyenne 20j de cette volatilite; min_weeks/max_weeks non utilises par le module original.",
            "capital": "10 000 EUR fixes par trade; pas un portefeuille compose; chevauchements non bloques.",
        },
    }
    (OUT / "SUMMARY.json").write_text(json.dumps(conditions, indent=2), encoding="utf-8")
    print(json.dumps(conditions, indent=2))
    print("\nANNUAL\n", annual.to_string(index=False))


if __name__ == "__main__":
    main()
