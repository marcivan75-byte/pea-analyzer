from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

from scripts.pre2023_yahoo_probe import (
    START, END_EXCLUSIVE, MAX_ALLOWED_DATE,
    _load_current_cache_tickers, _extract_ticker_frame,
)


def _geometry(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    idx = pd.to_datetime(frame.index, errors="coerce")
    frame = frame.loc[~idx.isna()].copy()
    idx = pd.DatetimeIndex(idx[~idx.isna()])
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    frame.index = idx
    if len(frame) and frame.index.max() > MAX_ALLOWED_DATE:
        raise SystemExit(f"BLOCK_YAHOO_HOLDOUT_LEAK:{frame.index.max()}")
    cols = {str(c).strip().lower(): c for c in frame.columns}
    if not all(k in cols for k in ("open","high","low","close")):
        return pd.Series(False, index=frame.index)
    o = pd.to_numeric(frame[cols["open"]], errors="coerce")
    h = pd.to_numeric(frame[cols["high"]], errors="coerce")
    l = pd.to_numeric(frame[cols["low"]], errors="coerce")
    c = pd.to_numeric(frame[cols["close"]], errors="coerce")
    observed = o.notna() & h.notna() & l.notna() & c.notna()
    return observed & ((h<l)|(h<o)|(h<c)|(l>o)|(l>c)|(o<=0)|(h<=0)|(l<=0)|(c<=0))


def main() -> None:
    import yfinance as yf

    out = Path("outputs/pre2023_yahoo_repair_audit")
    out.mkdir(parents=True, exist_ok=True)
    tickers = _load_current_cache_tickers(Path("data/cache/actions"))[:120]
    rows=[]
    for ticker in tickers:
        try:
            raw = yf.download(
                tickers=ticker, start=START, end=END_EXCLUSIVE, interval="1d",
                auto_adjust=False, actions=True, repair=False, progress=False,
                threads=False, timeout=20,
            )
            repaired = yf.download(
                tickers=ticker, start=START, end=END_EXCLUSIVE, interval="1d",
                auto_adjust=False, actions=True, repair=True, progress=False,
                threads=False, timeout=20,
            )
        except Exception as exc:
            rows.append({"ticker":ticker,"status":"REQUEST_ERROR","detail":f"{type(exc).__name__}:{str(exc)[:160]}"})
            continue

        rawf = _extract_ticker_frame(raw,ticker)
        repf = _extract_ticker_frame(repaired,ticker)
        raw_bad = _geometry(rawf)
        rep_bad = _geometry(repf)
        raw_count = int(raw_bad.sum()) if len(raw_bad) else 0
        rep_count = int(rep_bad.sum()) if len(rep_bad) else 0

        raw_dates = {d.date().isoformat() for d in raw_bad.index[raw_bad]} if len(raw_bad) else set()
        rep_dates = {d.date().isoformat() for d in rep_bad.index[rep_bad]} if len(rep_bad) else set()
        repaired_flag_rows = 0
        if not repf.empty:
            cols = {str(c).strip().lower(): c for c in repf.columns}
            if "repaired?" in cols:
                repaired_flag_rows = int(repf[cols["repaired?"]].fillna(False).astype(bool).sum())

        rows.append({
            "ticker":ticker,
            "status":"OK" if (not rawf.empty or not repf.empty) else "NO_HISTORY",
            "raw_rows":int(len(rawf)),"repaired_rows":int(len(repf)),
            "raw_invalid":raw_count,"repaired_invalid":rep_count,
            "invalid_resolved":int(len(raw_dates-rep_dates)),
            "invalid_persisting":int(len(raw_dates & rep_dates)),
            "new_invalid_after_repair":int(len(rep_dates-raw_dates)),
            "provider_repaired_flag_rows":repaired_flag_rows,
        })

    df = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    df.to_csv(out/"PRE2023_YAHOO_REPAIR_COMPARISON.csv",index=False)
    ok=df[df.status.eq("OK")]
    summary={
        "purpose":"SOURCE_QUALITY_DIAGNOSTIC_ONLY",
        "repair_promoted":False,
        "historical_universe_certified":False,
        "survivorship_safe":False,
        "retuning":False,
        "holdout_accessed_for_prices":False,
        "sample_tickers":int(len(df)),
        "ok_tickers":int(len(ok)),
        "raw_invalid":int(pd.to_numeric(ok.raw_invalid,errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "repaired_invalid":int(pd.to_numeric(ok.repaired_invalid,errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "invalid_resolved":int(pd.to_numeric(ok.invalid_resolved,errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "invalid_persisting":int(pd.to_numeric(ok.invalid_persisting,errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "new_invalid_after_repair":int(pd.to_numeric(ok.new_invalid_after_repair,errors="coerce").fillna(0).sum()) if len(ok) else 0,
        "provider_repaired_flag_rows":int(pd.to_numeric(ok.provider_repaired_flag_rows,errors="coerce").fillna(0).sum()) if len(ok) else 0,
    }
    (out/"PRE2023_YAHOO_REPAIR_AUDIT.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    main()
