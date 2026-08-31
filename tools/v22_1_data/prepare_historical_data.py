from __future__ import annotations

import argparse
import base64
import io
import json
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

IDENTITY_PARTS = Path("config/V21_9_ACTION_IDENTITY_MAP.parts")
OUTDIR = Path("outputs/hebdo/data_v22_1")
START_DOWNLOAD = "2018-01-01"
END_DOWNLOAD = "2025-07-15"  # warmup + 126 sessions after 2024-12-31
PIT_START = pd.Timestamp("2019-01-01")
PIT_END = pd.Timestamp("2024-12-31")
MIN_PREP_COVERAGE = 0.90


class DataPrepBlocked(RuntimeError):
    pass


def _decode_parts(path: Path) -> str:
    parts = sorted(path.glob("*.part"))
    if not parts:
        raise DataPrepBlocked(f"identity parts missing: {path}")
    encoded = "".join(p.read_text(encoding="utf-8").strip() for p in parts)
    try:
        raw = base64.b64decode(encoded)
        for wbits in (zlib.MAX_WBITS, zlib.MAX_WBITS | 32, -zlib.MAX_WBITS):
            try:
                return zlib.decompress(raw, wbits).decode("utf-8")
            except zlib.error:
                continue
    except Exception as exc:  # pragma: no cover
        raise DataPrepBlocked(f"identity decode failed: {type(exc).__name__}") from exc
    raise DataPrepBlocked("identity decode failed: unsupported compressed payload")


def load_identity_map(root: Path) -> pd.DataFrame:
    text = _decode_parts(root / IDENTITY_PARTS)
    try:
        payload = json.loads(text)
        if isinstance(payload, list):
            frame = pd.DataFrame(payload)
        elif isinstance(payload, dict):
            rows = next((payload[k] for k in ("rows", "data", "records") if isinstance(payload.get(k), list)), None)
            frame = pd.DataFrame(rows) if rows is not None else pd.DataFrame([payload])
        else:
            raise ValueError("unsupported JSON")
    except Exception:
        frame = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    if frame.empty:
        raise DataPrepBlocked("identity map decoded but empty")

    cols = {str(c).lower(): str(c) for c in frame.columns}
    ticker_col = next((cols[c] for c in ("yahoo_ticker", "ticker_yahoo", "ticker", "symbol") if c in cols), None)
    isin_col = cols.get("isin")
    if ticker_col is None or isin_col is None:
        raise DataPrepBlocked(f"identity map lacks validated ISIN/ticker columns: {list(frame.columns)}")
    out = frame.rename(columns={ticker_col: "ticker", isin_col: "isin"}).copy()
    out["ticker"] = out["ticker"].astype("string").str.strip().str.upper()
    out["isin"] = out["isin"].astype("string").str.strip().str.upper()
    out = out[out["ticker"].notna() & out["ticker"].ne("") & out["isin"].str.len().eq(12)].copy()

    for candidate in ("scorable", "ticker_validated", "eligible", "active"):
        if candidate in cols:
            source_col = cols[candidate]
            vals = out[source_col]
            if vals.dtype == bool:
                out = out[vals].copy()
            else:
                textv = vals.astype(str).str.upper().str.strip()
                out = out[textv.isin({"1", "TRUE", "YES", "Y", "OK", "VALID", "SCORABLE"})].copy()
            break
    out = out.drop_duplicates(subset=["isin"], keep="first")
    if out.empty:
        raise DataPrepBlocked("no validated ISIN/ticker pair after fail-closed filtering")
    return out


