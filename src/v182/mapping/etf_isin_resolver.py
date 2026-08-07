from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
import os
import time

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

YAHOO_SUFFIX_TO_MIC = {
    "PA": "XPAR", "AS": "XAMS", "BR": "XBRU", "LS": "XLIS", "MI": "XMIL",
    "DE": "XETR", "SW": "XSWX", "VI": "XWBO", "IR": "XDUB", "HE": "XHEL",
    "CO": "XCSE", "ST": "XSTO", "OL": "XOSL", "L": "XLON",
}
MIC_TO_YAHOO_SUFFIX = {mic: suffix for suffix, mic in YAHOO_SUFFIX_TO_MIC.items()}

ISIN_PREFIX_TO_MIC = {
    "FR": "XPAR", "NL": "XAMS", "BE": "XBRU", "PT": "XLIS", "IT": "XMIL",
    "DE": "XETR", "CH": "XSWX", "AT": "XWBO", "IE": "XDUB", "FI": "XHEL",
    "DK": "XCSE", "SE": "XSTO", "NO": "XOSL", "GB": "XLON",
}

MASTER_MAP_COLUMNS = [
    "universe", "isin", "original_yahoo_ticker", "openfigi_ticker",
    "openfigi_exch_code", "openfigi_mic", "yahoo_candidate", "figi",
    "composite_figi", "share_class_figi", "status", "updated_at",
]


