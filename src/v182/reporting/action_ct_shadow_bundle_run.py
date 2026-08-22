from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable
import json
import traceback

import pandas as pd

from v182.reporting import action_ct_shadow_run_v22_0 as v220
from v182.reporting import action_ct_shadow_run_v22_1 as v221


ROOT = Path(__file__).resolve().parents[3]
VERSION = "V21.13.10_ACTION_CT_SHARED_HISTORY_RUNTIME"


@dataclass
class SharedHistoryLoader:
    """Reuse physical parquet reads while isolating each model with deep copies.

    The first request for a cache directory uses the original governed loader.
    A later request for the same or a smaller ticker set is served from memory,
    but every consumer receives a deep copy so no model can mutate another
    model's input. If a later request expands the ticker set, the governed
    loader is called again on the union rather than silently returning gaps.
    """

    original_loader: Callable[[Path, set[str]], dict[str, pd.DataFrame]]
    _cache: dict[str, tuple[frozenset[str], dict[str, pd.DataFrame]]] = field(default_factory=dict)
    logical_requests: int = 0
    physical_loads: int = 0
    physical_load_seconds: float = 0.0
    copied_frames: int = 0

    def __call__(self, cache_dir: Path, wanted: set[str]) -> dict[str, pd.DataFrame]:
        self.logical_requests += 1
        requested = {str(ticker).strip() for ticker in wanted if str(ticker).strip()}
        if not requested:
            return {}

        directory = str(Path(cache_dir).resolve())
        cached = self._cache.get(directory)
        if cached is None or not requested.issubset(cached[0]):
            union = requested if cached is None else set(cached[0]).union(requested)
            started = perf_counter()
            loaded = self.original_loader(Path(cache_dir), union)
            self.physical_load_seconds += perf_counter() - started
            self.physical_loads += 1
            cached = (frozenset(union), loaded)
            self._cache[directory] = cached

        histories = {
            ticker: frame.copy(deep=True)
            for ticker, frame in cached[1].items()
            if ticker in requested and frame is not None and not frame.empty
        }
        self.copied_frames += len(histories)
        return histories

    def audit(self) -> dict:
        return {
            "logical_history_requests": int(self.logical_requests),
            "physical_parquet_batch_loads": int(self.physical_loads),
            "avoided_physical_batch_loads": int(max(0, self.logical_requests - self.physical_loads)),
            "physical_load_seconds": round(float(self.physical_load_seconds), 6),
            "deep_copied_consumer_frames": int(self.copied_frames),
            "consumer_isolation": "DEEP_COPY_PER_MODEL",
            "source_loader_unchanged": True,
        }


def _run_model(name: str, runner: Callable[..., dict], root: Path, now: datetime) -> tuple[dict, dict | None]:
    try:
        payload = runner(root=root, now=now)
        return payload, None
    except Exception as exc:  # preserve separate-step behavior: the second model still runs
        return {}, {
            "model": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=5),
        }


def run(root: Path = ROOT, now: datetime | None = None) -> dict:
    started = perf_counter()
    now = now or datetime.now(timezone.utc)
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_loader = v220._extract_histories
    shared_loader = SharedHistoryLoader(original_loader)
    v220._extract_histories = shared_loader
    try:
        parent, parent_error = _run_model("ACTION_CT_V22.0", v220.run, root, now)
        enriched, enriched_error = _run_model("ACTION_CT_V22.1", v221.run, root, now)
    finally:
        v220._extract_histories = original_loader

    errors = [error for error in (parent_error, enriched_error) if error is not None]
    payload = {
        "status": "SUCCESS_SHARED_RUNTIME" if not errors else "SHARED_RUNTIME_WITH_MODEL_ERRORS",
        "version": VERSION,
        "generated_at_utc": now.isoformat(),
        "same_time_anchor_for_v22_0_and_v22_1": True,
        "model_order_preserved": ["ACTION_CT_V22.0", "ACTION_CT_V22.1"],
        "outputs_and_pit_logic_owned_by_original_models": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "history_runtime": shared_loader.audit(),
        "models": {
            "ACTION_CT_V22.0": {
                "status": parent.get("status"),
                "rows": parent.get("rows"),
                "pit_validation_status": parent.get("pit_validation_status"),
            },
            "ACTION_CT_V22.1": {
                "status": enriched.get("status"),
                "rows": enriched.get("rows"),
                "pit_validation_status": enriched.get("pit_validation_status"),
            },
        },
        "errors": errors,
        "total_seconds": round(float(perf_counter() - started), 6),
    }
    audit_path = auditdir / "ACTION_CT_SHARED_HISTORY_RUNTIME_V21_13_10.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if errors:
        raise RuntimeError(
            "Action CT shared-history bundle completed with model error(s): "
            + ", ".join(str(error.get("model")) for error in errors)
        )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
