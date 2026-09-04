"""Structured ingestion helpers for historical-universe reconstruction.

The rule is conservative: a market delisting notice is evidence of a listing
lifecycle change, not automatically evidence of an economic terminal event.
Ambiguous records are quarantined rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional

from .pit_universe import ExitReason, ListingEventType


@dataclass(frozen=True)
class NormalizedExitEvidence:
    security_id: str
    effective_date: date
    listing_event_type: ListingEventType
    terminal_reason: Optional[ExitReason]
    source_code: str
    evidence_text: str
    confidence: float
    quarantine_reason: Optional[str] = None

    @property
    def quarantined(self) -> bool:
        return self.quarantine_reason is not None


def _text(row: Mapping[str, object]) -> str:
    fields = [
        row.get("title"), row.get("reason"), row.get("description"),
        row.get("event_type"), row.get("operation"),
    ]
    return " ".join(str(v) for v in fields if v not in (None, "")).strip()


def classify_exit_evidence(
    *, security_id: str, effective_date: date, source_code: str,
    row: Mapping[str, object],
) -> NormalizedExitEvidence:
    """Classify official exit evidence without inferring unsupported losses.

    Text rules are intentionally narrow and are only a first normalization pass.
    Every uncertain case remains visible in quarantine for manual/source-specific
    resolution.
    """
    evidence = _text(row)
    low = evidence.casefold()

    # Venue transfer: preserve economic identity and do not manufacture a loss.
    transfer_tokens = (
        "transfer to alternext", "transfert sur alternext",
        "transfer to euronext growth", "transfert sur euronext growth",
        "transfer to euronext access", "transfert sur euronext access",
        "market transfer", "transfert de marché",
    )
    if any(token in low for token in transfer_tokens):
        return NormalizedExitEvidence(
            security_id, effective_date, ListingEventType.MARKET_TRANSFER, None,
            source_code, evidence, 1.0,
        )

    # Explicit economic terminal outcomes only.
    if any(token in low for token in ("bankrupt", "bankruptcy", "faillite", "liquidation judiciaire")):
        terminal = ExitReason.BANKRUPTCY
    elif any(token in low for token in ("liquidation", "dissolution")):
        terminal = ExitReason.LIQUIDATED
    elif any(token in low for token in ("cash acquisition", "cash offer", "offre publique de retrait", "retrait obligatoire", "squeeze-out")):
        terminal = ExitReason.ACQUIRED_CASH
    elif any(token in low for token in ("merger", "fusion", "exchange offer", "offre publique d'échange")):
        terminal = ExitReason.MERGED
    elif any(token in low for token in ("security cancelled", "cancellation of shares", "annulation des actions")):
        terminal = ExitReason.SECURITY_CANCELLED
    else:
        terminal = None

    if terminal is not None:
        return NormalizedExitEvidence(
            security_id, effective_date, ListingEventType.DELISTED, terminal,
            source_code, evidence, 1.0,
        )

    # A bare delisting/termination is real listing evidence but not enough to
    # infer the economic outcome or PEA exit reason.
    if any(token in low for token in ("delist", "radiation", "termination", "cessation")):
        return NormalizedExitEvidence(
            security_id, effective_date, ListingEventType.DELISTED, None,
            source_code, evidence, 0.75,
            quarantine_reason="DELISTING_WITHOUT_ECONOMIC_OUTCOME",
        )

    return NormalizedExitEvidence(
        security_id, effective_date, ListingEventType.DELISTED, None,
        source_code, evidence, 0.0,
        quarantine_reason="UNCLASSIFIED_EXIT_EVIDENCE",
    )


def build_exit_audit(rows: list[NormalizedExitEvidence]) -> dict[str, object]:
    quarantined = [r for r in rows if r.quarantined]
    terminal = [r for r in rows if r.terminal_reason is not None]
    transfers = [r for r in rows if r.listing_event_type is ListingEventType.MARKET_TRANSFER]
    return {
        "record_count": len(rows),
        "terminal_event_count": len(terminal),
        "market_transfer_count": len(transfers),
        "quarantine_count": len(quarantined),
        "quarantine_security_ids": sorted({r.security_id for r in quarantined}),
        "strict_exit_evidence_ready": len(quarantined) == 0,
    }
