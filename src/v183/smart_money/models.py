from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date, datetime
from hashlib import sha256
from typing import Any

VALID_EVIDENCE = {"A", "B", "C", "D"}
VALID_STATUS = {"VALIDATED", "ISIN_MATCHED", "AUTO_MATCH", "REVIEW", "REJECTED"}

@dataclass(frozen=True)
class SmartMoneyEvent:
    universe: str
    isin: str
    event_type: str
    event_subtype: str
    source: str
    evidence_level: str
    validation_status: str
    publication_date: str
    transaction_date: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    direction: int = 0
    quantity: float | None = None
    price: float | None = None
    currency: str | None = None
    value_eur: float | None = None
    stake_before: float | None = None
    stake_after: float | None = None
    threshold_pct: float | None = None
    short_position_pct: float | None = None
    source_document_id: str | None = None
    collected_at: str | None = None
    metadata: dict[str, Any] | None = None

    def validate(self) -> None:
        if self.universe not in {"ACTION", "ETF"}:
            raise ValueError("invalid universe")
        if len(self.isin) != 12 or not self.isin[:2].isalpha():
            raise ValueError("invalid ISIN")
        if self.evidence_level not in VALID_EVIDENCE:
            raise ValueError("invalid evidence_level")
        if self.validation_status not in VALID_STATUS:
            raise ValueError("invalid validation_status")
        if self.direction not in {-1, 0, 1}:
            raise ValueError("invalid direction")
        _parse_date(self.publication_date)
        if self.transaction_date:
            _parse_date(self.transaction_date)

    def event_id(self) -> str:
        key = "|".join([
            self.isin,
            self.event_type,
            self.event_subtype,
            (self.actor_name or "").strip().upper(),
            self.transaction_date or "",
            _num(self.quantity),
            _num(self.price),
            self.source_document_id or "",
        ])
        return sha256(key.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["event_id"] = self.event_id()
        return data


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value[:10]).date()


def _num(value: float | None) -> str:
    return "" if value is None else f"{float(value):.10g}"
