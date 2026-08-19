from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
import logging
import os
from pathlib import Path
import re
import time
import unicodedata

import pandas as pd

from v182.audit.canonical_universe import IDENTITY_ONLY_STATUS
from v182.io.frames import is_missing

logger = logging.getLogger(__name__)
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
HYDRATED_STATUS = "HYDRATED_ATTRIBUTED_IDENTITY"
NAME_ONLY_STATUS = "OPENFIGI_NAME_ONLY_TICKER_UNRESOLVED"
NON_EEA_REVIEW_STATUS = "IDENTITY_VALIDATED_NON_EEA_VENUE_REVIEW"

EXCHANGE_TO_YAHOO_SUFFIX = {
    "FP": "PA", "PA": "PA", "BB": "BR", "BR": "BR", "NA": "AS", "AS": "AS",
    "PL": "LS", "LS": "LS", "IM": "MI", "MI": "MI", "SM": "MC", "GY": "DE",
    "GR": "DE", "SS": "ST", "ST": "ST", "FH": "HE", "HE": "HE", "NO": "OL",
    "OS": "OL", "AV": "VI", "VI": "VI", "ID": "IR", "IR": "IR", "LX": "LU",
    "CP": "PR",
}

# Yahoo exchange codes/suffixes that correspond to EU/EEA trading venues used by
# this PEA engine. A non-EEA rank-1 match can prove identity but is not promoted
# to the market-data ticker while an EEA execution/data venue remains unresolved.
EEA_YAHOO_EXCHANGES = {
    "PAR", "AMS", "BRU", "MCE", "MIL", "GER", "FRA", "STO", "HEL", "OSL",
    "VIE", "ISE", "PRA", "LIS", "CPH", "WSE", "ATH", "ICE", "LUX",
}
EEA_YAHOO_SUFFIXES = {
    ".PA", ".AS", ".BR", ".MC", ".MI", ".DE", ".F", ".ST", ".HE", ".OL",
    ".VI", ".IR", ".PR", ".LS", ".CO", ".WA", ".AT", ".IC", ".LU",
}

