from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class FeatureDiagnostics:
    provider: str
    as_of: datetime
    observed: int
    missing: int
    quarantined: int = 0
    latency_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureResult:
    rows: tuple[Mapping[str, Any], ...]
    diagnostics: FeatureDiagnostics


class FeatureProvider(ABC):
    """Contrat commun; aucun provider ne peut déclencher une décision ou un ordre."""

    name: str
    decision_influence: float = 0.0

    @abstractmethod
    def compute(self, *, as_of: datetime, universe: Iterable[str]) -> FeatureResult:
        raise NotImplementedError

    def validate_result(self, result: FeatureResult) -> None:
        if result.diagnostics.provider != self.name:
            raise ValueError("provider diagnostic mismatch")
        if result.diagnostics.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if self.decision_influence != 0.0:
            raise ValueError("pack providers must remain decision-neutral")
