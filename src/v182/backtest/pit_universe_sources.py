"""Authoritative-source hierarchy for survivorship-safe universe reconstruction.

This registry is intentionally separate from PIT consensus/fundamental sources.
It describes sources used to establish security identity, listing lifecycle and
historical universe membership.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable, Optional


class EvidenceKind(str, Enum):
    IDENTITY = "identity"
    LISTING = "listing"
    TERMINATION = "termination"
    CORPORATE_ACTION = "corporate_action"
    PEA_ELIGIBILITY = "pea_eligibility"


@dataclass(frozen=True)
class UniverseSource:
    code: str
    provider: str
    priority: int
    kinds: frozenset[EvidenceKind]
    coverage_from: Optional[date] = None
    coverage_to: Optional[date] = None
    authoritative: bool = True
    notes: str = ""

    def covers(self, d: date) -> bool:
        return (
            (self.coverage_from is None or self.coverage_from <= d)
            and (self.coverage_to is None or d <= self.coverage_to)
        )


SOURCES = (
    UniverseSource(
        code="EURONEXT_NOTICES",
        provider="Euronext",
        priority=1,
        kinds=frozenset({
            EvidenceKind.IDENTITY,
            EvidenceKind.LISTING,
            EvidenceKind.TERMINATION,
            EvidenceKind.CORPORATE_ACTION,
        }),
        coverage_from=date(2010, 1, 1),
        notes=(
            "Official cash-market notices. Delisting reason must be parsed: a market "
            "transfer is a listing event, not automatically an economic terminal event."
        ),
    ),
    UniverseSource(
        code="ESMA_FIRDS",
        provider="ESMA",
        priority=1,
        kinds=frozenset({EvidenceKind.IDENTITY, EvidenceKind.LISTING, EvidenceKind.TERMINATION}),
        coverage_from=date(2018, 1, 3),
        notes=(
            "MiFID II reference data; useful for ISIN/MIC admission and termination dates. "
            "Not a complete source for the 2010-2017 interval."
        ),
    ),
    UniverseSource(
        code="EURONEXT_SECURITY_PAGE",
        provider="Euronext",
        priority=2,
        kinds=frozenset({EvidenceKind.IDENTITY, EvidenceKind.LISTING, EvidenceKind.TERMINATION}),
        coverage_from=date(2010, 1, 1),
        notes="Instrument pages and linked official notices; useful for cross-checking.",
    ),
    UniverseSource(
        code="AMF_ISSUER_DOCUMENT",
        provider="AMF / issuer",
        priority=2,
        kinds=frozenset({EvidenceKind.CORPORATE_ACTION, EvidenceKind.IDENTITY}),
        coverage_from=date(2010, 1, 1),
        notes="Use for merger, squeeze-out, liquidation and acquisition terms.",
    ),
    UniverseSource(
        code="PEA_OFFICIAL_EVIDENCE",
        provider="French official/issuer evidence",
        priority=1,
        kinds=frozenset({EvidenceKind.PEA_ELIGIBILITY}),
        coverage_from=date(2010, 1, 1),
        notes="Historical PEA eligibility must be effective-dated; current eligibility is insufficient.",
    ),
)


def sources_for(kind: EvidenceKind, d: date) -> list[UniverseSource]:
    """Return applicable sources in deterministic evidence priority order."""
    return sorted(
        [s for s in SOURCES if kind in s.kinds and s.covers(d)],
        key=lambda s: (s.priority, s.code),
    )


def source_by_code(code: str) -> UniverseSource:
    for source in SOURCES:
        if source.code == code:
            return source
    raise KeyError(code)


def validate_registry(sources: Iterable[UniverseSource] = SOURCES) -> list[str]:
    rows = list(sources)
    errors: list[str] = []
    codes = [s.code for s in rows]
    if len(codes) != len(set(codes)):
        errors.append("duplicate source code")
    for s in rows:
        if s.priority < 1:
            errors.append(f"{s.code}: priority must be >=1")
        if not s.kinds:
            errors.append(f"{s.code}: no evidence kinds")
        if s.coverage_from and s.coverage_to and s.coverage_to < s.coverage_from:
            errors.append(f"{s.code}: invalid coverage interval")
    return errors