UNSUPPORTED_SECURITY_TOKENS = (
    "WARRANT", "RIGHT", "FUND", "ETF", "ETN", "BOND", "NOTE", "CERTIFICATE",
    "UNIT", "INDEX", "OPTION", "FUTURE",
)
NAME_STOP_TOKENS = {
    "SA", "SE", "NV", "PLC", "AG", "SPA", "OYJ", "OY", "ASA", "AB", "AS",
    "SCA", "SAS", "LTD", "LIMITED", "AKTIENGESELLSCHAFT", "REGISTERED", "REG",
    "SHARE", "SHARES", "SHS", "CLASS", "CL", "ORD", "ORDINARY", "PUBL", "NAMEN",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_text(value) -> str:
    if is_missing(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    tokens = [token for token in text.split() if token not in NAME_STOP_TOKENS]
    return " ".join(tokens).strip()


def name_similarity(left, right) -> float:
    a = _norm_text(left)
    b = _norm_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        short = min(len(a), len(b))
        long = max(len(a), len(b))
        return max(0.82, short / long)
    return SequenceMatcher(a=a, b=b).ratio()


def _is_equity_match(match: dict) -> bool:
    market = str(match.get("marketSector") or "").strip().upper()
    if market and market != "EQUITY":
        return False
    sec = " ".join(
        str(match.get(key) or "") for key in ("securityType", "securityType2", "securityDescription")
    ).upper()
    return not any(token in sec for token in UNSUPPORTED_SECURITY_TOKENS)


def candidate_yahoo_ticker(match: dict) -> str:
    """Diagnostic only; governed resolution uses Yahoo's exact ISIN search."""
    if not _is_equity_match(match):
        return ""
    ticker = str(match.get("ticker") or "").strip().upper()
    exch = str(match.get("exchCode") or "").strip().upper()
    suffix = EXCHANGE_TO_YAHOO_SUFFIX.get(exch)
    if not ticker or not suffix or "." in ticker:
        return ""
    return f"{ticker}.{suffix}"


def resolve_isins_openfigi(
    isins: list[str], api_key: str | None = None, *, delay_seconds: float | None = None
) -> dict[str, list[dict]]:
    import requests

    key = api_key or os.environ.get("OPENFIGI_API_KEY")
    batch_size = 100 if key else 5
    delay = float(delay_seconds if delay_seconds is not None else (1.0 if key else 3.0))
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    clean = sorted({str(isin).strip().upper() for isin in isins if str(isin).strip()})
    result: dict[str, list[dict]] = {}
    for start in range(0, len(clean), batch_size):
        batch = clean[start:start + batch_size]
        payload = [{"idType": "ID_ISIN", "idValue": isin} for isin in batch]
        try:
            response = requests.post(OPENFIGI_URL, headers=headers, data=json.dumps(payload), timeout=30)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            logger.warning("OpenFIGI Action identity batch failed: %s: %s", type(exc).__name__, str(exc)[:180])
            for isin in batch:
                result[isin] = []
            time.sleep(delay)
            continue
        for isin, entry in zip(batch, body):
            matches = entry.get("data", []) if isinstance(entry, dict) else []
            result[isin] = [match for match in matches if isinstance(match, dict)]
        if start + batch_size < len(clean):
            time.sleep(delay)
    return result


@dataclass(frozen=True)
class YahooIdentity:
    ticker: str
    quote_type: str
    name: str
    currency: str
    exchange: str
    similarity: float


def validate_yahoo_candidate(ticker: str, expected_name: str) -> YahooIdentity | None:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).get_info()
    except Exception as exc:
        logger.info("Yahoo identity validation failed for %s: %s", ticker, type(exc).__name__)
        return None
    quote_type = str(info.get("quoteType") or "").upper()
    if quote_type != "EQUITY":
        return None
    yahoo_name = str(info.get("longName") or info.get("shortName") or "").strip()
    similarity = name_similarity(expected_name, yahoo_name)
    if similarity < 0.70:
        return None
    return YahooIdentity(
        ticker=ticker,
        quote_type=quote_type,
        name=yahoo_name,
        currency=str(info.get("currency") or "").strip().upper(),
        exchange=str(info.get("exchange") or info.get("fullExchangeName") or "").strip(),
        similarity=similarity,
    )


def search_yahoo_by_isin(isin: str) -> list[dict]:
    """Return Yahoo quote-search results for an exact ISIN, preserving rank."""
    try:
        import yfinance as yf
        search = yf.Search(
            str(isin).strip().upper(), max_results=8, news_count=0, lists_count=0,
            include_cb=False, raise_errors=False,
        )
        quotes = search.quotes or []
    except Exception as exc:
        logger.info("Yahoo ISIN search failed for %s: %s", isin, type(exc).__name__)
        return []
    return [quote for quote in quotes if isinstance(quote, dict)]


def _is_eea_yahoo_quote(quote: dict) -> bool:
    if str(quote.get("quoteType") or "").strip().upper() != "EQUITY":
        return False
    exchange = str(quote.get("exchange") or quote.get("exchDisp") or "").strip().upper()
    symbol = str(quote.get("symbol") or "").strip().upper()
    if exchange in EEA_YAHOO_EXCHANGES:
        return True
    return any(symbol.endswith(suffix) for suffix in EEA_YAHOO_SUFFIXES)


def _equity_matches(matches: list[dict]) -> list[dict]:
    return [m for m in matches if isinstance(m, dict) and _is_equity_match(m) and str(m.get("name") or "").strip()]


def _best_openfigi_match(matches: list[dict], yahoo_name: str) -> tuple[dict | None, float]:
    eligible = _equity_matches(matches)
    if not eligible:
        return None, 0.0
    ranked = sorted(
        ((name_similarity(match.get("name"), yahoo_name), match) for match in eligible),
        key=lambda item: item[0], reverse=True,
    )
    return ranked[0][1], float(ranked[0][0])


def _representative_openfigi_name(matches: list[dict]) -> str:
    eligible = _equity_matches(matches)
    if not eligible:
        return ""
    normalized: dict[str, str] = {}
    for match in eligible:
        raw = str(match.get("name") or "").strip()
        key = _norm_text(raw)
        if key and key not in normalized:
            normalized[key] = raw
    if len(normalized) == 1:
        return next(iter(normalized.values()))
    return str(eligible[0].get("name") or "").strip()


def _quote_name(quote: dict) -> str:
    return str(quote.get("longname") or quote.get("shortname") or "").strip()


def _select_eea_quote(quotes: list[dict], matches: list[dict]) -> tuple[dict | None, int, float]:
    for rank, quote in enumerate(quotes, start=1):
        if not _is_eea_yahoo_quote(quote):
            continue
        symbol = str(quote.get("symbol") or "").strip()
        if not symbol:
            continue
        _match, similarity = _best_openfigi_match(matches, _quote_name(quote))
        if similarity >= 0.70:
            return quote, rank, similarity
    return None, 0, 0.0


def resolve_identity_rows(
    frame: pd.DataFrame,
    *,
    openfigi_matches: dict[str, list[dict]] | None = None,
    yahoo_isin_searcher=search_yahoo_by_isin,
    yahoo_validator=validate_yahoo_candidate,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve identity-only Actions without inventing ticker or trading venue.

    OpenFIGI proves the ISIN/share identity. Yahoo is queried with that exact ISIN.
    The first name-compatible EEA Equity result is independently revalidated via
    Yahoo Ticker info. Non-EEA-only mappings stay BLOCK_DATA pending PEA venue
    review; preferred shares remain valid Equity candidates.
    """
    if "isin" not in frame.columns or "canonical_seed_status" not in frame.columns:
        return pd.DataFrame(), pd.DataFrame()
    missing = frame[frame["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS)].copy()
    isins = missing["isin"].astype(str).str.strip().tolist()
    matches_by_isin = openfigi_matches if openfigi_matches is not None else resolve_isins_openfigi(isins)
    overlay_rows: list[dict] = []
    gaps: list[dict] = []

    for isin in isins:
        matches = matches_by_isin.get(isin, [])
        equity_matches = _equity_matches(matches)
        representative_name = _representative_openfigi_name(matches)
        if not equity_matches:
            gaps.append({"isin": isin, "status": "UNRESOLVED", "reason": "NO_OPENFIGI_EQUITY_IDENTITY", "openfigi_matches": len(matches)})
            continue

        raw_quotes = yahoo_isin_searcher(isin)
        quotes = [raw_quotes] if isinstance(raw_quotes, dict) else list(raw_quotes or [])
        quote, rank, search_similarity = _select_eea_quote(quotes, matches)
        if quote is None:
            non_eea = next((
                q for q in quotes
                if str(q.get("quoteType") or "").upper() == "EQUITY"
                and str(q.get("symbol") or "").strip()
                and not _is_eea_yahoo_quote(q)
            ), None)
            overlay_rows.append({
                "isin": isin,
                "name": representative_name,
                "yahoo_ticker": "",
                "canonical_seed_status": IDENTITY_ONLY_STATUS,
                "identity_resolution_status": NON_EEA_REVIEW_STATUS if non_eea else NAME_ONLY_STATUS,
                "identity_source": "OpenFIGI_ID_ISIN+Yahoo_Search_ISIN" if non_eea else "OpenFIGI_ID_ISIN",
                "identity_validation_as_of": _now(),
                "yahoo_non_eea_symbol": str(non_eea.get("symbol") or "").strip() if non_eea else "",
                "yahoo_non_eea_exchange": str(non_eea.get("exchange") or non_eea.get("exchDisp") or "").strip() if non_eea else "",
            })
            gaps.append({
                "isin": isin, "status": "UNRESOLVED",
                "reason": "NO_NAME_COMPATIBLE_EEA_YAHOO_ISIN_RESULT" if quotes else "YAHOO_ISIN_SEARCH_EMPTY",
                "yahoo_non_eea_symbol": str(non_eea.get("symbol") or "").strip() if non_eea else "",
                "yahoo_non_eea_exchange": str(non_eea.get("exchange") or non_eea.get("exchDisp") or "").strip() if non_eea else "",
            })
            continue

        symbol = str(quote.get("symbol") or "").strip()
        search_name = _quote_name(quote)
        best_match, _ = _best_openfigi_match(matches, search_name)
        expected_name = str(best_match.get("name") if best_match else representative_name).strip()
        yahoo = yahoo_validator(symbol, expected_name)
        if yahoo is None:
            gaps.append({"isin": isin, "status": "UNRESOLVED", "reason": "YAHOO_TICKER_SECONDARY_VALIDATION_FAILED", "yahoo_symbol": symbol, "yahoo_search_rank": rank})
            continue

        final_match, final_similarity = _best_openfigi_match(matches, yahoo.name)
        if final_match is None or final_similarity < 0.70:
            gaps.append({"isin": isin, "status": "UNRESOLVED", "reason": "FINAL_OPENFIGI_YAHOO_NAME_MISMATCH", "yahoo_symbol": symbol, "final_name_similarity": round(final_similarity, 4)})
            continue

        overlay_rows.append({
            "isin": isin,
            "name": str(final_match.get("name") or representative_name).strip(),
            "yahoo_ticker": symbol,
            "canonical_seed_status": HYDRATED_STATUS,
            "identity_resolution_status": "VALIDATED",
            "identity_source": "OpenFIGI_ID_ISIN+Yahoo_Search_ISIN_EEA_rank+Yahoo_Ticker_validation",
            "identity_validation_as_of": _now(),
            "openfigi_figi": str(final_match.get("figi") or "").strip(),
            "openfigi_share_class_figi": str(final_match.get("shareClassFIGI") or "").strip(),
            "openfigi_exchange_code": str(final_match.get("exchCode") or "").strip().upper(),
            "openfigi_security_type": str(final_match.get("securityType") or final_match.get("securityType2") or "").strip(),
            "yahoo_search_rank": rank,
            "yahoo_search_name": search_name,
            "yahoo_search_exchange": str(quote.get("exchange") or quote.get("exchDisp") or "").strip(),
            "yahoo_validated_name": yahoo.name,
            "yahoo_quote_type": yahoo.quote_type,
            "yahoo_currency": yahoo.currency,
            "yahoo_exchange": yahoo.exchange,
            "identity_name_similarity": round(final_similarity, 6),
            "search_name_similarity": round(search_similarity, 6),
        })

    return pd.DataFrame(overlay_rows), pd.DataFrame(gaps)


def apply_identity_overlay(frame: pd.DataFrame, overlay_path: str | Path) -> tuple[pd.DataFrame, dict]:
    path = Path(overlay_path)
    if not path.exists():
        return frame.copy(), {"status": "NO_OVERLAY", "applied": 0}
    overlay = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, keep_default_na=True)
    if overlay.empty:
        return frame.copy(), {"status": "EMPTY_OVERLAY", "applied": 0}
    required = {"isin", "identity_resolution_status", "identity_source", "identity_validation_as_of"}
    if not required.issubset(overlay.columns):
        raise RuntimeError(f"ACTION_IDENTITY_OVERLAY_MISSING_COLUMNS:{sorted(required-set(overlay.columns))}")
    if overlay["isin"].duplicated().any():
        raise RuntimeError("ACTION_IDENTITY_OVERLAY_DUPLICATE_ISIN")

    result = frame.copy().set_index("isin", drop=False)
    applied = 0
    full = 0
    name_observed = 0
    for _, row in overlay.iterrows():
        isin = str(row.get("isin") or "").strip()
        if not isin or isin not in result.index:
            continue
        current_status = str(result.at[isin, "canonical_seed_status"] if "canonical_seed_status" in result.columns else "")
        if current_status != IDENTITY_ONLY_STATUS:
            continue
        resolution = str(row.get("identity_resolution_status") or "").strip().upper()
        source = str(row.get("identity_source") or "").strip()
        as_of = str(row.get("identity_validation_as_of") or "").strip()
        if not source or not as_of:
            continue
        name = str(row.get("name") or "").strip()
        ticker = str(row.get("yahoo_ticker") or "").strip()
        if name and is_missing(result.at[isin, "name"] if "name" in result.columns else None):
            result.at[isin, "name"] = name
            name_observed += 1
        if resolution == "VALIDATED" and name and ticker:
            result.at[isin, "yahoo_ticker"] = ticker
            result.at[isin, "canonical_seed_status"] = HYDRATED_STATUS
            full += 1
        applied += 1
        for field in (
            "identity_resolution_status", "identity_source", "identity_validation_as_of",
            "openfigi_figi", "openfigi_share_class_figi", "openfigi_exchange_code", "openfigi_security_type",
            "yahoo_search_rank", "yahoo_search_name", "yahoo_search_exchange", "yahoo_validated_name",
            "yahoo_quote_type", "yahoo_currency", "yahoo_exchange", "identity_name_similarity",
            "search_name_similarity", "yahoo_non_eea_symbol", "yahoo_non_eea_exchange",
        ):
            if field in overlay.columns and not is_missing(row.get(field)):
                if field not in result.columns:
                    result[field] = pd.NA
                result.at[isin, field] = row.get(field)
    return result.reset_index(drop=True), {
        "status": "APPLIED", "overlay_rows": int(len(overlay)), "applied": applied,
        "fully_hydrated": full, "name_observed": name_observed,
    }


def run(root: Path) -> dict:
    from v182.audit.canonical_universe import filter_actions
    from v182.io.frames import load_master

    legacy = load_master(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    canonical = filter_actions(legacy, root / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts").included
    overlay, gaps = resolve_identity_rows(canonical)
    outdir = root / "outputs" / "gaps"
    outdir.mkdir(parents=True, exist_ok=True)
    overlay_path = outdir / "V21_9_ACTION_IDENTITY_OVERLAY_CANDIDATE.csv"
    gaps_path = outdir / "V21_9_ACTION_IDENTITY_UNRESOLVED.csv"
    overlay.to_csv(overlay_path, sep=";", encoding="utf-8-sig", index=False)
    gaps.to_csv(gaps_path, sep=";", encoding="utf-8-sig", index=False)
    fully = int(overlay.get("identity_resolution_status", pd.Series(dtype=str)).astype(str).str.upper().eq("VALIDATED").sum()) if not overlay.empty else 0
    name_only = int(len(overlay) - fully)
    payload = {
        "generated_at_utc": _now(),
        "identity_only_input": int(canonical["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS).sum()),
        "fully_validated_eea_ticker": fully,
        "resolved_identity_but_not_scoring": name_only,
        "unresolved": int(len(gaps)),
        "policy": "OPENFIGI_ISIN_PLUS_YAHOO_EXACT_ISIN_FIRST_NAME_COMPATIBLE_EEA_EQUITY_PLUS_YAHOO_TICKER_VALIDATION; NON_EEA_ONLY_STAYS_BLOCK_DATA",
        "candidate_overlay": str(overlay_path.relative_to(root)),
        "unresolved_file": str(gaps_path.relative_to(root)),
    }
    (root / "outputs" / "audit").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / "audit" / "V21_9_ACTION_IDENTITY_RESOLUTION.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run(Path(__file__).resolve().parents[3])
