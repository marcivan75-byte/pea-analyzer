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
        governance = self.document.get("governance", {})
        forbidden = ["tracking_error_enabled", "adaptive_weights_enabled", "real_orders_enabled", "shadow_layers_live"]
        if any(governance.get(key) is not False for key in forbidden):
            raise RegistryIntegrityError("unsafe feature enabled")

    def payload_sha256(self) -> str:
        payload = json.dumps(self.criteria, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
