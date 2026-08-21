from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class RegistryIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class CriterionRegistry:
    document: dict

    @classmethod
    def load(cls, path: str | Path) -> CriterionRegistry:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def criteria(self) -> list[dict]:
        return self.document["criteria"]

    def validate(self) -> None:
        expected = self.document.get("criteria_count")
        if expected != 268 or len(self.criteria) != 268:
            raise RegistryIntegrityError(f"criteria_count mismatch: {expected=} actual={len(self.criteria)}")
        ids = [row["criterion_id"] for row in self.criteria]
        if len(ids) != len(set(ids)):
            raise RegistryIntegrityError("duplicate criterion_id")
        required = {"criterion_id", "name", "family", "horizon", "direction", "pit_required", "status", "provenance"}
        for index, row in enumerate(self.criteria, 1):
            missing = required.difference(row)
            if missing:
                raise RegistryIntegrityError(f"row {index}: missing {sorted(missing)}")
        allowed = {
            "status": {"ACTIVE", "CONFIGURED_NO_CANONICAL_268_ROW_PROOF"},
            "horizon": {"CT", "LT", "MT", "MULTI", "SHORT", "TOP_DOWN"},
            "direction": {"GOVERNED_RULE", "HIGH", "LOW"},
            "missing_policy": {
                "BLOCK",
                "BLOCK_COMMITTEE",
                "BLOCK_EXECUTION",
                "CONTRACT_TO_50",
                "FAIL_CLOSED_NO_NEUTRAL_IMPUTATION",
            },
        }
        for field, values in allowed.items():
            invalid = sorted({str(row.get(field)) for row in self.criteria} - values)
            if invalid:
                raise RegistryIntegrityError(f"invalid {field}: {invalid}")
        governance = self.document.get("governance", {})
        forbidden = ["tracking_error_enabled", "adaptive_weights_enabled", "real_orders_enabled", "shadow_layers_live"]
        if any(governance.get(key) is not False for key in forbidden):
            raise RegistryIntegrityError("unsafe feature enabled")
        if self.document.get("registry_payload_sha256") != self.payload_sha256():
            raise RegistryIntegrityError("registry payload hash mismatch")
        if self.document.get("expected_ordered_names_sha256") != self.ordered_names_sha256():
            raise RegistryIntegrityError("ordered names hash mismatch")

    def payload_sha256(self) -> str:
        payload = json.dumps(self.criteria, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def ordered_names_sha256(self) -> str:
        payload = "\n".join(str(row["name"]) for row in self.criteria).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def filter(
        self, *, family: str | None = None, horizon: str | None = None, status: str | None = None
    ) -> tuple[dict, ...]:
        filters = {"family": family, "horizon": horizon, "status": status}
        return tuple(
            row
            for row in self.criteria
            if all(value is None or row.get(key) == value for key, value in filters.items())
        )

    def weights_normalized(self, *, horizon: str | None = None) -> dict[str, float]:
        rows = self.filter(horizon=horizon) if horizon else tuple(self.criteria)
        positive = {str(row["criterion_id"]): max(float(row.get("weight_default") or 0.0), 0.0) for row in rows}
        total = sum(positive.values())
        return {} if total <= 0 else {key: value / total for key, value in positive.items()}
