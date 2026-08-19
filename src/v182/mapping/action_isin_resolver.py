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

# OpenFIGI/Bloomberg exchange code -> Yahoo Finance suffix. Only venue pairs that
# we explicitly support are enabled. Country-prefix guessing is forbidden.
# Both common API code variants seen in our reference stack are accepted.
EXCHANGE_TO_YAHOO_SUFFIX = {
    "FP": "PA", "PA": "PA",          # Paris
    "BB": "BR", "BR": "BR",          # Brussels
    "NA": "AS", "AS": "AS",          # Amsterdam
    "PL": "LS", "LS": "LS",          # Lisbon
    "IM": "MI", "MI": "MI",          # Milan
    "SM": "MC",                        # Madrid
    "GY": "DE", "GR": "DE",          # Germany/Xetra family
    "SS": "ST", "ST": "ST",          # Stockholm
    "FH": "HE", "HE": "HE",          # Helsinki
    "NO": "OL", "OS": "OL",          # Oslo
    "AV": "VI", "VI": "VI",          # Vienna
    "ID": "IR", "IR": "IR",          # Dublin
    "LX": "LU",                        # Luxembourg
    "CP": "PR",                        # Prague
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
    if not _is_equity_match(match):
        return ""
    ticker = str(match.get("ticker") or "").strip().upper()
    exch = str(match.get("exchCode") or "").strip().upper()
    suffix = EXCHANGE_TO_YAHOO_SUFFIX.get(exch)
    if not ticker or not suffix:
        return ""
    if "." in ticker:
        return ""
    return f"{ticker}.{suffix}"


def resolve_isins_openfigi(
    isins: list[str], api_key: str | None = None, *, delay_seconds: float | None = None
) -> dict[str, list[dict]]:
    """Resolve ISINs with OpenFIGI v3 using rate-safe batches.

    The public endpoint has a lower per-request job limit than authenticated use,
    so unauthenticated batches are deliberately capped at five. The resolver does
    not infer identities when OpenFIGI returns no supported equity match.
    """
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


def _candidate_records(matches: list[dict]) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for match in matches:
        yahoo_ticker = candidate_yahoo_ticker(match)
        if not yahoo_ticker or yahoo_ticker in seen:
            continue
        name = str(match.get("name") or "").strip()
        if not name:
            continue
        seen.add(yahoo_ticker)
        records.append({
            "yahoo_ticker": yahoo_ticker,
            "openfigi_name": name,
            "openfigi_figi": str(match.get("figi") or "").strip(),
            "openfigi_share_class_figi": str(match.get("shareClassFIGI") or "").strip(),
            "openfigi_exchange_code": str(match.get("exchCode") or "").strip().upper(),
            "openfigi_security_type": str(match.get("securityType") or match.get("securityType2") or "").strip(),
        })
    return records


def resolve_identity_rows(
    frame: pd.DataFrame,
    *,
    openfigi_matches: dict[str, list[dict]] | None = None,
    yahoo_validator=validate_yahoo_candidate,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Produce a sourced overlay and unresolved worklist for identity-only rows.

    A ticker is promoted only after an OpenFIGI ISIN mapping and independent
    Yahoo quote/name validation. If multiple materially plausible Yahoo symbols
    survive, the ISIN remains unresolved rather than selecting one arbitrarily.
    """
    if "isin" not in frame.columns or "canonical_seed_status" not in frame.columns:
        return pd.DataFrame(), pd.DataFrame()
    missing = frame[frame["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS)].copy()
    isins = missing["isin"].astype(str).str.strip().tolist()
    matches_by_isin = openfigi_matches if openfigi_matches is not None else resolve_isins_openfigi(isins)
    overlay_rows: list[dict] = []
    gaps: list[dict] = []
    validation_cache: dict[tuple[str, str], YahooIdentity | None] = {}

    for isin in isins:
        matches = matches_by_isin.get(isin, [])
        candidates = _candidate_records(matches)
        direct_names = sorted({_norm_text(m.get("name")) for m in matches if _is_equity_match(m) and _norm_text(m.get("name"))})
        if not candidates:
            if len(direct_names) == 1:
                raw_name = next(str(m.get("name") or "").strip() for m in matches if _norm_text(m.get("name")) == direct_names[0])
                overlay_rows.append({
                    "isin": isin,
                    "name": raw_name,
                    "yahoo_ticker": "",
                    "canonical_seed_status": IDENTITY_ONLY_STATUS,
                    "identity_resolution_status": NAME_ONLY_STATUS,
                    "identity_source": "OpenFIGI_ID_ISIN",
                    "identity_validation_as_of": _now(),
                })
            gaps.append({"isin": isin, "status": "UNRESOLVED", "reason": "NO_SUPPORTED_EQUITY_YAHOO_CANDIDATE", "openfigi_matches": len(matches)})
            continue

        validated: list[tuple[dict, YahooIdentity]] = []
        for candidate in candidates[:8]:
            key = (candidate["yahoo_ticker"], candidate["openfigi_name"])
            if key not in validation_cache:
                validation_cache[key] = yahoo_validator(candidate["yahoo_ticker"], candidate["openfigi_name"])
            yahoo = validation_cache[key]
            if yahoo is not None:
                validated.append((candidate, yahoo))

        validated.sort(key=lambda item: item[1].similarity, reverse=True)
        if not validated:
            gaps.append({"isin": isin, "status": "UNRESOLVED", "reason": "YAHOO_IDENTITY_VALIDATION_FAILED", "candidate_count": len(candidates)})
            continue
        if len(validated) > 1:
            best = validated[0][1].similarity
            second = validated[1][1].similarity
            if validated[0][0]["yahoo_ticker"] != validated[1][0]["yahoo_ticker"] and best - second < 0.08:
                gaps.append({
                    "isin": isin,
                    "status": "UNRESOLVED",
                    "reason": "AMBIGUOUS_MULTIPLE_VALIDATED_TICKERS",
                    "candidate_count": len(validated),
                    "best_similarity": round(best, 4),
                    "second_similarity": round(second, 4),
                })
                continue

        candidate, yahoo = validated[0]
        overlay_rows.append({
            "isin": isin,
            "name": candidate["openfigi_name"],
            "yahoo_ticker": candidate["yahoo_ticker"],
            "canonical_seed_status": HYDRATED_STATUS,
            "identity_resolution_status": "VALIDATED",
            "identity_source": "OpenFIGI_ID_ISIN+Yahoo_identity_check",
            "identity_validation_as_of": _now(),
            **{key: candidate[key] for key in (
                "openfigi_figi", "openfigi_share_class_figi", "openfigi_exchange_code", "openfigi_security_type"
            )},
            "yahoo_validated_name": yahoo.name,
            "yahoo_quote_type": yahoo.quote_type,
            "yahoo_currency": yahoo.currency,
            "yahoo_exchange": yahoo.exchange,
            "identity_name_similarity": round(yahoo.similarity, 6),
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
    name_only = 0
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
            name_only += 1
        if resolution == "VALIDATED" and name and ticker:
            result.at[isin, "yahoo_ticker"] = ticker
            result.at[isin, "canonical_seed_status"] = HYDRATED_STATUS
            full += 1
        applied += 1
        for field in (
            "identity_resolution_status", "identity_source", "identity_validation_as_of",
            "openfigi_figi", "openfigi_share_class_figi", "openfigi_exchange_code", "openfigi_security_type",
            "yahoo_validated_name", "yahoo_quote_type", "yahoo_currency", "yahoo_exchange", "identity_name_similarity",
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
        "name_observed": name_only,
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
        "policy": "OPENFIGI_ISIN_PLUS_INDEPENDENT_YAHOO_IDENTITY_VALIDATION; NO_COUNTRY_SUFFIX_GUESSING; AMBIGUITY_STAYS_BLOCK_DATA",
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
