from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json

import pandas as pd

from v182.audit.provenance import load_latest, retained_meta_matches_value, value_hash
from v182.core.merge import ACCEPTED_VALIDATION_STATUSES, is_missing_value

ROOT = Path(__file__).resolve().parents[3]
STATE_COLUMNS = [
    "captured_at_utc",
    "universe",
    "isin",
    "field",
    "value",
    "source",
    "source_url",
    "evidence_level",
    "as_of",
    "validation_status",
    "value_sha256",
]


def load_state_config(path: str | Path | None = None) -> dict:
    resolved = Path(path) if path is not None else ROOT / "config" / "ETF_STRUCTURE_STATE_V21_15.json"
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ETF_STRUCTURE_STATE_CONFIG_OBJECT_REQUIRED")
    _validate_config(payload)
    return payload


def _validate_config(config: Mapping[str, Any]) -> None:
    configured = set(map(str, config.get("accepted_validation_statuses") or []))
    if configured != set(ACCEPTED_VALIDATION_STATUSES):
        raise ValueError("ETF_STRUCTURE_STATE_VALIDATION_STATUS_CONTRACT_DRIFT")
    fields = config.get("fields") or {}
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("ETF_STRUCTURE_STATE_FIELDS_REQUIRED")
    for field, spec in fields.items():
        if not str(field).strip() or not isinstance(spec, Mapping):
            raise ValueError("ETF_STRUCTURE_STATE_INVALID_FIELD_SPEC")
        ttl = int(spec.get("max_age_days", 0))
        if ttl <= 0:
            raise ValueError(f"ETF_STRUCTURE_STATE_INVALID_TTL:{field}")
    governance = config.get("governance") or {}
    required_false = (
        "daily_network_structural_scrape",
        "new_cron_created",
        "missing_imputation",
        "neutral_imputation",
        "weights_changed",
        "thresholds_changed",
        "decision_influence_changed",
        "t1_t2_scope_changed",
        "holdout_opened",
    )
    if any(governance.get(key) is not False for key in required_false):
        raise ValueError("ETF_STRUCTURE_STATE_GOVERNANCE_DRIFT")


def _utc(value: Any) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed


