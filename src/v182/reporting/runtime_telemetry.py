from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import csv
import json
import os
import time
from typing import Any


RUNTIME_VERSION = "PIPELINE_RUNTIME_V21_16_3"
DURATION_CONTRACT = Path(__file__).resolve().parents[3] / "config" / "RUNTIME_DURATION_CONTRACT_V21_16.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round_seconds(value: float) -> float:
    return round(max(0.0, float(value)), 6)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _load_duration_contract() -> dict:
    if not DURATION_CONTRACT.exists():
        return {}
    try:
        payload = json.loads(DURATION_CONTRACT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _design_budget(contract: dict) -> dict:
    """Return the newest versioned static budget while remaining backward compatible."""
    preferred = (
        "v21_16_3_static_design_budget",
        "v21_16_2_static_design_budget",
        "v21_16_1_static_design_budget",
    )
    for key in preferred:
        value = contract.get(key)
        if isinstance(value, dict):
            return value
    # Future minor versions may change only the suffix. Choose the lexically
    # latest governed V21.16 static design block rather than silently returning
    # an empty contract.
    candidates = sorted(
        key
        for key, value in contract.items()
        if key.startswith("v21_16_") and key.endswith("_static_design_budget") and isinstance(value, dict)
    )
    return contract[candidates[-1]] if candidates else {}


def _budget_for_profile(profile: str) -> dict:
    contract = _load_duration_contract()
    budget = _design_budget(contract)
    normalized = str(profile or "").strip().upper()
    if normalized == "DAILY_TACTICAL":
        expected = budget.get("daily_expected_wall_range_minutes")
        alert = budget.get("daily_alert_wall_minutes")
        billable = budget.get("daily_billable_budget_minutes")
        scope = "DAILY_TACTICAL_MON_THU"
    elif normalized == "WEEKLY_FULL_COMMITTEE":
        expected = budget.get("weekly_expected_wall_range_minutes")
        alert = budget.get("weekly_alert_wall_minutes")
        billable = budget.get("weekly_billable_budget_minutes")
        scope = "WEEKLY_FULL_COMMITTEE_FRIDAY"
    else:
        return {}
    return {
        "contract_version": contract.get("version"),
        "scope": scope,
        "expected_wall_range_minutes": expected,
        "billable_budget_minutes": billable,
        "alert_wall_minutes": alert,
        "targets_are_not_observed_runtime": bool(budget.get("targets_are_not_observed_runtime", True)),
        "measurement_status": budget.get("measurement_status"),
    }


def _budget_result(profile: str, wall_seconds: float) -> dict:
    budget = _budget_for_profile(profile)
    if not budget:
        return {}
    wall_minutes = max(0.0, float(wall_seconds)) / 60.0
    expected = budget.get("expected_wall_range_minutes")
    alert = budget.get("alert_wall_minutes")
    upper = None
    if isinstance(expected, list) and len(expected) >= 2:
        try:
            upper = float(expected[1])
        except (TypeError, ValueError):
            upper = None
    try:
        alert_value = float(alert) if alert is not None else None
    except (TypeError, ValueError):
        alert_value = None
    status = "MEASURED_NO_THRESHOLD"
    if alert_value is not None and wall_minutes > alert_value:
        status = "ALERT_EXCEEDED"
    elif upper is not None and wall_minutes > upper:
        status = "ABOVE_DESIGN_RANGE_BELOW_ALERT"
    elif upper is not None:
        status = "WITHIN_STATIC_DESIGN_RANGE"
    return {
        **budget,
        "measured_wall_minutes": round(wall_minutes, 4),
        "measurement_comparison_status": status,
    }


class RuntimeTelemetry:
    """Persist wall/CPU stage timings without changing pipeline decisions."""

    def __init__(self, output_dir: str | Path, *, run_id: str, profile: str) -> None:
        self.output_dir = Path(output_dir)
        self.run_id = str(run_id)
        self.profile = str(profile)
        self.json_path = self.output_dir / f"{RUNTIME_VERSION}.json"
        self.csv_path = self.output_dir / f"{RUNTIME_VERSION}.csv"
        self.started_at_utc = _utc_now()
        self._wall_start = time.perf_counter()
        self._cpu_start = time.process_time()
        self._active: dict[str, Any] | None = None
        self._stages: list[dict[str, Any]] = []
        self._status = "RUNNING"
        self._extra: dict[str, Any] = {}
        self._write()

    @property
    def paths(self) -> dict[str, str]:
        return {"json": str(self.json_path), "csv": str(self.csv_path)}

    def transition(self, name: str, category: str) -> None:
        now_wall = time.perf_counter()
        now_cpu = time.process_time()
        self._close_active(now_wall, now_cpu, "SUCCESS")
        self._active = {
            "name": str(name),
            "category": str(category).upper(),
            "started_at_utc": _utc_now(),
            "wall_start": now_wall,
            "cpu_start": now_cpu,
        }
        self._write()

    def finalize(self, status: str, **extra: Any) -> dict[str, str]:
        if self._status != "RUNNING":
            return self.paths
        normalized = str(status).upper()
        stage_status = "SUCCESS" if normalized == "SUCCESS" else normalized
        self._close_active(time.perf_counter(), time.process_time(), stage_status)
        self._status = normalized
        effective_audit_format = os.environ.get("PEA_EFFECTIVE_INTERMEDIATE_AUDIT_FORMAT", "").strip().upper()
        if effective_audit_format and "intermediate_collection_audit_format" in extra:
            extra["intermediate_collection_audit_format"] = effective_audit_format
        self._extra.update(extra)
        self._write()
        return self.paths

    def _close_active(self, now_wall: float, now_cpu: float, status: str) -> None:
        if self._active is None:
            return
        self._stages.append(
            {
                "sequence": len(self._stages) + 1,
                "name": self._active["name"],
                "category": self._active["category"],
                "status": status,
                "started_at_utc": self._active["started_at_utc"],
                "ended_at_utc": _utc_now(),
                "wall_seconds": _round_seconds(now_wall - self._active["wall_start"]),
                "cpu_seconds": _round_seconds(now_cpu - self._active["cpu_start"]),
            }
        )
        self._active = None

    def _payload(self) -> dict[str, Any]:
        now_wall = time.perf_counter()
        now_cpu = time.process_time()
        wall_seconds = _round_seconds(now_wall - self._wall_start)
        totals_by_category: dict[str, float] = {}
        for stage in self._stages:
            category = str(stage["category"])
            totals_by_category[category] = totals_by_category.get(category, 0.0) + float(stage["wall_seconds"])
        active = None
        if self._active is not None:
            active = {
                "name": self._active["name"],
                "category": self._active["category"],
                "started_at_utc": self._active["started_at_utc"],
                "wall_seconds_so_far": _round_seconds(now_wall - self._active["wall_start"]),
            }
        return {
            "version": RUNTIME_VERSION,
            "status": self._status,
            "run_id": self.run_id,
            "profile": self.profile,
            "started_at_utc": self.started_at_utc,
            "updated_at_utc": _utc_now(),
            "wall_seconds": wall_seconds,
            "cpu_seconds": _round_seconds(now_cpu - self._cpu_start),
            "totals_by_category_seconds": {key: _round_seconds(value) for key, value in sorted(totals_by_category.items())},
            "duration_contract": _budget_result(self.profile, wall_seconds),
            "active_stage": active,
            "stages": self._stages,
            "decision_logic_changed": False,
            **self._extra,
        }

    def _write(self) -> None:
        payload = self._payload()
        _atomic_text(self.json_path, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        fields = ["sequence", "name", "category", "status", "started_at_utc", "ended_at_utc", "wall_seconds", "cpu_seconds"]
        rows = [dict(row) for row in self._stages]
        temporary = self.csv_path.with_name(f".{self.csv_path.name}.{os.getpid()}.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.csv_path)


def write_step_runtime(
    output_dir: str | Path,
    *,
    run_id: str,
    profile: str,
    wall_seconds: float,
    cpu_seconds: float,
    steps: dict[str, dict],
) -> dict[str, str]:
    """Write the unified runner's already-measured step durations."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "UNIFIED_RUNTIME_V21_16_3.json"
    csv_path = root / "UNIFIED_RUNTIME_V21_16_3.csv"
    rows = []
    for sequence, (name, step) in enumerate(steps.items(), start=1):
        rows.append(
            {
                "sequence": sequence,
                "name": name,
                "status": step.get("status", "UNKNOWN"),
                "wall_seconds": step.get("wall_seconds"),
                "cpu_seconds": step.get("cpu_seconds"),
            }
        )
    payload = {
        "version": "UNIFIED_RUNTIME_V21_16_3",
        "status": "SUCCESS",
        "run_id": run_id,
        "profile": profile,
        "generated_at_utc": _utc_now(),
        "wall_seconds": _round_seconds(wall_seconds),
        "cpu_seconds": _round_seconds(cpu_seconds),
        "duration_contract": _budget_result(profile, wall_seconds),
        "steps": rows,
        "decision_logic_changed": False,
    }
    _atomic_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    fields = ["sequence", "name", "status", "wall_seconds", "cpu_seconds"]
    temporary = csv_path.with_name(f".{csv_path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(csv_path)
    return {"json": str(json_path), "csv": str(csv_path)}
