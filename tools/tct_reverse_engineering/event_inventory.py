from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd

OUT = Path("outputs/tct_reverse_engineering_v1")
HORIZONS = (4, 10, 20)
THRESH = 0.20


def _norm_name(x: object) -> str:
    s = str(x).strip().lower().replace(" ", "_")
    return {"adjclose": "adj_close", "datetime": "date", "timestamp": "date", "symbol": "ticker"}.get(s, s)


def _norm_flat(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [_norm_name(c) for c in out.columns]
    return out


def _naive_dates(values) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce").dt.tz_convert(None)


def _read_pre2023() -> pd.DataFrame:
    p = Path("inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet")
    d = _norm_flat(pd.read_parquet(p))
    if "date" not in d.columns or "ticker" not in d.columns:
        raise RuntimeError(f"PRE2023 schema unsupported: {d.columns.tolist()}")
    pc = "adj_close" if "adj_close" in d.columns else "close"
    keep = ["date", "ticker", pc] + (["volume"] if "volume" in d.columns else [])
    d = d[keep].rename(columns={pc: "price"}).copy()
    d["date"] = _naive_dates(d["date"])
    d["ticker"] = d["ticker"].astype(str).str.upper()
    d["source"] = "PRE2023_GOVERNED_YAHOO"
    return d


def _date_from_index(d: pd.DataFrame) -> pd.Series:
    if isinstance(d.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(d.index, utc=True, errors="coerce").tz_convert(None), index=d.index)
    for candidate in ("date", "datetime", "timestamp"):
        if candidate in d.columns:
            return _naive_dates(d[candidate])
    return pd.Series(pd.NaT, index=d.index, dtype="datetime64[ns]")


def _long_from_multiindex(d: pd.DataFrame, file_name: str) -> tuple[list[pd.DataFrame], dict]:
    parts: list[pd.DataFrame] = []
    diag: dict = {
        "file": file_name,
        "rows": int(len(d)),
        "multiindex": True,
        "nlevels": int(d.columns.nlevels),
        "level_sizes": [int(len(pd.Index(d.columns.get_level_values(i)).unique())) for i in range(d.columns.nlevels)],
    }
    if d.columns.nlevels < 2:
        return parts, diag

    # Yahoo cache convention is normally (ticker, field). Detect the field level
    # instead of assuming position, so the reader remains stable if levels swap.
    field_words = {"open", "high", "low", "close", "adj close", "adj_close", "adjclose", "volume"}
    scores = []
    for level in range(d.columns.nlevels):
        vals = {_norm_name(v).replace("_", " ") for v in d.columns.get_level_values(level)}
        scores.append(sum(v in field_words for v in vals))
    field_level = int(np.argmax(scores))
    ticker_level = 1 - field_level if d.columns.nlevels == 2 else next(i for i in range(d.columns.nlevels) if i != field_level)
    diag["field_level"] = field_level
    diag["ticker_level"] = ticker_level

    tickers = pd.Index(d.columns.get_level_values(ticker_level)).unique()
    date_values = pd.to_datetime(d.index, utc=True, errors="coerce").tz_convert(None) if isinstance(d.index, pd.DatetimeIndex) else None
    extracted = 0
    for ticker in tickers:
        mask = d.columns.get_level_values(ticker_level) == ticker
        cols = d.columns[mask]
        if len(cols) == 0:
            continue
        sub = d.loc[:, cols].copy()
        names = [_norm_name(c[field_level]) for c in cols]
        sub.columns = names
        # Duplicate field names can occur in malformed provider responses. Keep last.
        sub = sub.loc[:, ~pd.Index(sub.columns).duplicated(keep="last")]
        pc = "adj_close" if "adj_close" in sub.columns else ("close" if "close" in sub.columns else None)
        if pc is None:
            continue
        out = pd.DataFrame(index=sub.index)
        if date_values is not None:
            out["date"] = date_values
        else:
            dates = _date_from_index(d)
            out["date"] = dates.to_numpy()
        out["ticker"] = str(ticker).upper()
        out["price"] = pd.to_numeric(sub[pc], errors="coerce").to_numpy()
        out["volume"] = pd.to_numeric(sub["volume"], errors="coerce").to_numpy() if "volume" in sub.columns else np.nan
        out["source"] = "POST2022_GOVERNED_CACHE"
        out = out[out["date"] >= pd.Timestamp("2023-01-01")]
        if not out.empty:
            parts.append(out.reset_index(drop=True))
            extracted += 1
    diag["extracted_tickers"] = extracted
    diag["output_rows"] = int(sum(len(x) for x in parts))
    return parts, diag


def _long_from_flat(d: pd.DataFrame, file_name: str) -> tuple[list[pd.DataFrame], dict]:
    d = _norm_flat(d)
    diag = {"file": file_name, "rows": int(len(d)), "multiindex": False, "columns": d.columns.tolist()}
    if d.empty:
        return [], diag
    if "date" not in d.columns and isinstance(d.index, pd.DatetimeIndex):
        d = d.reset_index()
        d = _norm_flat(d)
        if "index" in d.columns and "date" not in d.columns:
            d = d.rename(columns={"index": "date"})
    pc = "adj_close" if "adj_close" in d.columns else ("close" if "close" in d.columns else None)
    if pc is None or "date" not in d.columns:
        diag["output_rows"] = 0
        return [], diag
    if "ticker" not in d.columns:
        d["ticker"] = file_name.removeprefix("history_").removesuffix(".parquet")
    keep = ["date", "ticker", pc] + (["volume"] if "volume" in d.columns else [])
    out = d[keep].rename(columns={pc: "price"}).copy()
    if "volume" not in out.columns:
        out["volume"] = np.nan
    out["date"] = _naive_dates(out["date"])
    out["ticker"] = out["ticker"].astype(str).str.upper()
    out["source"] = "POST2022_GOVERNED_CACHE"
    out = out[out["date"] >= pd.Timestamp("2023-01-01")]
    diag["output_rows"] = int(len(out))
    return ([out] if not out.empty else []), diag


def _read_post2022() -> tuple[pd.DataFrame, list[dict], int]:
    parts: list[pd.DataFrame] = []
    audit: list[dict] = []
    files = sorted(Path("data/cache/actions").glob("history_*.parquet"))
    for p in files:
        try:
            raw = pd.read_parquet(p)
            if isinstance(raw.columns, pd.MultiIndex):
                ps, diag = _long_from_multiindex(raw, p.name)
            else:
                ps, diag = _long_from_flat(raw, p.name)
            parts.extend(ps)
            audit.append(diag)
        except Exception as exc:
            audit.append({"file": p.name, "error": repr(exc)})
    if not parts:
        return pd.DataFrame(columns=["date", "ticker", "price", "volume", "source"]), audit, len(files)
    return pd.concat(parts, ignore_index=True, sort=False), audit, len(files)


def _event_inventory(allp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw: list[dict] = []
    clustered: list[dict] = []
    coverage: list[dict] = []

    for ticker, g in allp.groupby("ticker", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        px = g["price"].to_numpy(float)
        dates = g["date"].to_numpy()
        n = len(g)
        if n < 5:
            continue
        coverage.append({
            "ticker": ticker,
            "min_date": str(g["date"].min().date()),
            "max_date": str(g["date"].max().date()),
            "rows": n,
            "volume_nonnull_pct": float(g["volume"].notna().mean() * 100) if "volume" in g else 0.0,
        })
        for H in HORIZONS:
            qual: list[int] = []
            meta: list[tuple[int, float]] = []
            for i in range(n - 1):
                j2 = min(n, i + H + 1)
                fut = px[i + 1 : j2]
                if fut.size == 0 or not np.isfinite(fut).any() or not np.isfinite(px[i]) or px[i] <= 0:
                    continue
                rel = fut / px[i] - 1.0
                k = int(np.nanargmax(rel))
                m = float(rel[k])
                if m > THRESH:
                    peak_i = i + 1 + k
                    qual.append(i)
                    meta.append((peak_i, m))
                    raw.append({
                        "ticker": ticker, "j0": pd.Timestamp(dates[i]), "horizon_sessions": H,
                        "max_forward_return": m, "peak_date": pd.Timestamp(dates[peak_i]),
                        "sessions_to_peak": peak_i - i, "j0_price": px[i], "peak_price": px[peak_i],
                    })
            last_suppressed = -1
            for i, (peak_i, m) in zip(qual, meta):
                if i <= last_suppressed:
                    continue
                clustered.append({
                    "ticker": ticker, "j0": pd.Timestamp(dates[i]), "horizon_sessions": H,
                    "max_forward_return": m, "peak_date": pd.Timestamp(dates[peak_i]),
                    "sessions_to_peak": peak_i - i, "j0_price": px[i], "peak_price": px[peak_i],
                })
                last_suppressed = peak_i

    return pd.DataFrame(raw), pd.DataFrame(clustered), pd.DataFrame(coverage)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pre = _read_pre2023()
    post, schema_audit, cache_file_count = _read_post2022()

    allp = pd.concat([pre, post], ignore_index=True, sort=False)
    allp["ticker"] = allp["ticker"].astype(str).str.upper()
    allp["price"] = pd.to_numeric(allp["price"], errors="coerce")
    allp["volume"] = pd.to_numeric(allp.get("volume"), errors="coerce")
    allp = allp.dropna(subset=["date", "ticker", "price"])
    allp = allp[allp["price"] > 0]
    allp = allp.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last").reset_index(drop=True)

    raw, epi, cov = _event_inventory(allp)
    for d in (raw, epi):
        if not d.empty:
            d["year"] = pd.to_datetime(d["j0"]).dt.year
            d["max_forward_return_pct"] = 100.0 * d["max_forward_return"]

    raw.to_csv(OUT / "TCT_GT20_RAW_DATES_2010_2026.csv", index=False)
    epi.to_csv(OUT / "TCT_GT20_CLUSTERED_EPISODES_2010_2026.csv", index=False)
    cov.to_csv(OUT / "TCT_HISTORY_COVERAGE_2010_2026.csv", index=False)
    (OUT / "POST2022_CACHE_SCHEMA_AUDIT.json").write_text(json.dumps(schema_audit, indent=2, default=str), encoding="utf-8")

    annual = (epi.groupby(["year", "horizon_sessions"])
              .agg(episodes=("ticker", "size"), unique_tickers=("ticker", "nunique"),
                   median_max_return_pct=("max_forward_return_pct", "median"),
                   median_sessions_to_peak=("sessions_to_peak", "median"))
              .reset_index()) if not epi.empty else pd.DataFrame(columns=["year", "horizon_sessions", "episodes", "unique_tickers"])
    annual.to_csv(OUT / "TCT_GT20_ANNUAL_INVENTORY_2010_2026.csv", index=False)
    if not annual.empty:
        pivot = annual.pivot(index="year", columns="horizon_sessions", values=["episodes", "unique_tickers"])
        pivot.columns = [f"{a}_H{b}" for a, b in pivot.columns]
        pivot.reset_index().to_csv(OUT / "TCT_GT20_ANNUAL_PIVOT_2010_2026.csv", index=False)

    manifest = json.loads(Path("inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json").read_text())
    summary = {
        "status": "SUCCESS",
        "threshold_rule": "strict max forward adjusted/available price return > 20%",
        "horizons_sessions": list(HORIZONS),
        "dedup_rule": "first qualifying J0 retained; subsequent qualifying J0s through retained event peak suppressed, separately by ticker and horizon",
        "pre2023_manifest": manifest,
        "post2022_cache_files": cache_file_count,
        "post2022_rows": int(len(post)),
        "post2022_tickers": int(post["ticker"].nunique()) if not post.empty else 0,
        "post2022_min_date": str(post["date"].min()) if not post.empty else None,
        "post2022_max_date": str(post["date"].max()) if not post.empty else None,
        "combined_rows": int(len(allp)),
        "combined_tickers": int(allp["ticker"].nunique()),
        "combined_min_date": str(allp["date"].min()),
        "combined_max_date": str(allp["date"].max()),
        "raw_positive_dates": int(len(raw)),
        "clustered_episodes": int(len(epi)),
        "governance_warning": {
            "historical_universe_certified": bool(manifest.get("historical_universe_certified", False)),
            "survivorship_safe": bool(manifest.get("survivorship_safe", False)),
            "historical_pea_eligibility_certified": bool(manifest.get("historical_pea_eligibility_certified", False)),
            "use": "DISCOVERY_ONLY_UNTIL_SURVIVORSHIP_AND_HISTORICAL_PEA_MEMBERSHIP_ARE_CERTIFIED",
        },
    }
    (OUT / "TCT_EVENT_INVENTORY_SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print("---ANNUAL---")
    print(annual.to_csv(index=False))


if __name__ == "__main__":
    main()
