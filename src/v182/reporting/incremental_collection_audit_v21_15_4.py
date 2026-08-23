from __future__ import annotations

from pathlib import Path
import json
import os

import pandas as pd

from v182.audit.provenance import actual_sources_by_field
from v182.reporting import collection_audit as base


VERSION = "INCREMENTAL_COLLECTION_AUDIT_V21_15_4"


class IncrementalCollectionAuditor:
    """Patch only fields touched since the last audit; keep WAVE_99 exhaustive."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.touched: dict[str, set[str]] = {"ACTION": set(), "ETF": set()}
        self.initialized = False
        self.full_scans = 0
        self.incremental_scans = 0
        self.reused_scans = 0
        self.fallback_full_scans = 0
        self.fields_recomputed = 0

    def note(self, observations) -> None:
        if not observations:
            return
        for row in observations:
            if not isinstance(row, dict):
                continue
            universe = str(row.get("universe") or "").upper()
            field = str(row.get("field") or "").strip()
            if universe in self.touched and field and field not in {"isin", "name"}:
                self.touched[universe].add(field)

    def _clear(self) -> None:
        for fields in self.touched.values():
            fields.clear()

    @staticmethod
    def _merge_provenance(status: pd.DataFrame, provenance: pd.DataFrame) -> pd.DataFrame:
        if status.empty:
            return status
        if not provenance.empty:
            prov = provenance.rename(columns={"universe": "asset_class"})
            status = status.merge(prov, on=["asset_class", "field"], how="left")
        else:
            for column in ("sources_reelles", "source_urls", "evidence_levels", "last_as_of"):
                status[column] = ""
        for column in ("sources_reelles", "source_urls", "evidence_levels", "last_as_of"):
            if column not in status.columns:
                status[column] = ""
        status["source_reelle_absente"] = status["sources_reelles"].fillna("").astype(str).str.strip().eq("")
        return status

    def _patch_inventory(self, actions: pd.DataFrame, etfs: pd.DataFrame, wave_id: str) -> int:
        patches: list[pd.DataFrame] = []
        for universe, frame in (("ACTION", actions), ("ETF", etfs)):
            fields = [field for field in frame.columns if field in self.touched[universe]]
            if not fields:
                continue
            patches.append(base._field_status(frame[fields], universe, wave_id))
        if not patches:
            return 0

        patch = pd.concat(patches, ignore_index=True, sort=False)
        provenance = actual_sources_by_field()
        patch = self._merge_provenance(patch, provenance)
        patch_keys = set(zip(patch["asset_class"].astype(str), patch["field"].astype(str)))

        with base._AUDIT_CACHE_LOCK:
            if base._LAST_INVENTORY is None:
                raise RuntimeError("INCREMENTAL_AUDIT_BASE_INVENTORY_MISSING")
            current = base._LAST_INVENTORY.copy(deep=True)
            if not current.empty:
                current_keys = list(zip(current["asset_class"].astype(str), current["field"].astype(str)))
                keep = [key not in patch_keys for key in current_keys]
                current = current.loc[keep].copy()
            base._LAST_INVENTORY = pd.concat([current, patch], ignore_index=True, sort=False)
            base._LAST_PROVENANCE = provenance.copy(deep=True)
        return int(len(patch))

    def audit(
        self,
        actions: pd.DataFrame,
        etfs: pd.DataFrame,
        wave_id: str,
        *,
        failures: list[dict] | None,
        source_context: str,
        original_audit,
    ) -> None:
        # Initial inventory establishes the authoritative full state. WAVE_99 is
        # always exhaustive, irrespective of prior incremental success.
        if not self.initialized or wave_id == "WAVE_99_FINAL":
            original_audit(
                actions,
                etfs,
                wave_id,
                failures=failures,
                source_context=source_context,
            )
            self.full_scans += 1
            self.initialized = True
            self._clear()
            return

        try:
            recomputed = self._patch_inventory(actions, etfs, wave_id)
            daily_profile = os.environ.get("PEA_RUN_PROFILE", "").strip().upper() == "DAILY_TACTICAL"
            base.write_collection_audit(
                actions,
                etfs,
                wave_id,
                self.root,
                failures=failures,
                source_context=source_context,
                write_excel=not daily_profile or wave_id == "WAVE_99_FINAL",
                reuse_previous_state=True,
            )
            if recomputed:
                self.incremental_scans += 1
                self.fields_recomputed += recomputed
            else:
                self.reused_scans += 1
        except Exception:
            # Observability optimization must never weaken or block collection.
            original_audit(
                actions,
                etfs,
                wave_id,
                failures=failures,
                source_context=source_context,
            )
            self.fallback_full_scans += 1
        finally:
            self._clear()

    def payload(self) -> dict:
        return {
            "version": VERSION,
            "full_scans": int(self.full_scans),
            "incremental_scans": int(self.incremental_scans),
            "unchanged_inventory_reuses": int(self.reused_scans),
            "fail_closed_full_scan_fallbacks": int(self.fallback_full_scans),
            "fields_recomputed_incrementally": int(self.fields_recomputed),
            "final_wave_exhaustive": True,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
        }

    def write_audit(self) -> None:
        path = self.root.parent / "audit" / f"{VERSION}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=2), encoding="utf-8")