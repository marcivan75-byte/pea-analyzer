from __future__ import annotations

import argparse
import base64
import io
import json
import math
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
        timeout=20,
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
            g = g.reset_index().rename(columns={g.index.name or "index": "date", "Date": "date"})
            if "date" not in g.columns:
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
    if not chunks:
        raise DataPrepBlocked("no OHLCV downloaded from yfinance")
    ohlcv = pd.concat(chunks, ignore_index=True, sort=False)
    rename = {"adj close": "adj_close"}
    ohlcv = ohlcv.rename(columns=rename)
    needed = {"ticker", "date", "open", "high", "low", "close", "volume"}
    missing = needed.difference(ohlcv.columns)
    if missing:
        raise DataPrepBlocked(f"downloaded OHLCV missing columns {sorted(missing)}")
    ohlcv["date"] = pd.to_datetime(ohlcv["date"], errors="coerce").dt.tz_localize(None)
    for c in ("open", "high", "low", "close", "volume"):
        ohlcv[c] = pd.to_numeric(ohlcv[c], errors="coerce")
    ohlcv = ohlcv.dropna(subset=["ticker", "date", "close"]).sort_values(["ticker", "date"])
    ohlcv.to_parquet(outdir / "V22_1_OHLCV_2018_2025.parquet", index=False)
    (outdir / "V22_1_DOWNLOAD_FAILURES.json").write_text(json.dumps({"failed_batches": failed_batches}, indent=2), encoding="utf-8")
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
        tr = pd.concat([(g["high"]-g["low"]).abs(), (g["high"]-prev).abs(), (g["low"]-prev).abs()], axis=1).max(axis=1)
        g["atr_14_pct"] = tr.rolling(14, min_periods=14).mean() / close.replace(0, np.nan)
        weekly = close.resample("W-FRI").last().dropna()
        wrsi = _rsi(weekly, 14)
        g["rsi_14_hebdo"] = wrsi.reindex(g.index, method="ffill")
        friday = pd.date_range(PIT_START, PIT_END, freq="W-FRI")
        pos = g.index.searchsorted(friday, side="right") - 1
        valid = pos >= 0
        if not valid.any():
            continue
        take = g.iloc[pos[valid]].copy()
        take["as_of_date"] = friday[valid].values
        take["pit_observed_at"] = pd.to_datetime(take.index).strftime("%Y-%m-%dT17:30:00Z")
        take["ticker"] = ticker
        rows.append(take.reset_index(drop=True)[["ticker","as_of_date","pit_observed_at","close","sma20","sma200","vol_z","drawdown_4w","mom_26w","atr_14_pct","rsi_14_hebdo"]])
    if not rows:
        raise DataPrepBlocked("technical PIT generation produced no rows")
    pit = pd.concat(rows, ignore_index=True)
    pit = pit.merge(id_small, on="ticker", how="left", validate="many_to_one")
    pit = pit.dropna(subset=["isin"])
    pit.to_parquet(outdir / "V22_1_TECHNICAL_PIT_2019_2024.parquet", index=False)
    return pit


def scan_historical_nonprice_pit(root: Path) -> dict[str, object]:
    audit = root / "outputs" / "audit"
    sector_ready = False
    quality_ready = False
    evidence: list[str] = []
    if audit.is_dir():
        for path in audit.rglob("*"):
            if path.suffix.lower() not in {".csv", ".parquet", ".json", ".jsonl"}:
                continue
            name = path.name.lower()
            if "sector" in name and any(k in name for k in ("pit", "history", "histor")):
                sector_ready = True; evidence.append(str(path))
            if any(k in name for k in ("fundamental", "financial", "quality", "roe", "debt")) and any(k in name for k in ("pit", "history", "histor")):
                quality_ready = True; evidence.append(str(path))
    return {"sector_pit_ready": sector_ready, "quality_pit_ready": quality_ready, "evidence": evidence}


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
    report = {
        "status": "READY_TECHNICAL_ONLY" if not (nonprice["sector_pit_ready"] and nonprice["quality_pit_ready"]) else "READY_FULL_PIT",
        "identity_rows": int(len(identity)),
        "validated_tickers": ticker_count,
        "ohlcv_tickers": ohlcv_tickers,
        "ohlcv_ticker_coverage": coverage,
        "technical_pit_rows": int(len(pit)),
        "download_period": [START_DOWNLOAD, END_DOWNLOAD],
        "pit_period": [str(PIT_START.date()), str(PIT_END.date())],
        "technical_features_pit_reconstructable": True,
        "sector_history": nonprice["sector_pit_ready"],
        "quality_roe_debt_history": nonprice["quality_pit_ready"],
        "historical_nonprice_evidence": nonprice["evidence"],
        "final_performance_validation_authorized": bool(coverage >= 0.90 and nonprice["sector_pit_ready"] and nonprice["quality_pit_ready"]),
        "governance": {
            "no_invented_ticker": True,
            "identity_source": str(IDENTITY_PARTS),
            "current_fundamentals_not_used_as_history": True,
            "current_sector_not_used_as_history": True,
            "fail_closed": True,
        },
    }
    (outdir / "V22_1_DATA_READINESS.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if coverage >= 0.75 else 2


if __name__ == "__main__":
    raise SystemExit(main())