def _batches(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def expected_mic(yahoo_ticker: str | None, isin: str | None = None) -> str:
    text = str(yahoo_ticker or "").strip().upper()
    if "." in text:
        suffix = text.rsplit(".", 1)[-1]
        mic = YAHOO_SUFFIX_TO_MIC.get(suffix)
        if mic:
            return mic
    prefix = str(isin or "").strip().upper()[:2]
    return ISIN_PREFIX_TO_MIC.get(prefix, "")


def _base_symbol(yahoo_ticker: str | None) -> str:
    text = str(yahoo_ticker or "").strip()
    if not text:
        return ""
    if "." in text and text.rsplit(".", 1)[-1].upper() in YAHOO_SUFFIX_TO_MIC:
        return text.rsplit(".", 1)[0]
    return text


def resolve_isins(
    isins: list[str],
    api_key: str | None = None,
    mic_by_isin: dict[str, str] | None = None,
    batch_size: int | None = None,
    delay_seconds: float | None = None,
    max_retries: int = 3,
) -> dict[str, list[dict] | None]:
    """Resolve ISINs through OpenFIGI v3.

    ``None`` means a transient/API failure and is deliberately not cached as a
    negative. ``[]`` means a normal no-identifier response. When a MIC is known
    it is sent in the job so Bloomberg exchange codes are never guessed into
    Yahoo suffixes.
    """
    import requests

    key = api_key or os.environ.get("OPENFIGI_API_KEY")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key

    effective_batch = int(batch_size or (100 if key else 5))
    effective_delay = float(delay_seconds if delay_seconds is not None else (0.30 if key else 2.50))
    clean_isins = sorted({str(i).strip() for i in isins if str(i or "").strip()})
    mic_by_isin = mic_by_isin or {}
    results: dict[str, list[dict] | None] = {}

    for batch in _batches(clean_isins, effective_batch):
        payload = []
        for isin in batch:
            job = {"idType": "ID_ISIN", "idValue": isin, "marketSecDes": "Equity"}
            mic = str(mic_by_isin.get(isin) or "").strip().upper()
            if mic:
                job["micCode"] = mic
            payload.append(job)

        body = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(OPENFIGI_URL, headers=headers, json=payload, timeout=30)
                if resp.status_code == 429:
                    reset = resp.headers.get("ratelimit-reset") or resp.headers.get("Retry-After")
                    try:
                        wait = max(0.5, float(reset)) if reset is not None else max(1.0, effective_delay * (attempt + 2))
                    except (TypeError, ValueError):
                        wait = max(1.0, effective_delay * (attempt + 2))
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

        if not isinstance(body, list) or len(body) != len(batch):
            for isin in batch:
                results[isin] = None
        else:
            for isin, entry in zip(batch, body):
                if not isinstance(entry, dict):
                    results[isin] = None
                elif entry.get("warning"):
                    results[isin] = []
                elif entry.get("error"):
                    results[isin] = None
                else:
                    results[isin] = entry.get("data", []) or []
        time.sleep(effective_delay)

    return results


def pick_best_match(matches: list[dict] | None) -> dict | None:
    """Choose a plain equity/ETF listing and exclude derivative-like results."""
    if not matches:
        return None
    blocked = {"OPTION", "WARRANT", "FUTURE", "RIGHT", "PREFERRED"}
    candidates = []
    for match in matches:
        if not isinstance(match, dict) or not match.get("ticker"):
            continue
        sector = str(match.get("marketSector") or "").upper()
        type2 = str(match.get("securityType2") or "").upper()
        if sector and sector != "EQUITY":
            continue
        if any(token in type2 for token in blocked):
            continue
        candidates.append(match)
    if not candidates:
        return None
    return sorted(candidates, key=lambda m: (str(m.get("ticker") or ""), str(m.get("figi") or "")))[0]


def _row_from_match(
    universe: str,
    isin: str,
    original_ticker: str,
    requested_mic: str,
    match: dict | None,
    no_identifier: bool = False,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    if not match:
        return {
            "universe": universe, "isin": isin, "original_yahoo_ticker": original_ticker,
            "openfigi_ticker": "", "openfigi_exch_code": "", "openfigi_mic": requested_mic,
            "yahoo_candidate": "", "figi": "", "composite_figi": "", "share_class_figi": "",
            "status": "NO_IDENTIFIER" if no_identifier else "NO_SUPPORTED_MATCH", "updated_at": now,
        }
    ticker = str(match.get("ticker") or "").strip()
    suffix = MIC_TO_YAHOO_SUFFIX.get(requested_mic, "")
    candidate = f"{ticker}.{suffix}" if ticker and suffix else ""
    return {
        "universe": universe, "isin": isin, "original_yahoo_ticker": original_ticker,
        "openfigi_ticker": ticker, "openfigi_exch_code": str(match.get("exchCode") or ""),
        "openfigi_mic": requested_mic, "yahoo_candidate": candidate,
        "figi": str(match.get("figi") or ""), "composite_figi": str(match.get("compositeFIGI") or ""),
        "share_class_figi": str(match.get("shareClassFIGI") or ""), "status": "RESOLVED",
        "updated_at": now,
    }


def _cache_fresh(row, days: int) -> bool:
    status = str(row.get("status") or "").upper()
    if status == "RESOLVED":
        return True
    if status not in {"NO_IDENTIFIER", "NO_SUPPORTED_MATCH"}:
        return False
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp >= datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    except Exception:
        return False


def _cache_identity_matches(old, current_row: dict) -> bool:
    """A cached mapping is reusable only for the same ticker and expected MIC."""
    return (
        str(old.get("original_yahoo_ticker") or "").strip().upper()
        == str(current_row.get("original_yahoo_ticker") or "").strip().upper()
        and str(old.get("openfigi_mic") or "").strip().upper()
        == str(current_row.get("expected_mic") or "").strip().upper()
    )


def build_openfigi_master_map(
    actions_df,
    etf_df,
    output_path: str | Path,
    api_key: str | None = None,
    negative_cache_days: int = 30,
) -> dict:
    """Persist a safe OpenFIGI cache for Actions + ETF.

    Resolved entries are reusable while the local ticker/MIC identity context
    remains unchanged. Definitive negatives expire after a TTL. Transient API
    failures are never written as negative matches. A stale identity is removed
    immediately even when its refresh request fails, so it cannot be reused as
    an unsafe fallback.
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
            isin = str(row.get("isin") or "").strip().upper()
            if not isin:
                continue
            original = str(row.get("yahoo_ticker") or "").strip()
            source_rows.append({
                "universe": universe,
                "isin": isin,
                "original_yahoo_ticker": original,
                "expected_mic": expected_mic(original, isin),
            })
    source = pd.DataFrame(source_rows).drop_duplicates(["universe", "isin"])

    current = {}
    if not existing.empty:
        current = {(r["universe"], r["isin"]): r for _, r in existing.iterrows()}
    to_refresh = []
    invalid_identity_keys = set()
    for _, row in source.iterrows():
        current_row = row.to_dict()
        key = (current_row["universe"], current_row["isin"])
        old = current.get(key)
        identity_ok = old is not None and _cache_identity_matches(old, current_row)
        if old is not None and not identity_ok:
            invalid_identity_keys.add(key)
        if old is None or not identity_ok or not _cache_fresh(old, negative_cache_days):
            to_refresh.append(current_row)

    unique_isins = sorted({r["isin"] for r in to_refresh})
    mic_by_isin = {}
    for row in to_refresh:
        if row["expected_mic"] and row["isin"] not in mic_by_isin:
            mic_by_isin[row["isin"]] = row["expected_mic"]
    resolved = resolve_isins(unique_isins, api_key=api_key, mic_by_isin=mic_by_isin) if unique_isins else {}

    new_rows = []
    transient_failures = 0
    for row in to_refresh:
        raw = resolved.get(row["isin"])
        if raw is None:
            transient_failures += 1
            continue
        best = pick_best_match(raw)
        new_rows.append(_row_from_match(
            row["universe"], row["isin"], row["original_yahoo_ticker"], row["expected_mic"],
            best, no_identifier=(raw == []),
        ))

    refreshed_keys = {(r["universe"], r["isin"]) for r in new_rows}
    remove_keys = refreshed_keys | invalid_identity_keys
    if not existing.empty and remove_keys:
        existing = existing[[
            (u, i) not in remove_keys for u, i in zip(existing["universe"], existing["isin"])
        ]]
    updated = pd.concat([existing, pd.DataFrame(new_rows, columns=MASTER_MAP_COLUMNS)], ignore_index=True)
    if not updated.empty:
        updated = updated.drop_duplicates(["universe", "isin"], keep="last").sort_values(["universe", "isin"])
    output.parent.mkdir(parents=True, exist_ok=True)
    updated.to_csv(output, sep=";", index=False, encoding="utf-8-sig")

    resolved_count = int((updated["status"] == "RESOLVED").sum()) if not updated.empty else 0
    return {
        "records": len(updated), "resolved": resolved_count,
        "coverage_pct": round(resolved_count / len(source) * 100, 2) if len(source) else 100.0,
        "api_isins_requested": len(unique_isins), "new_records": len(new_rows),
        "invalidated_identity_records": len(invalid_identity_keys),
        "transient_failures": transient_failures,
        "authenticated": bool(api_key or os.environ.get("OPENFIGI_API_KEY")),
    }


def fallback_specs(master_df, failed_tickers: list[str], openfigi_map_path: str | Path, universe: str) -> dict[str, dict]:
    """Build safe Yahoo-repair and Marketstack fallback specs."""
    import pandas as pd

    failed = set(failed_tickers)
    if not failed:
        return {}
    by_isin = {}
    path = Path(openfigi_map_path)
    if path.exists():
        mapping = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
        mapping = mapping[(mapping["universe"] == universe) & (mapping["status"] == "RESOLVED")]
        if not mapping.empty:
            by_isin = mapping.drop_duplicates("isin").set_index("isin").to_dict("index")

    specs: dict[str, dict] = {}
    for _, row in master_df.iterrows():
        original = str(row.get("yahoo_ticker") or "").strip()
        if original not in failed:
            continue
        isin = str(row.get("isin") or "").strip().upper()
        mapped = by_isin.get(isin, {})
        direct_mic = expected_mic(original, isin)
        mapped_identity_ok = (
            str(mapped.get("original_yahoo_ticker") or "").strip().upper() == original.upper()
            and str(mapped.get("openfigi_mic") or "").strip().upper() == direct_mic.upper()
        )
        if not mapped_identity_ok:
            mapped = {}
        specs[original] = {
            "isin": isin,
            "yahoo_candidate": str(mapped.get("yahoo_candidate") or ""),
            "marketstack_symbol": str(mapped.get("openfigi_ticker") or _base_symbol(original)),
            "marketstack_mic": str(mapped.get("openfigi_mic") or direct_mic),
            "figi": str(mapped.get("figi") or ""),
        }
    return specs


def build_etf_ticker_map(etf_master_path: str | Path, output_map_path: str | Path,
                          gaps_path: str | Path, api_key: str | None = None) -> dict:
    """Backward-compatible ETF map builder using MIC-constrained OpenFIGI."""
    import pandas as pd

    etf_df = pd.read_csv(etf_master_path, sep=";", encoding="utf-8-sig", dtype=str)
    output_map_path = Path(output_map_path)
    if output_map_path.exists():
        existing = pd.read_csv(output_map_path, sep=";", encoding="utf-8-sig", dtype=str)
    else:
        existing = pd.DataFrame(columns=["isin", "yahoo_ticker"])

    already_mapped = set(existing["isin"].dropna())
    to_resolve = [str(isin) for isin in etf_df["isin"] if isin not in already_mapped]
    mic_by_isin = {isin: expected_mic("", isin) for isin in to_resolve if expected_mic("", isin)}
    resolved = resolve_isins(to_resolve, api_key=api_key or os.environ.get("OPENFIGI_API_KEY"), mic_by_isin=mic_by_isin)

    new_rows, gaps = [], []
    for isin in to_resolve:
        raw = resolved.get(isin)
        if raw is None:
            gaps.append({"isin": isin, "reason": "OPENFIGI_TRANSIENT_FAILURE", "raw_matches": 0})
            continue
        best = pick_best_match(raw)
        mic = mic_by_isin.get(isin, "")
        suffix = MIC_TO_YAHOO_SUFFIX.get(mic, "")
        if best is None or not suffix:
            gaps.append({"isin": isin, "reason": "NO_SAFE_OPENFIGI_MATCH", "raw_matches": len(raw)})
            continue
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
