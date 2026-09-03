from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd

OUT = Path("outputs/tct_reverse_engineering_v1")
HORIZONS = (4, 10, 20)
THRESH = 0.20
SCALE_BREAK_GROSS = 10.0  # quality quarantine only; not a trading rule


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
        sub.columns = [_norm_name(c[field_level]) for c in cols]
        sub = sub.loc[:, ~pd.Index(sub.columns).duplicated(keep="last")]
        pc = "adj_close" if "adj_close" in sub.columns else ("close" if "close" in sub.columns else None)
        if pc is None:
            continue
        out = pd.DataFrame(index=sub.index)
        out["date"] = date_values if date_values is not None else _date_from_index(d).to_numpy()
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
        d = _norm_flat(d.reset_index())
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
            ps, diag = _long_from_multiindex(raw, p.name) if isinstance(raw.columns, pd.MultiIndex) else _long_from_flat(raw, p.name)
            parts.extend(ps)
            audit.append(diag)
        except Exception as exc:
            audit.append({"file": p.name, "error": repr(exc)})
    if not parts:
        return pd.DataFrame(columns=["date", "ticker", "price", "volume", "source"]), audit, len(files)
    return pd.concat(parts, ignore_index=True, sort=False), audit, len(files)


def _analyze_history(allp: pd.DataFrame):
    raw: list[dict] = []
    clustered: list[dict] = []
    master: list[dict] = []
    coverage: list[dict] = []
    base_rows: list[dict] = []
    scale_break_rows: list[dict] = []

    for ticker, g in allp.groupby("ticker", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        px = g["price"].to_numpy(float)
        dates = g["date"].to_numpy()
        n = len(g)
        if n < 21:
            continue
        gross = np.full(n, np.nan)
        gross[1:] = px[1:] / px[:-1]
        break_at = np.isfinite(gross) & ((gross >= SCALE_BREAK_GROSS) | (gross <= 1.0 / SCALE_BREAK_GROSS))
        break_prefix = np.cumsum(break_at.astype(int))
        for j in np.flatnonzero(break_at):
            scale_break_rows.append({
                "ticker": ticker, "date": pd.Timestamp(dates[j]), "prior_date": pd.Timestamp(dates[j-1]),
                "prior_price": px[j-1], "price": px[j], "gross_ratio": gross[j],
            })

        coverage.append({
            "ticker": ticker, "min_date": str(g["date"].min().date()), "max_date": str(g["date"].max().date()),
            "rows": n, "volume_nonnull_pct": float(g["volume"].notna().mean() * 100),
            "scale_break_count": int(break_at.sum()),
        })

        # Cache complete-horizon metrics per J0 to construct one cross-horizon master rally dataset.
        metrics: dict[tuple[int, int], dict] = {}
        for H in HORIZONS:
            qual: list[int] = []
            meta: list[tuple[int, float, int]] = []
            eligible_by_year: dict[int, int] = {}
            positives_by_year: dict[int, int] = {}
            quarantined_by_year: dict[int, int] = {}

            # Complete horizon is mandatory: exactly H future observations must exist.
            for i in range(0, n - H):
                year = pd.Timestamp(dates[i]).year
                # A quality break at positions i+1...i+H contaminates the forward window.
                breaks = break_prefix[i + H] - break_prefix[i]
                if breaks > 0:
                    quarantined_by_year[year] = quarantined_by_year.get(year, 0) + 1
                    continue
                if not np.isfinite(px[i]) or px[i] <= 0:
                    continue
                fut = px[i + 1 : i + H + 1]
                if fut.size != H or not np.isfinite(fut).all():
                    continue
                eligible_by_year[year] = eligible_by_year.get(year, 0) + 1
                rel = fut / px[i] - 1.0
                k = int(np.argmax(rel))
                mfe = float(rel[k])
                mae = float(np.min(rel))
                hit_positions = np.flatnonzero(rel > THRESH)
                first_passage = int(hit_positions[0] + 1) if hit_positions.size else None
                peak_i = i + 1 + k
                metrics[(i, H)] = {
                    "mfe": mfe, "mae": mae, "peak_i": peak_i, "sessions_to_peak": peak_i - i,
                    "first_passage": first_passage, "hit": bool(mfe > THRESH),
                }
                if mfe > THRESH:
                    positives_by_year[year] = positives_by_year.get(year, 0) + 1
                    qual.append(i)
                    meta.append((peak_i, mfe, first_passage or 0))
                    raw.append({
                        "ticker": ticker, "j0": pd.Timestamp(dates[i]), "horizon_sessions": H,
                        "max_forward_return": mfe, "mae_forward_return": mae,
                        "peak_date": pd.Timestamp(dates[peak_i]), "sessions_to_peak": peak_i - i,
                        "first_passage_gt20_sessions": first_passage,
                        "j0_price": px[i], "peak_price": px[peak_i],
                    })

            years = set(eligible_by_year) | set(positives_by_year) | set(quarantined_by_year)
            for year in sorted(years):
                elig = eligible_by_year.get(year, 0)
                pos = positives_by_year.get(year, 0)
                base_rows.append({
                    "year": year, "horizon_sessions": H, "eligible_observations": elig,
                    "positive_observations": pos, "positive_rate_pct": 100.0 * pos / elig if elig else np.nan,
                    "quarantined_scale_break_windows": quarantined_by_year.get(year, 0),
                })

            last_suppressed = -1
            for i, (peak_i, mfe, fp) in zip(qual, meta):
                if i <= last_suppressed:
                    continue
                m = metrics[(i, H)]
                clustered.append({
                    "ticker": ticker, "j0": pd.Timestamp(dates[i]), "horizon_sessions": H,
                    "max_forward_return": mfe, "mae_forward_return": m["mae"],
                    "peak_date": pd.Timestamp(dates[peak_i]), "sessions_to_peak": peak_i - i,
                    "first_passage_gt20_sessions": fp,
                    "j0_price": px[i], "peak_price": px[peak_i],
                })
                last_suppressed = peak_i

        # Master economic episodes = H20 clustered episodes. At its single J0, attach all horizon outcomes.
        h20_qual = []
        for i in range(0, n - 20):
            m20 = metrics.get((i, 20))
            if m20 and m20["hit"]:
                h20_qual.append(i)
        last_suppressed = -1
        for i in h20_qual:
            m20 = metrics[(i, 20)]
            if i <= last_suppressed:
                continue
            row = {
                "ticker": ticker, "j0": pd.Timestamp(dates[i]), "j0_price": px[i],
                "peak20_date": pd.Timestamp(dates[m20["peak_i"]]),
                "mfe20_pct": 100.0 * m20["mfe"], "mae20_pct": 100.0 * m20["mae"],
                "first_passage_gt20_sessions": m20["first_passage"],
            }
            for H in HORIZONS:
                m = metrics.get((i, H))
                row[f"hit20_h{H}"] = bool(m and m["hit"])
                row[f"mfe_h{H}_pct"] = 100.0 * m["mfe"] if m else np.nan
                row[f"mae_h{H}_pct"] = 100.0 * m["mae"] if m else np.nan
            master.append(row)
            last_suppressed = m20["peak_i"]

    return (pd.DataFrame(raw), pd.DataFrame(clustered), pd.DataFrame(master), pd.DataFrame(coverage),
            pd.DataFrame(base_rows), pd.DataFrame(scale_break_rows))


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

    raw, epi, master, cov, base, scale_breaks = _analyze_history(allp)
    for d in (raw, epi, master):
        if not d.empty:
            d["year"] = pd.to_datetime(d["j0"]).dt.year
    for d in (raw, epi):
        if not d.empty:
            d["max_forward_return_pct"] = 100.0 * d["max_forward_return"]
            d["mae_forward_return_pct"] = 100.0 * d["mae_forward_return"]

    raw.to_csv(OUT / "TCT_GT20_RAW_DATES_2010_2026.csv", index=False)
    epi.to_csv(OUT / "TCT_GT20_CLUSTERED_EPISODES_2010_2026.csv", index=False)
    master.to_csv(OUT / "TCT_GT20_MASTER_EPISODES_2010_2026.csv", index=False)
    cov.to_csv(OUT / "TCT_HISTORY_COVERAGE_2010_2026.csv", index=False)
    base.to_csv(OUT / "TCT_GT20_BASE_RATES_2010_2026.csv", index=False)
    scale_breaks.to_csv(OUT / "TCT_SCALE_BREAK_QUARANTINE_2010_2026.csv", index=False)
    (OUT / "POST2022_CACHE_SCHEMA_AUDIT.json").write_text(json.dumps(schema_audit, indent=2, default=str), encoding="utf-8")

    annual = (epi.groupby(["year", "horizon_sessions"])
              .agg(episodes=("ticker", "size"), unique_tickers=("ticker", "nunique"),
                   median_max_return_pct=("max_forward_return_pct", "median"),
                   median_first_passage=("first_passage_gt20_sessions", "median"),
                   median_mae_pct=("mae_forward_return_pct", "median"))
              .reset_index()) if not epi.empty else pd.DataFrame()
    annual.to_csv(OUT / "TCT_GT20_ANNUAL_INVENTORY_2010_2026.csv", index=False)

    master_annual = (master.groupby("year")
                     .agg(master_episodes=("ticker", "size"), unique_tickers=("ticker", "nunique"),
                          hit_h4_pct=("hit20_h4", lambda x: 100.0 * x.mean()),
                          hit_h10_pct=("hit20_h10", lambda x: 100.0 * x.mean()),
                          median_mfe20_pct=("mfe20_pct", "median"), median_mae20_pct=("mae20_pct", "median"),
                          median_first_passage=("first_passage_gt20_sessions", "median"))
                     .reset_index()) if not master.empty else pd.DataFrame()
    master_annual.to_csv(OUT / "TCT_GT20_MASTER_ANNUAL_2010_2026.csv", index=False)

    manifest = json.loads(Path("inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json").read_text())
    summary = {
        "status": "SUCCESS",
        "threshold_rule": "strict max forward adjusted/available price return > 20%",
        "complete_horizon_required": True,
        "horizons_sessions": list(HORIZONS),
        "quality_quarantine": f"exclude forward windows crossing adjacent gross price ratio >= {SCALE_BREAK_GROSS}x or <= 1/{SCALE_BREAK_GROSS}",
        "dedup_rule": "per-horizon first qualifying J0 retained through observed peak; master episodes use H20 clusters with H4/H10/H20 labels at same J0",
        "pre2023_manifest": manifest,
        "post2022_cache_files": cache_file_count,
        "post2022_rows": int(len(post)), "post2022_tickers": int(post["ticker"].nunique()) if not post.empty else 0,
        "post2022_min_date": str(post["date"].min()) if not post.empty else None,
        "post2022_max_date": str(post["date"].max()) if not post.empty else None,
        "combined_rows": int(len(allp)), "combined_tickers": int(allp["ticker"].nunique()),
        "combined_min_date": str(allp["date"].min()), "combined_max_date": str(allp["date"].max()),
        "scale_breaks_quarantined": int(len(scale_breaks)),
        "raw_positive_dates": int(len(raw)), "clustered_horizon_episodes": int(len(epi)),
        "master_h20_episodes": int(len(master)),
        "governance_warning": {
            "historical_universe_certified": bool(manifest.get("historical_universe_certified", False)),
            "survivorship_safe": bool(manifest.get("survivorship_safe", False)),
            "historical_pea_eligibility_certified": bool(manifest.get("historical_pea_eligibility_certified", False)),
            "use": "DISCOVERY_ONLY_UNTIL_SURVIVORSHIP_AND_HISTORICAL_PEA_MEMBERSHIP_ARE_CERTIFIED",
        },
    }
    (OUT / "TCT_EVENT_INVENTORY_SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print("---ANNUAL HORIZONS---")
    print(annual.to_csv(index=False))
    print("---BASE RATES---")
    print(base.to_csv(index=False))
    print("---MASTER ANNUAL---")
    print(master_annual.to_csv(index=False))


if __name__ == "__main__":
    main()
