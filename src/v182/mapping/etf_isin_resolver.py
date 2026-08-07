from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import os
import time

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

EXCHANGE_TO_YAHOO_SUFFIX = {
    "PA": "PA", "AS": "AS", "BR": "BR", "LS": "LS", "MI": "MI",
    "GR": "DE", "GY": "DE", "SW": "SW", "VI": "VI", "IR": "IR",
    "HE": "HE", "CO": "CO", "ST": "ST", "OS": "OL", "LN": "L",
}

EXCHANGE_TO_MIC = {
    "PA": "XPAR", "AS": "XAMS", "BR": "XBRU", "LS": "XLIS", "MI": "XMIL",
    "GR": "XETR", "GY": "XETR", "SW": "XSWX", "VI": "XWBO", "IR": "XDUB",
    "HE": "XHEL", "CO": "XCSE", "ST": "XSTO", "OS": "XOSL", "LN": "XLON",
}

EXCHANGE_PREFERENCE = ["PA", "AS", "BR", "MI", "GR", "GY", "LS", "IR", "SW", "LN", "VI", "HE", "CO", "ST", "OS"]
YAHOO_SUFFIX_TO_EXCHANGE = {
    "PA": "PA", "AS": "AS", "BR": "BR", "LS": "LS", "MI": "MI",
    "DE": "GR", "SW": "SW", "VI": "VI", "IR": "IR", "HE": "HE",
    "CO": "CO", "ST": "ST", "OL": "OS", "L": "LN",
}

MASTER_MAP_COLUMNS = [
    "universe", "isin", "original_yahoo_ticker", "openfigi_ticker",
    "openfigi_exch_code", "openfigi_mic", "yahoo_candidate", "figi",
    "composite_figi", "share_class_figi", "status", "updated_at",
]


def _batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _preferred_exchange(yahoo_ticker: str | None) -> str | None:
    text = str(yahoo_ticker or "").strip().upper()
    if "." not in text:
        return None
    suffix = text.rsplit(".", 1)[-1]
    return YAHOO_SUFFIX_TO_EXCHANGE.get(suffix)


def resolve_isins(
    isins: list[str],
    api_key: str | None = None,
    batch_size: int | None = None,
    delay_seconds: float | None = None,
    max_retries: int = 3,
) -> dict[str, list[dict]]:
    """Resolve ISINs through OpenFIGI v3 with authenticated high-throughput
    batches when an API key is available. 429/5xx responses are retried using
    the server rate-limit reset header when present.
    """
    import requests

    key = api_key or os.environ.get("OPENFIGI_API_KEY")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key

    # OpenFIGI v3 currently accepts up to 100 jobs with a key. Anonymous mode
    # is intentionally conservative because its documented limit is lower.
    effective_batch = int(batch_size or (100 if key else 5))
    effective_delay = float(delay_seconds if delay_seconds is not None else (0.30 if key else 2.50))
    clean_isins = sorted({str(i).strip() for i in isins if str(i or "").strip()})
    results: dict[str, list[dict]] = {}

    for batch in _batches(clean_isins, effective_batch):
        payload = [{"idType": "ID_ISIN", "idValue": isin} for isin in batch]
        body = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(OPENFIGI_URL, headers=headers, json=payload, timeout=30)
                if resp.status_code == 429:
                    reset = resp.headers.get("ratelimit-reset") or resp.headers.get("Retry-After")
                    wait = float(reset) if reset and str(reset).replace(".", "", 1).isdigit() else max(1.0, effective_delay * (attempt + 2))
                    time.sleep(wait + 0.15)
                    continue
                if resp.status_code in {500, 502, 503, 504}:
                    time.sleep(max(1.0, 2 ** attempt))
                    continue
                resp.raise_for_status()
                body = resp.json()
                break
            except Exception:
                if attempt >= max_retries:
                    break
                time.sleep(max(1.0, 2 ** attempt))

        if not isinstance(body, list):
            for isin in batch:
                results[isin] = []
        else:
            for isin, entry in zip(batch, body):
                if not isinstance(entry, dict) or entry.get("warning") or entry.get("error"):
                    results[isin] = []
                else:
                    results[isin] = entry.get("data", []) or []
        time.sleep(effective_delay)

    return results


