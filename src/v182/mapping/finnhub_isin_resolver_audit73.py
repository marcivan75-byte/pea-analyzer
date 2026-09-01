"""Strict Audit73 certification of ISIN -> Finnhub recommendation symbols.

The resolver is intentionally conservative. A symbol is usable only when a
Finnhub security record carries the exact requested ISIN and represents an
equity. Name similarity, Yahoo suffixes, or a symbol-lookup hit alone are never
sufficient evidence. Ambiguous or incomplete mappings fail closed and the
corresponding Finnhub criterion must be reported as unavailable rather than
imputed.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import re
from typing import Iterable

_VERSION = "AUDIT73_FINNHUB_ISIN_MAP_V1"
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_NON_EQUITY = ("ETF", "ETN", "FUND", "BOND", "NOTE", "WARRANT", "RIGHT", "INDEX", "FUTURE", "OPTION")
_SECRET_RE = re.compile(r"([?&](?:token|api[_-]?key|apikey|key)=)[^&\s]+", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_isin(value: object) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    return text if _ISIN_RE.fullmatch(text) else ""


def sanitize_detail(value: object, limit: int = 180) -> str:
    return _SECRET_RE.sub(r"\1<REDACTED>", str(value or ""))[:limit]


def _is_equity(row: dict) -> bool:
    kind = str(row.get("type") or row.get("securityType") or "").strip().upper()
    if any(token in kind for token in _NON_EQUITY):
        return False
    return bool(kind) and ("STOCK" in kind or "EQUITY" in kind or "SHARE" in kind or kind in {"COMMON", "PREFERRED"})


def _symbol(row: dict) -> str:
    return str(row.get("symbol") or "").strip().upper()


@dataclass(frozen=True)
class FinnhubMappingDecision:
    isin: str
    finnhub_symbol: str
    status: str
    reason: str
    evidence_count: int
    evidence_source: str
    observed_at_utc: str
    version: str = _VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def certify_from_stock_symbol_rows(isin: str, rows: Iterable[dict], *, observed_at_utc: str | None = None) -> FinnhubMappingDecision:
    """Certify one mapping from Finnhub stock-symbol records.

    Exact ISIN evidence is mandatory. Multiple rows are acceptable only when they
    resolve to the same unique Finnhub ``symbol``; multiple different symbols are
    deliberately ambiguous and therefore blocked.
    """
    clean = normalize_isin(isin)
    stamp = str(observed_at_utc or _now())
    if not clean:
        return FinnhubMappingDecision(str(isin or ""), "", "BLOCKED", "INVALID_ISIN", 0, "Finnhub_stock_symbol_exact_ISIN", stamp)

    matches = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        if normalize_isin(raw.get("isin")) != clean:
            continue
        if not _is_equity(raw):
            continue
        symbol = _symbol(raw)
        if not symbol:
            continue
        matches.append(raw)

    symbols = sorted({_symbol(row) for row in matches if _symbol(row)})
    if not matches:
        return FinnhubMappingDecision(clean, "", "UNAVAILABLE", "NO_EXACT_FINNHUB_ISIN_EQUITY_EVIDENCE", 0, "Finnhub_stock_symbol_exact_ISIN", stamp)
    if len(symbols) != 1:
        return FinnhubMappingDecision(clean, "", "BLOCKED", "AMBIGUOUS_FINNHUB_SYMBOLS_FOR_EXACT_ISIN", len(matches), "Finnhub_stock_symbol_exact_ISIN", stamp)
    return FinnhubMappingDecision(clean, symbols[0], "CERTIFIED", "EXACT_ISIN_UNIQUE_EQUITY_SYMBOL", len(matches), "Finnhub_stock_symbol_exact_ISIN", stamp)


def certify_universe(universe_rows: Iterable[dict], stock_symbol_rows: Iterable[dict], *, observed_at_utc: str | None = None) -> list[dict]:
    """Return one auditable decision per ACTION ISIN; ETFs are not mapped here."""
    evidence = list(stock_symbol_rows or [])
    output: list[dict] = []
    for row in universe_rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("asset_class") or "").strip().upper() != "ACTION":
            continue
        decision = certify_from_stock_symbol_rows(row.get("isin"), evidence, observed_at_utc=observed_at_utc).as_dict()
        decision["name"] = str(row.get("name") or "").strip()
        decision["asset_class"] = "ACTION"
        decision["criterion_policy"] = "USE_FINNHUB_ONLY_IF_CERTIFIED_ELSE_COMMENT_UNAVAILABLE"
        output.append(decision)
    return output


def assert_no_unsafe_mapping(decisions: Iterable[dict]) -> None:
    """Fail closed if a non-certified row nevertheless carries a Finnhub symbol."""
    for row in decisions or []:
        status = str(row.get("status") or "")
        symbol = str(row.get("finnhub_symbol") or "").strip()
        if status != "CERTIFIED" and symbol:
            raise ValueError("BLOCK_UNSAFE_FINNHUB_MAPPING_NONCERTIFIED_SYMBOL")
