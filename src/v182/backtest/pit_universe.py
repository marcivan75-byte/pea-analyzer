"""Point-in-time universe controls for survivorship-bias-safe backtests.

This module deliberately separates security identity, venue/listing lifecycle,
historical PEA membership and true economic terminal events. A delisting from one
market is not automatically a terminal loss: the same security may transfer to
another market and remain investable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence


class ExitReason(str, Enum):
    ACQUIRED_CASH = "acquired_cash"
    ACQUIRED_STOCK = "acquired_stock"
    MERGED = "merged"
    BANKRUPTCY = "bankruptcy"
    LIQUIDATED = "liquidated"
    SECURITY_CANCELLED = "security_cancelled"
    UNKNOWN = "unknown"


class ListingEventType(str, Enum):
    ADMITTED = "admitted"
    DELISTED = "delisted"
    MARKET_TRANSFER = "market_transfer"
    SUSPENDED = "suspended"
    RESUMED = "resumed"


@dataclass(frozen=True)
class SecurityIdentity:
    """Stable security identity; ticker/ISIN aliases may change over time."""

    security_id: str
    ticker: str
    isin: Optional[str] = None
    mic: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


@dataclass(frozen=True)
class ListingEvent:
    """Venue/listing lifecycle event; not necessarily an economic terminal event."""

    security_id: str
    effective_date: date
    event_type: ListingEventType
    mic_from: Optional[str] = None
    mic_to: Optional[str] = None
    reason_text: Optional[str] = None
    source: str = "unknown"
    confidence: float = 1.0


@dataclass(frozen=True)
class UniverseMembership:
    """One effective-dated membership interval for an investable universe."""

    security_id: str
    effective_from: date
    effective_to: Optional[date] = None
    eligible: bool = True
    universe_code: str = "PEA_ACTIONS"
    source: str = "unknown"
    source_asof_date: Optional[date] = None
    confidence: float = 1.0

    def active_on(self, as_of: date) -> bool:
        return (
            self.eligible
            and self.effective_from <= as_of
            and (self.effective_to is None or as_of <= self.effective_to)
        )


@dataclass(frozen=True)
class TerminalEvent:
    """True economic/security terminal event preventing silent disappearance."""

    security_id: str
    effective_date: date
    reason: ExitReason
    cash_per_share: Optional[float] = None
    successor_security_id: Optional[str] = None
    exchange_ratio: Optional[float] = None
    source: str = "unknown"
    confidence: float = 1.0


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def universe_as_of(
    as_of: date | datetime | str,
    memberships: Iterable[UniverseMembership],
    *,
    universe_code: str = "PEA_ACTIONS",
    min_confidence: float = 1.0,
) -> set[str]:
    """Return the confirmed investable universe at ``as_of``.

    Default is fail-closed: uncertain reconstructed membership is not promoted
    into the strict PIT universe unless callers explicitly lower min_confidence.
    """

    d = _as_date(as_of)
    return {
        row.security_id
        for row in memberships
        if row.universe_code == universe_code
        and row.confidence >= min_confidence
        and row.active_on(d)
    }


def validate_memberships(
    memberships: Sequence[UniverseMembership],
) -> list[str]:
    """Return structural errors that can invalidate a PIT backtest."""

    errors: list[str] = []
    by_key: dict[tuple[str, str], list[UniverseMembership]] = {}
    for row in memberships:
        if not 0.0 <= row.confidence <= 1.0:
            errors.append(f"{row.security_id}: confidence outside [0,1]")
        if row.effective_to is not None and row.effective_to < row.effective_from:
            errors.append(f"{row.security_id}: effective_to before effective_from")
        by_key.setdefault((row.security_id, row.universe_code), []).append(row)

    for (security_id, universe_code), rows in by_key.items():
        rows = sorted(rows, key=lambda x: x.effective_from)
        previous: Optional[UniverseMembership] = None
        for current in rows:
            if previous is not None:
                prev_to = previous.effective_to
                if prev_to is None or current.effective_from <= prev_to:
                    errors.append(
                        f"{security_id}/{universe_code}: overlapping membership intervals"
                    )
            previous = current
    return errors


def coverage_report(
    as_of_dates: Iterable[date | datetime | str],
    memberships: Iterable[UniverseMembership],
    prices_by_date: Mapping[date, set[str]],
    *,
    universe_code: str = "PEA_ACTIONS",
    min_confidence: float = 1.0,
) -> list[dict[str, object]]:
    """Measure PIT membership/price coverage for certification reporting."""

    rows = list(memberships)
    report: list[dict[str, object]] = []
    for value in as_of_dates:
        d = _as_date(value)
        universe = universe_as_of(
            d,
            rows,
            universe_code=universe_code,
            min_confidence=min_confidence,
        )
        priced = prices_by_date.get(d, set())
        covered = universe & priced
        missing = universe - priced
        total = len(universe)
        report.append(
            {
                "date": d.isoformat(),
                "universe_count": total,
                "priced_count": len(covered),
                "missing_price_count": len(missing),
                "coverage_price_pct": (100.0 * len(covered) / total) if total else 100.0,
                "missing_security_ids": sorted(missing),
            }
        )
    return report