def pick_best_match(matches: list[dict], preferred_exchange: str | None = None) -> dict | None:
    """Pick a supported European listing, preferring the exchange already
    encoded in the Yahoo ticker when possible.
    """
    supported = [m for m in matches if m.get("exchCode") in EXCHANGE_TO_YAHOO_SUFFIX and m.get("ticker")]
    if preferred_exchange:
        for match in supported:
            if match.get("exchCode") == preferred_exchange:
                return match
    by_exchange = {m.get("exchCode"): m for m in supported}
    for exch in EXCHANGE_PREFERENCE:
        if exch in by_exchange:
            return by_exchange[exch]
    return None


def _row_from_match(universe: str, isin: str, original_ticker: str, match: dict | None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    if not match:
        return {
            "universe": universe, "isin": isin, "original_yahoo_ticker": original_ticker,
            "openfigi_ticker": "", "openfigi_exch_code": "", "openfigi_mic": "",
            "yahoo_candidate": "", "figi": "", "composite_figi": "", "share_class_figi": "",
            "status": "NO_SUPPORTED_MATCH", "updated_at": now,
        }
    exch = match.get("exchCode", "")
    ticker = str(match.get("ticker") or "").strip()
    suffix = EXCHANGE_TO_YAHOO_SUFFIX.get(exch, "")
    yahoo_candidate = f"{ticker}.{suffix}" if ticker and suffix else ""
    return {
        "universe": universe, "isin": isin, "original_yahoo_ticker": original_ticker,
        "openfigi_ticker": ticker, "openfigi_exch_code": exch,
        "openfigi_mic": EXCHANGE_TO_MIC.get(exch, ""), "yahoo_candidate": yahoo_candidate,
        "figi": match.get("figi", "") or "", "composite_figi": match.get("compositeFIGI", "") or "",
        "share_class_figi": match.get("shareClassFIGI", "") or "", "status": "RESOLVED",
        "updated_at": now,
    }


def build_openfigi_master_map(actions_df, etf_df, output_path: str | Path, api_key: str | None = None) -> dict:
    """Build/persist one OpenFIGI cache for both universes. Existing ISINs are
    not requested again, making authenticated OpenFIGI a low-cost identifier
    service rather than a repeated market-data call.
    """
    import pandas as pd

    output = Path(output_path)
    if output.exists():
        existing = pd.read_csv(output, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
        for col in MASTER_MAP_COLUMNS:
            if col not in existing.columns:
                existing[col] = ""
        existing = existing[MASTER_MAP_COLUMNS]
    else:
        existing = pd.DataFrame(columns=MASTER_MAP_COLUMNS)

    source_rows = []
    for universe, frame in (("ACTION", actions_df), ("ETF", etf_df)):
        if "isin" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            isin = str(row.get("isin") or "").strip()
            if not isin:
                continue
            source_rows.append({
                "universe": universe,
                "isin": isin,
                "original_yahoo_ticker": str(row.get("yahoo_ticker") or "").strip(),
            })

    source = pd.DataFrame(source_rows).drop_duplicates(["universe", "isin"])
    existing_keys = set(zip(existing["universe"], existing["isin"])) if not existing.empty else set()
    missing = source[[ (u, i) not in existing_keys for u, i in zip(source["universe"], source["isin"]) ]]
    unique_isins = missing["isin"].dropna().astype(str).unique().tolist()
    resolved = resolve_isins(unique_isins, api_key=api_key) if unique_isins else {}

    new_rows = []
    for _, row in missing.iterrows():
        isin = row["isin"]
        original = row["original_yahoo_ticker"]
        best = pick_best_match(resolved.get(isin, []), _preferred_exchange(original))
        new_rows.append(_row_from_match(row["universe"], isin, original, best))

    updated = pd.concat([existing, pd.DataFrame(new_rows, columns=MASTER_MAP_COLUMNS)], ignore_index=True)
    if not updated.empty:
        updated = updated.drop_duplicates(["universe", "isin"], keep="last").sort_values(["universe", "isin"])
    output.parent.mkdir(parents=True, exist_ok=True)
    updated.to_csv(output, sep=";", index=False, encoding="utf-8-sig")

    resolved_count = int((updated["status"] == "RESOLVED").sum()) if not updated.empty else 0
    return {
        "records": len(updated), "resolved": resolved_count,
        "coverage_pct": round(resolved_count / len(updated) * 100, 2) if len(updated) else 100.0,
        "api_isins_requested": len(unique_isins), "new_records": len(new_rows),
        "authenticated": bool(api_key or os.environ.get("OPENFIGI_API_KEY")),
    }


def fallback_specs(master_df, failed_tickers: list[str], openfigi_map_path: str | Path, universe: str) -> dict[str, dict]:
    """Return OpenFIGI-derived Yahoo and Marketstack identifiers for Yahoo
    tickers that failed to produce usable OHLCV.
    """
    import pandas as pd

    path = Path(openfigi_map_path)
    if not path.exists() or not failed_tickers:
        return {}
    mapping = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    mapping = mapping[(mapping["universe"] == universe) & (mapping["status"] == "RESOLVED")]
    by_isin = mapping.drop_duplicates("isin").set_index("isin").to_dict("index")
    failed = set(failed_tickers)
    specs: dict[str, dict] = {}
    for _, row in master_df.iterrows():
        original = str(row.get("yahoo_ticker") or "").strip()
        if original not in failed:
            continue
        isin = str(row.get("isin") or "").strip()
        mapped = by_isin.get(isin)
        if not mapped:
            continue
        specs[original] = {
            "isin": isin,
            "yahoo_candidate": mapped.get("yahoo_candidate", ""),
            "marketstack_symbol": mapped.get("openfigi_ticker", ""),
            "marketstack_mic": mapped.get("openfigi_mic", ""),
            "figi": mapped.get("figi", ""),
        }
    return specs


def build_etf_ticker_map(etf_master_path: str | Path, output_map_path: str | Path,
                          gaps_path: str | Path, api_key: str | None = None) -> dict:
    """Backward-compatible ETF map builder retained for existing V18.2 tests."""
    import pandas as pd

    etf_df = pd.read_csv(etf_master_path, sep=";", encoding="utf-8-sig", dtype=str)
    output_map_path = Path(output_map_path)
    if output_map_path.exists():
        existing = pd.read_csv(output_map_path, sep=";", encoding="utf-8-sig", dtype=str)
    else:
        existing = pd.DataFrame(columns=["isin", "yahoo_ticker"])

    already_mapped = set(existing["isin"].dropna())
    to_resolve = [isin for isin in etf_df["isin"] if isin not in already_mapped]
    resolved = resolve_isins(to_resolve, api_key=api_key or os.environ.get("OPENFIGI_API_KEY"))

    new_rows, gaps = [], []
    for isin in to_resolve:
        matches = resolved.get(isin, [])
        best = pick_best_match(matches)
        if best is None:
            gaps.append({"isin": isin, "reason": "NO_OPENFIGI_MATCH_ON_KNOWN_EXCHANGE", "raw_matches": len(matches)})
            continue
        suffix = EXCHANGE_TO_YAHOO_SUFFIX[best["exchCode"]]
        new_rows.append({"isin": isin, "yahoo_ticker": f"{best['ticker']}.{suffix}"})

    updated = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True).drop_duplicates("isin")
    updated.to_csv(output_map_path, sep=";", index=False, encoding="utf-8-sig")
    if gaps:
        Path(gaps_path).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(gaps).to_csv(gaps_path, sep=";", index=False, encoding="utf-8-sig")
    return {"requested": len(to_resolve), "resolved": len(new_rows), "gaps": len(gaps)}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    summary = build_etf_ticker_map(
        root / "inputs" / "V18.2_PEA_ETF_MASTER.csv",
        root / "config" / "V18.2_ETF_TICKER_MAP.csv",
        root / "outputs" / "gaps" / "V18.2_ETF_TICKER_OPENFIGI_GAPS.csv",
    )
    print(summary)
