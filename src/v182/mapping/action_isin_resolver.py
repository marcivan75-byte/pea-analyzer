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

# Kept for deterministic diagnostics and legacy tests only. The governed resolver
# no longer constructs the selected Yahoo ticker from this table: Yahoo is queried
# directly with the ISIN so venue/symbol selection is made by Yahoo itself.
EXCHANGE_TO_YAHOO_SUFFIX = {
    "FP": "PA", "PA": "PA",
    "BB": "BR", "BR": "BR",
    "NA": "AS", "AS": "AS",
    "PL": "LS", "LS": "LS",
    "IM": "MI", "MI": "MI",
    "SM": "MC",
    "GY": "DE", "GR": "DE",
    "SS": "ST", "ST": "ST",
    "FH": "HE", "HE": "HE",
    "NO": "OL", "OS": "OL",
    "AV": "VI", "VI": "VI",
    "ID": "IR", "IR": "IR",
    "LX": "LU",
    "CP": "PR",
}

UNSUPPORTED_SECURITY_TOKENS = (
    "WARRANT", "PREFERRED", "PREFERENCE", "RIGHT", "FUND", "ETF", "ETN",
    "BOND", "NOTE", "CERTIFICATE", "UNIT", "INDEX", "OPTION", "FUTURE",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_text(value) -> str:
    if is_missing(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = re.sub(r"\b(SA|S A|SE|NV|N V|PLC|AG|SPA|S P A|OYJ|ASA|AB|A S|SCA|SAS|LTD|LIMITED)\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
    """Diagnostic only: construct the conventional Yahoo symbol for a known venue."""
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
    """Resolve ISINs with OpenFIGI v3, respecting authenticated/public limits."""
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
    if similarity < 0.75:
        return None
    return YahooIdentity(
        ticker=ticker,
        quote_type=quote_type,
        name=yahoo_name,
        currency=str(info.get("currency") or "").strip().upper(),
        exchange=str(info.get("exchange") or info.get("fullExchangeName") or "").strip(),
        similarity=similarity,
    )


def search_yahoo_by_isin(isin: str) -> dict | None:
    """Return Yahoo's rank-1 quote for an exact ISIN search.

    yfinance itself uses an ISIN Search query for ISIN lookup. We deliberately
    accept only the first ranked quote and never choose a lower-ranked venue to
    avoid turning an alternate listing into the canonical market-data ticker.
    """
    try:
        import yfinance as yf
        search = yf.Search(
            str(isin).strip().upper(),
            max_results=8,
            news_count=0,
            lists_count=0,
            include_cb=False,
            raise_errors=False,
        )
        quotes = search.quotes or []
    except Exception as exc:
        logger.info("Yahoo ISIN search failed for %s: %s", isin, type(exc).__name__)
        return None
    if not quotes:
        return None
    first = quotes[0]
    if not isinstance(first, dict):
        return None
    if str(first.get("quoteType") or "").strip().upper() != "EQUITY":
        return None
    if not str(first.get("symbol") or "").strip():
        return None
    return first


def _equity_matches(matches: list[dict]) -> list[dict]:
    return [m for m in matches if isinstance(m, dict) and _is_equity_match(m) and str(m.get("name") or "").strip()]


def _best_openfigi_match(matches: list[dict], yahoo_name: str) -> tuple[dict | None, float]:
    eligible = _equity_matches(matches)
    if not eligible:
        return None, 0.0
    ranked = sorted(
        ((name_similarity(match.get("name"), yahoo_name), match) for match in eligible),
        key=lambda item: item[0],
        reverse=True,
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


def resolve_identity_rows(
    frame: pd.DataFrame,
    *,
    openfigi_matches: dict[str, list[dict]] | None = None,
    yahoo_isin_searcher=search_yahoo_by_isin,
    yahoo_validator=validate_yahoo_candidate,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve canonical identity-only Actions using two independent mappings.

    Promotion requires:
      1) OpenFIGI returns at least one supported Equity identity for the ISIN;
      2) Yahoo Search queried with that same ISIN returns a rank-1 Equity symbol;
      3) Yahoo Ticker info validates the symbol as Equity and its issuer name
         materially agrees with the OpenFIGI identity.

    No country suffix, venue priority or alternate-listing guess is used.
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
            gaps.append({
                "isin": isin,
                "status": "UNRESOLVED",
                "reason": "NO_OPENFIGI_EQUITY_IDENTITY",
                "openfigi_matches": len(matches),
            })
            continue

        search_quote = yahoo_isin_searcher(isin)
        if not search_quote:
            if representative_name:
                overlay_rows.append({
                    "isin": isin,
                    "name": representative_name,
                    "yahoo_ticker": "",
                    "canonical_seed_status": IDENTITY_ONLY_STATUS,
                    "identity_resolution_status": NAME_ONLY_STATUS,
                    "identity_source": "OpenFIGI_ID_ISIN",
                    "identity_validation_as_of": _now(),
                })
            gaps.append({
                "isin": isin,
                "status": "UNRESOLVED",
                "reason": "YAHOO_ISIN_SEARCH_NO_EQUITY_RANK1",
                "openfigi_equity_matches": len(equity_matches),
            })
            continue

        symbol = str(search_quote.get("symbol") or "").strip()
        search_name = str(search_quote.get("longname") or search_quote.get("shortname") or "").strip()
        best_match, search_similarity = _best_openfigi_match(matches, search_name)
        if best_match is None or search_similarity < 0.70:
            gaps.append({
                "isin": isin,
                "status": "UNRESOLVED",
                "reason": "YAHOO_ISIN_SEARCH_NAME_MISMATCH",
                "yahoo_symbol": symbol,
                "search_name_similarity": round(search_similarity, 4),
            })
            continue

        expected_name = str(best_match.get("name") or representative_name).strip()
        yahoo = yahoo_validator(symbol, expected_name)
        if yahoo is None:
            gaps.append({
                "isin": isin,
                "status": "UNRESOLVED",
                "reason": "YAHOO_TICKER_SECONDARY_VALIDATION_FAILED",
                "yahoo_symbol": symbol,
            })
            continue

        final_match, final_similarity = _best_openfigi_match(matches, yahoo.name)
        if final_match is None or final_similarity < 0.75:
            gaps.append({
                "isin": isin,
                "status": "UNRESOLVED",
                "reason": "FINAL_OPENFIGI_YAHOO_NAME_MISMATCH",
                "yahoo_symbol": symbol,
                "final_name_similarity": round(final_similarity, 4),
            })
            continue

        overlay_rows.append({
            "isin": isin,
            "name": str(final_match.get("name") or representative_name).strip(),
            "yahoo_ticker": symbol,
            "canonical_seed_status": HYDRATED_STATUS,
            "identity_resolution_status": "VALIDATED",
            "identity_source": "OpenFIGI_ID_ISIN+Yahoo_Search_ISIN_rank1+Yahoo_Ticker_validation",
            "identity_validation_as_of": _now(),
            "openfigi_figi": str(final_match.get("figi") or "").strip(),
            "openfigi_share_class_figi": str(final_match.get("shareClassFIGI") or "").strip(),
            "openfigi_exchange_code": str(final_match.get("exchCode") or "").strip().upper(),
            "openfigi_security_type": str(final_match.get("securityType") or final_match.get("securityType2") or "").strip(),
            "yahoo_search_name": search_name,
            "yahoo_search_exchange": str(search_quote.get("exchange") or search_quote.get("exchDisp") or "").strip(),
            "yahoo_validated_name": yahoo.name,
            "yahoo_quote_type": yahoo.quote_type,
            "yahoo_currency": yahoo.currency,
            "yahoo_exchange": yahoo.exchange,
            "identity_name_similarity": round(final_similarity, 6),
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
            "yahoo_search_name", "yahoo_search_exchange", "yahoo_validated_name", "yahoo_quote_type",
            "yahoo_currency", "yahoo_exchange", "identity_name_similarity",
        ):
            if field in overlay.columns and not is_missing(row.get(field)):
                if field not in result.columns:
                    result[field] = pd.NA
                result.at[isin, field] = row.get(field)
    return result.reset_index(drop=True), {
        "status": "APPLIED",
        "overlay_rows": int(len(overlay)),
        "applied": applied,
        "fully_hydrated": full,
        "name_observed": name_observed,
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
        "fully_validated": fully,
        "name_only": name_only,
        "unresolved": int(len(gaps)),
        "policy": "OPENFIGI_ISIN_PLUS_YAHOO_EXACT_ISIN_RANK1_PLUS_YAHOO_TICKER_VALIDATION; NO_COUNTRY_SUFFIX_GUESSING; NO_ALTERNATE_VENUE_SELECTION",
        "candidate_overlay": str(overlay_path.relative_to(root)),
        "unresolved_file": str(gaps_path.relative_to(root)),
    }
    (root / "outputs" / "audit").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / "audit" / "V21_9_ACTION_IDENTITY_RESOLUTION.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run(Path(__file__).resolve().parents[3])