def _download_batch(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    data = yf.download(
        tickers=tickers,
        start=start,
        end=end,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        threads=True,
        progress=False,
        timeout=25,
    )
    rows: list[pd.DataFrame] = []
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        top = set(map(str, data.columns.get_level_values(0)))
        for ticker in tickers:
            if ticker not in top:
                continue
            g = data[ticker].copy()
            g.columns = [str(c).lower() for c in g.columns]
            g = g.reset_index()
            g = g.rename(columns={g.columns[0]: "date"})
            g["ticker"] = ticker
            rows.append(g)
    elif len(tickers) == 1:
        g = data.copy()
        g.columns = [str(c).lower() for c in g.columns]
        g = g.reset_index()
        g = g.rename(columns={g.columns[0]: "date"})
        g["ticker"] = tickers[0]
        rows.append(g)
    return pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()


def download_ohlcv(identity: pd.DataFrame, outdir: Path, batch_size: int = 80) -> pd.DataFrame:
    tickers = identity["ticker"].drop_duplicates().tolist()
    chunks: list[pd.DataFrame] = []
    failed_batches: list[list[str]] = []

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            part = _download_batch(batch, START_DOWNLOAD, END_DOWNLOAD)
        except Exception:
            failed_batches.append(batch)
            continue
        if not part.empty:
            chunks.append(part)

    first_pass = pd.concat(chunks, ignore_index=True, sort=False) if chunks else pd.DataFrame()
    downloaded = set(first_pass["ticker"].astype(str).unique()) if not first_pass.empty and "ticker" in first_pass.columns else set()
    missing = [t for t in tickers if t not in downloaded]

    # Retry every missing ticker individually. This avoids losing an entire batch
    # because one provider symbol is temporarily unavailable.
    retry_failures: list[str] = []
    retry_chunks: list[pd.DataFrame] = []
    for n, ticker in enumerate(missing, start=1):
        try:
            part = _download_batch([ticker], START_DOWNLOAD, END_DOWNLOAD)
            if part.empty:
                retry_failures.append(ticker)
            else:
                retry_chunks.append(part)
        except Exception:
            retry_failures.append(ticker)
        if n % 25 == 0:
            time.sleep(1.0)

    all_chunks = ([first_pass] if not first_pass.empty else []) + retry_chunks
    if not all_chunks:
        raise DataPrepBlocked("no OHLCV downloaded from yfinance")
    ohlcv = pd.concat(all_chunks, ignore_index=True, sort=False)
    ohlcv = ohlcv.rename(columns={"adj close": "adj_close"})
    needed = {"ticker", "date", "open", "high", "low", "close", "volume"}
    missing_cols = needed.difference(ohlcv.columns)
    if missing_cols:
        raise DataPrepBlocked(f"downloaded OHLCV missing columns {sorted(missing_cols)}")
    ohlcv["date"] = pd.to_datetime(ohlcv["date"], errors="coerce", utc=True).dt.tz_convert(None)
    for c in ("open", "high", "low", "close", "volume"):
        ohlcv[c] = pd.to_numeric(ohlcv[c], errors="coerce")
    ohlcv = (
        ohlcv.dropna(subset=["ticker", "date", "close"])
        .drop_duplicates(subset=["ticker", "date"], keep="last")
        .sort_values(["ticker", "date"])
    )
    ohlcv.to_parquet(outdir / "V22_1_OHLCV_2018_2025.parquet", index=False)
    final_downloaded = set(ohlcv["ticker"].astype(str).unique())
    final_missing = sorted(set(tickers).difference(final_downloaded))
    failure_payload = {
        "failed_batches_first_pass": failed_batches,
        "individual_retry_failures": retry_failures,
        "final_missing_tickers": final_missing,
    }
    (outdir / "V22_1_DOWNLOAD_FAILURES.json").write_text(json.dumps(failure_payload, indent=2), encoding="utf-8")
    return ohlcv


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).rolling(n, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_technical_pit(ohlcv: pd.DataFrame, identity: pd.DataFrame, outdir: Path) -> pd.DataFrame:
    id_small = identity[[c for c in identity.columns if c in {"isin", "ticker"}]].drop_duplicates("ticker")
    rows: list[pd.DataFrame] = []
    friday_grid = pd.date_range(PIT_START, PIT_END, freq="W-FRI")
    for ticker, g0 in ohlcv.groupby("ticker", sort=False):
        g = g0.sort_values("date").copy().set_index("date")
        close = g["close"]
        volume = g["volume"]
        prior = volume.shift(1)
        avg20 = prior.rolling(20, min_periods=20).mean()
        std20 = prior.rolling(20, min_periods=20).std(ddof=0)
        g["vol_z"] = (volume - avg20) / std20.replace(0, np.nan)
        g["sma20"] = close.rolling(20, min_periods=20).mean()
        g["sma200"] = close.rolling(200, min_periods=200).mean()
        g["drawdown_4w"] = close / close.rolling(20, min_periods=20).max() - 1
        g["mom_26w"] = close / close.shift(126) - 1
        prev = close.shift(1)
        tr = pd.concat([(g["high"] - g["low"]).abs(), (g["high"] - prev).abs(), (g["low"] - prev).abs()], axis=1).max(axis=1)
        g["atr_14_pct"] = tr.rolling(14, min_periods=14).mean() / close.replace(0, np.nan)
        weekly = close.resample("W-FRI").last().dropna()
        wrsi = _rsi(weekly, 14)
        g["rsi_14_hebdo"] = wrsi.reindex(g.index, method="ffill")

        pos = g.index.searchsorted(friday_grid, side="right") - 1
        valid = pos >= 0
        if not valid.any():
            continue
        take = g.iloc[pos[valid]].copy()
        signal_dates = friday_grid[valid]
        take["signal_date"] = signal_dates.values
        # Use an explicit Friday end-of-day UTC cutoff rather than an implicit
        # midnight timestamp. market_data_date separately records the actual
        # trading session (Thursday if Friday was a market holiday).
        take["as_of_date"] = (signal_dates + pd.Timedelta(hours=23, minutes=59, seconds=59)).values
        take["market_data_date"] = pd.to_datetime(take.index).values
        take["pit_observed_at"] = [f"{d.date().isoformat()}T23:59:59Z" for d in signal_dates]
        take["ticker"] = ticker
        rows.append(
            take.reset_index(drop=True)[[
                "ticker", "signal_date", "as_of_date", "market_data_date", "pit_observed_at",
                "close", "sma20", "sma200", "vol_z", "drawdown_4w", "mom_26w",
                "atr_14_pct", "rsi_14_hebdo",
            ]]
        )
    if not rows:
        raise DataPrepBlocked("technical PIT generation produced no rows")
    pit = pd.concat(rows, ignore_index=True)
    pit = pit.merge(id_small, on="ticker", how="left", validate="many_to_one")
    pit = pit.dropna(subset=["isin"])
    pit.to_parquet(outdir / "V22_1_TECHNICAL_PIT_2019_2024.parquet", index=False)
    return pit


def scan_historical_nonprice_pit(root: Path) -> dict[str, object]:
    roots = [root / "outputs" / "audit", root / "data", root / "inputs", root / "config"]
    sector_ready = False
    quality_ready = False
    evidence: list[str] = []
    scanned = 0
    for base in roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in {".csv", ".parquet", ".json", ".jsonl"}:
                continue
            scanned += 1
            name = path.name.lower()
            historical = any(k in name for k in ("pit", "history", "histor", "point_in_time", "asof"))
            if historical and "sector" in name:
                sector_ready = True
                evidence.append(str(path.relative_to(root)))
            if historical and any(k in name for k in ("fundamental", "financial", "quality", "roe", "debt")):
                quality_ready = True
                evidence.append(str(path.relative_to(root)))
    return {
        "sector_pit_ready": sector_ready,
        "quality_pit_ready": quality_ready,
        "evidence": sorted(set(evidence)),
        "files_scanned": scanned,
    }


def coverage_by_year(ohlcv: pd.DataFrame, identity: pd.DataFrame) -> dict[str, float]:
    expected = int(identity["ticker"].nunique())
    out: dict[str, float] = {}
    if expected == 0:
        return out
    for year in range(2018, 2026):
        n = int(ohlcv.loc[ohlcv["date"].dt.year.eq(year), "ticker"].nunique())
        out[str(year)] = n / expected
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--batch-size", type=int, default=80)
    args = ap.parse_args()
    root = args.root.resolve()
    outdir = root / OUTDIR
    outdir.mkdir(parents=True, exist_ok=True)

    identity = load_identity_map(root)
    identity[["isin", "ticker"]].to_csv(outdir / "V22_1_VALIDATED_IDENTITIES.csv", index=False)
    ohlcv = download_ohlcv(identity, outdir, batch_size=args.batch_size)
    pit = build_technical_pit(ohlcv, identity, outdir)
    nonprice = scan_historical_nonprice_pit(root)

    ticker_count = int(identity["ticker"].nunique())
    ohlcv_tickers = int(ohlcv["ticker"].nunique())
    coverage = ohlcv_tickers / ticker_count if ticker_count else 0.0
    yearly_coverage = coverage_by_year(ohlcv, identity)
    pit_isins = int(pit["isin"].nunique())
    pit_isin_coverage = pit_isins / int(identity["isin"].nunique()) if len(identity) else 0.0

    report = {
        "status": "READY_FULL_PIT" if (coverage >= MIN_PREP_COVERAGE and nonprice["sector_pit_ready"] and nonprice["quality_pit_ready"]) else "READY_TECHNICAL_ONLY",
        "identity_rows": int(len(identity)),
        "validated_tickers": ticker_count,
        "ohlcv_tickers": ohlcv_tickers,
        "ohlcv_ticker_coverage": coverage,
        "ohlcv_ticker_coverage_by_year": yearly_coverage,
        "technical_pit_rows": int(len(pit)),
        "technical_pit_isins": pit_isins,
        "technical_pit_isin_coverage": pit_isin_coverage,
        "download_period": [START_DOWNLOAD, END_DOWNLOAD],
        "pit_period": [str(PIT_START.date()), str(PIT_END.date())],
        "technical_features_pit_reconstructable": True,
        "sector_history": nonprice["sector_pit_ready"],
        "quality_roe_debt_history": nonprice["quality_pit_ready"],
        "historical_nonprice_evidence": nonprice["evidence"],
        "historical_nonprice_files_scanned": nonprice["files_scanned"],
        "final_performance_validation_authorized": bool(
            coverage >= MIN_PREP_COVERAGE
            and pit_isin_coverage >= MIN_PREP_COVERAGE
            and nonprice["sector_pit_ready"]
            and nonprice["quality_pit_ready"]
        ),
        "governance": {
            "no_invented_ticker": True,
            "identity_source": str(IDENTITY_PARTS),
            "current_fundamentals_not_used_as_history": True,
            "current_sector_not_used_as_history": True,
            "signal_cutoff": "Friday 23:59:59Z; actual last market session stored separately",
            "fail_closed": True,
        },
    }
    (outdir / "V22_1_DATA_READINESS.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    # Preparation may complete with technical-only data. This is deliberately
    # not equivalent to authorizing a performance-validation run.
    return 0 if coverage >= 0.75 else 2


if __name__ == "__main__":
    raise SystemExit(main())