def _state_path(root: Path, config: Mapping[str, Any], override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    return root / str(config["state_path"])


def _state_digest(frame: pd.DataFrame) -> str:
    encoded = frame.to_csv(sep=";", index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_structural_state_snapshot(
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    root: Path = ROOT,
    state_path: str | Path | None = None,
    provenance_path: str | Path | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    """Persist only structural values bound to the provenance of the retained value.

    A legacy/current value without matching retained provenance is deliberately not
    copied into state. This prevents a state cache from upgrading an unattributed
    master cell into governed evidence merely because the value exists.
    """
    _validate_config(config)
    if "isin" not in frame.columns:
        raise ValueError("ETF_STRUCTURE_STATE_ISIN_REQUIRED")
    captured = _utc(now or datetime.now(timezone.utc))
    if captured is None:
        raise ValueError("ETF_STRUCTURE_STATE_INVALID_CAPTURE_TIME")
    metadata = load_latest(provenance_path)
    rows: list[dict[str, str]] = []
    skipped: Counter[str] = Counter()
    allowed_fields = tuple((config.get("fields") or {}).keys())
    accepted = set(map(str, config["accepted_validation_statuses"]))

    for _, instrument in frame.iterrows():
        isin = str(instrument.get("isin") or "").strip()
        if not isin:
            skipped["MISSING_ISIN"] += 1
            continue
        for field in allowed_fields:
            if field not in frame.columns:
                continue
            value = instrument.get(field)
            if is_missing_value(value):
                continue
            meta = metadata.get((isin, str(field)))
            if not meta:
                skipped["NO_RETAINED_PROVENANCE"] += 1
                continue
            if not retained_meta_matches_value(meta, value):
                skipped["PROVENANCE_VALUE_HASH_MISMATCH"] += 1
                continue
            validation_status = str(meta.get("validation_status") or "").strip()
            if validation_status not in accepted:
                skipped["VALIDATION_STATUS_REJECTED"] += 1
                continue
            as_of = _utc(meta.get("as_of"))
            if as_of is None:
                skipped["AS_OF_UNPARSEABLE"] += 1
                continue
            if as_of > captured + pd.Timedelta(days=1):
                skipped["AS_OF_FUTURE"] += 1
                continue
            evidence = str(meta.get("evidence_level") or "").strip().upper()
            if evidence not in {"A", "B", "C", "D"}:
                skipped["EVIDENCE_REJECTED"] += 1
                continue
            rows.append(
                {
                    "captured_at_utc": captured.isoformat(),
                    "universe": "ETF",
                    "isin": isin,
                    "field": str(field),
                    "value": str(value),
                    "source": str(meta.get("source") or ""),
                    "source_url": str(meta.get("source_url") or ""),
                    "evidence_level": evidence,
                    "as_of": as_of.isoformat(),
                    "validation_status": validation_status,
                    "value_sha256": value_hash(value),
                }
            )

    state = pd.DataFrame(rows, columns=STATE_COLUMNS)
    if not state.empty:
        state = state.sort_values(["isin", "field"]).drop_duplicates(["isin", "field"], keep="last")
    target = _state_path(root, config, state_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state.to_csv(target, sep=";", index=False, encoding="utf-8-sig")
    by_field = Counter(state["field"].astype(str)) if not state.empty else Counter()
    by_evidence = Counter(state["evidence_level"].astype(str)) if not state.empty else Counter()
    return {
        "version": config.get("version"),
        "status": "SUCCESS",
        "state_path": str(target.relative_to(root)) if target.is_relative_to(root) else str(target),
        "rows": int(len(state)),
        "unique_isins": int(state["isin"].nunique()) if not state.empty else 0,
        "rows_by_field": {key: int(value) for key, value in sorted(by_field.items())},
        "rows_by_evidence": {key: int(value) for key, value in sorted(by_evidence.items())},
        "skipped": {key: int(value) for key, value in sorted(skipped.items())},
        "sha256": _state_digest(state),
        "governance": {
            "matching_retained_provenance_required": True,
            "actual_values_persisted": True,
            "missing_imputation": False,
            "validation_status_contract_widened": False,
        },
    }


def load_replay_observations(
    config: Mapping[str, Any],
    *,
    root: Path = ROOT,
    state_path: str | Path | None = None,
    as_of: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load only fresh, internally consistent state rows as normal merge observations."""
    _validate_config(config)
    target = _state_path(root, config, state_path)
    now = _utc(as_of or datetime.now(timezone.utc))
    if now is None:
        raise ValueError("ETF_STRUCTURE_STATE_INVALID_REPLAY_TIME")
    base_diag: dict[str, Any] = {
        "version": config.get("version"),
        "state_path": str(target.relative_to(root)) if target.is_relative_to(root) else str(target),
        "replay_as_of": now.isoformat(),
        "missing_imputation": False,
        "validation_status_contract_widened": False,
    }
    if not target.exists() or target.stat().st_size == 0:
        return [], {**base_diag, "status": "NO_STATE", "rows": 0, "eligible_rows": 0}

    try:
        state = pd.read_csv(target, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return [], {
            **base_diag,
            "status": "STATE_READ_FAILED",
            "rows": 0,
            "eligible_rows": 0,
            "error": type(exc).__name__,
        }
    required = set(STATE_COLUMNS)
    missing_columns = sorted(required - set(state.columns))
    if missing_columns:
        return [], {
            **base_diag,
            "status": "STATE_SCHEMA_INVALID",
            "rows": int(len(state)),
            "eligible_rows": 0,
            "missing_columns": missing_columns,
        }

    duplicate_mask = state.duplicated(["isin", "field"], keep=False)
    duplicate_keys = set(map(tuple, state.loc[duplicate_mask, ["isin", "field"]].astype(str).to_records(index=False)))
    accepted = set(map(str, config["accepted_validation_statuses"]))
    field_specs: Mapping[str, Any] = config["fields"]
    observations: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    eligible_by_field: Counter[str] = Counter()

    for _, row in state.iterrows():
        isin = str(row.get("isin") or "").strip()
        field = str(row.get("field") or "").strip()
        key = (isin, field)
        if key in duplicate_keys:
            rejected["DUPLICATE_KEY"] += 1
            continue
        if str(row.get("universe") or "") != "ETF" or not isin:
            rejected["IDENTITY_INVALID"] += 1
            continue
        if field not in field_specs:
            rejected["FIELD_NOT_ALLOWED"] += 1
            continue
        value = row.get("value")
        if is_missing_value(value):
            rejected["MISSING_VALUE"] += 1
            continue
        if value_hash(value) != str(row.get("value_sha256") or "").strip():
            rejected["VALUE_HASH_MISMATCH"] += 1
            continue
        validation_status = str(row.get("validation_status") or "").strip()
        if validation_status not in accepted:
            rejected["VALIDATION_STATUS_REJECTED"] += 1
            continue
        evidence = str(row.get("evidence_level") or "").strip().upper()
        if evidence not in {"A", "B", "C", "D"}:
            rejected["EVIDENCE_REJECTED"] += 1
            continue
        observed_at = _utc(row.get("as_of"))
        captured_at = _utc(row.get("captured_at_utc"))
        if observed_at is None or captured_at is None:
            rejected["TIMESTAMP_UNPARSEABLE"] += 1
            continue
        if observed_at > now + pd.Timedelta(days=1) or captured_at > now + pd.Timedelta(days=1):
            rejected["TIMESTAMP_FUTURE"] += 1
            continue
        ttl_days = int(field_specs[field]["max_age_days"])
        age_days = (now - observed_at).total_seconds() / 86400.0
        if age_days > ttl_days:
            rejected["STALE"] += 1
            continue
        observations.append(
            {
                "universe": "ETF",
                "isin": isin,
                "field": field,
                "value": value,
                "source": str(row.get("source") or ""),
                "source_url": str(row.get("source_url") or ""),
                "evidence_level": evidence,
                "as_of": observed_at.isoformat(),
                "validation_status": validation_status,
            }
        )
        eligible_by_field[field] += 1

    status = "SUCCESS" if observations else "NO_ELIGIBLE_STATE_ROWS"
    return observations, {
        **base_diag,
        "status": status,
        "rows": int(len(state)),
        "eligible_rows": int(len(observations)),
        "eligible_isins": int(len({row["isin"] for row in observations})),
        "eligible_by_field": {key: int(value) for key, value in sorted(eligible_by_field.items())},
        "rejected": {key: int(value) for key, value in sorted(rejected.items())},
        "state_sha256": _state_digest(state[STATE_COLUMNS]),
    }
