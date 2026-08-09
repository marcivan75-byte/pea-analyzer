from __future__ import annotations

from pathlib import Path
import re

import pandas as pd

from .core import CaptureStore, clean_text, number, utcnow

ALIASES = {
    "isin": ["isin", "codeisin", "code_isin"],
    "date": ["date", "datetime", "jour", "timestamp"],
    "open": ["open", "ouverture", "opening"],
    "high": ["high", "haut", "plushaut", "plus_haut"],
    "low": ["low", "bas", "plusbas", "plus_bas"],
    "close": ["close", "cloture", "cours", "dernier", "last"],
    "volume": ["volume", "volumes", "quantite", "quantity"],
    "ticker": ["ticker", "symbol", "symbole", "code"],
}


def _norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _column(df: pd.DataFrame, name: str) -> str | None:
    norms = {c: _norm(c) for c in df.columns}
    wanted = {_norm(x) for x in ALIASES[name]}
    return next((c for c, n in norms.items() if n in wanted), None)


def _read(path: Path) -> pd.DataFrame | None:
    for sep in [";", ",", "\t", "|"]:
        try:
            d = pd.read_csv(path, sep=sep, dtype=object, encoding="utf-8-sig", low_memory=False)
            if len(d.columns) >= 4:
                return d
        except Exception:
            pass
    return None


def _source(path: Path) -> str:
    name = path.name.lower()
    if name.startswith("abc") or "abcbourse" in name:
        return "ABC_BOURSE_IMPORT"
    if name.startswith("prt") or "prorealtime" in name:
        return "PROREALTIME_IMPORT"
    return "MANUAL_FREE_IMPORT"


def capture(universe: pd.DataFrame, store: CaptureStore, input_root: Path) -> dict:
    if not input_root.exists():
        store.add_health("MANUAL_FREE_IMPORT", "NO_INPUT_DIRECTORY", message=str(input_root))
        return {"status": "NO_INPUT_DIRECTORY", "files": 0, "rows_added": 0}
    by_ticker = {}
    for _, row in universe.iterrows():
        isin = str(row["isin"])
        for c in ["yahoo_ticker", "euronext_symbol"]:
            t = clean_text(row.get(c)).upper()
            if t:
                by_ticker[t] = isin
                by_ticker[t.split(".")[0]] = isin
    rows = []
    files = 0
    rejected = 0
    for path in sorted(input_root.glob("*")):
        if path.suffix.lower() not in {".csv", ".txt"}:
            continue
        d = _read(path)
        if d is None:
            continue
        date_c, close_c = _column(d, "date"), _column(d, "close")
        if not date_c or not close_c:
            continue
        isin_c, ticker_c = _column(d, "isin"), _column(d, "ticker")
        if not isin_c and not ticker_c:
            continue
        files += 1
        source = _source(path)
        for _, r in d.iterrows():
            isin = clean_text(r.get(isin_c)) if isin_c else ""
            ticker = clean_text(r.get(ticker_c)).upper() if ticker_c else ""
            if not isin and ticker:
                isin = by_ticker.get(ticker) or by_ticker.get(ticker.split(".")[0], "")
            if not re.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", isin):
                rejected += 1
                continue
            day = pd.to_datetime(r.get(date_c), errors="coerce", dayfirst=True)
            close = number(r.get(close_c))
            if pd.isna(day) or close is None:
                rejected += 1
                continue
            def val(name: str):
                c = _column(d, name)
                return number(r.get(c)) if c else None
            rows.append({
                "isin": isin, "date": day.date().isoformat(), "open": val("open"), "high": val("high"),
                "low": val("low"), "close": close, "volume": val("volume"), "currency": "",
                "source": source, "ticker": ticker, "mic": "", "observed_at_utc": utcnow(),
            })
    added = store.upsert_market(rows)
    store.add_health("MANUAL_FREE_IMPORT", "OK" if added else "NO_NEW_DATA", len(rows) + rejected, added, rejected,
                     message=f"files={files}; input={input_root}")
    return {"status": "OK", "files": files, "parsed_rows": len(rows), "rows_added": added, "rejected": rejected}
