from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os

import pandas as pd

from v182.audit.provenance import actual_sources_by_field
from v182.reporting import collection_audit as base


VERSION = "INCREMENTAL_COLLECTION_AUDIT_V21_15_4"
SNAPSHOT_VERSION = "COLLECTION_AUDIT_SNAPSHOT_V1"


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    """Stable content fingerprint used only to prove snapshot/master identity."""
    digest = sha256()
    digest.update(str(tuple(frame.columns)).encode("utf-8"))
    digest.update(str(tuple(str(dtype) for dtype in frame.dtypes)).encode("utf-8"))
    if not frame.empty:
        hashed = pd.util.hash_pandas_object(frame, index=True, categorize=True).to_numpy(dtype="uint64", copy=False)
        digest.update(hashed.tobytes())
    return digest.hexdigest()


class IncrementalCollectionAuditor:
    """Patch only fields touched since the last audit; keep WAVE_99 exhaustive."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_dir = root.parent.parent / "state" / "provenance" / "daily_fast_master_v1"
        self.snapshot_inventory = self.state_dir / "collection_audit_inventory.parquet"
        self.snapshot_provenance = self.state_dir / "collection_audit_provenance.parquet"
        self.snapshot_meta = self.state_dir / "collection_audit_snapshot.json"
        self.touched: dict[str, set[str]] = {"ACTION": set(), "ETF": set()}
        self.initialized = False
        self.snapshot_candidate: dict | None = self._load_snapshot_meta()
        self.snapshot_reused = False
        self.snapshot_identity_mismatch = False
        self.full_scans = 0
        self.incremental_scans = 0
        self.reused_scans = 0
        self.fallback_full_scans = 0
        self.fields_recomputed = 0

    def _load_snapshot_meta(self) -> dict | None:
        if not self.snapshot_meta.exists() or not self.snapshot_inventory.exists() or not self.snapshot_provenance.exists():
            return None
        try:
            payload = json.loads(self.snapshot_meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
            return None
        return payload

    def _try_seed_snapshot(self, actions: pd.DataFrame, etfs: pd.DataFrame) -> bool:
        payload = self.snapshot_candidate
        if not payload:
            return False
        try:
            if payload.get("actions_fingerprint") != _frame_fingerprint(actions):
                self.snapshot_identity_mismatch = True
                return False
            if payload.get("etf_fingerprint") != _frame_fingerprint(etfs):
                self.snapshot_identity_mismatch = True
                return False
            inventory = pd.read_parquet(self.snapshot_inventory)
            provenance = pd.read_parquet(self.snapshot_provenance)
            if inventory.empty or not {"asset_class", "field", "status"}.issubset(inventory.columns):
                return False
            with base._AUDIT_CACHE_LOCK:
                base._LAST_INVENTORY = inventory.copy(deep=True)
                base._LAST_PROVENANCE = provenance.copy(deep=True)
            self.initialized = True
            self.snapshot_reused = True
            return True
        except Exception:
            return False

    def _persist_final_snapshot(self, actions: pd.DataFrame, etfs: pd.DataFrame) -> None:
        """Persist WAVE_99 inventory; next run reuses it only on exact master identity."""
        try:
            with base._AUDIT_CACHE_LOCK:
                inventory = None if base._LAST_INVENTORY is None else base._LAST_INVENTORY.copy(deep=True)
                provenance = None if base._LAST_PROVENANCE is None else base._LAST_PROVENANCE.copy(deep=True)
            if inventory is None or provenance is None or inventory.empty:
                return
            self.state_dir.mkdir(parents=True, exist_ok=True)
            inv_tmp = self.snapshot_inventory.with_suffix(".parquet.tmp")
            prov_tmp = self.snapshot_provenance.with_suffix(".parquet.tmp")
            inventory.to_parquet(inv_tmp, index=False)
            provenance.to_parquet(prov_tmp, index=False)
            inv_tmp.replace(self.snapshot_inventory)
            prov_tmp.replace(self.snapshot_provenance)
            payload = {
                "version": SNAPSHOT_VERSION,
                "actions_fingerprint": _frame_fingerprint(actions),
                "etf_fingerprint": _frame_fingerprint(etfs),
                "inventory_rows": int(len(inventory)),
                "provenance_rows": int(len(provenance)),
                "final_wave_exhaustive": True,
                "decision_logic_changed": False,
            }
            temp = self.snapshot_meta.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.snapshot_meta)
        except Exception:
            # Snapshotting is only an optimization; exhaustive audit remains authoritative.
            return

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
        # Seed only when the persisted final inventory matches both retained masters
        # exactly. Any mismatch falls back to the authoritative full initial scan.
        if not self.initialized and wave_id != "WAVE_99_FINAL":
            self._try_seed_snapshot(actions, etfs)

        # WAVE_99 is always exhaustive, irrespective of prior incremental success.
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
            if wave_id == "WAVE_99_FINAL":
                self._persist_final_snapshot(actions, etfs)
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
            "initial_snapshot_reused": bool(self.snapshot_reused),
            "initial_snapshot_identity_mismatch": bool(self.snapshot_identity_mismatch),
            "initial_snapshot_fingerprint_verified": bool(self.snapshot_reused),
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
