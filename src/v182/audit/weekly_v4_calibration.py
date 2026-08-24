from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/WEEKLY_V4_GOVERNANCE.json")
OUTPUT = Path("outputs/audit/WEEKLY_V4_CALIBRATION_AUDIT.json")


def _json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"OBJECT_REQUIRED:{path.as_posix()}")
    return payload


def _metrics(values: list[float]) -> dict:
    hhi = sum(value * value for value in values)
    ordered = sorted(values, reverse=True)
    return {
        "criteria": len(values),
        "sum": sum(values),
        "maximum": max(values, default=0.0),
        "top_three_share": sum(ordered[:3]),
        "hhi": hhi,
        "effective_criteria": (1.0 / hhi) if hhi else 0.0,
    }


def _weight_metrics(root: Path, cfg: dict) -> dict[str, dict]:
    refs = cfg["referentials"]
    actions = _json(root / refs["actions_registry"])
    etfs = _json(root / refs["etf_registry"])
    result: dict[str, dict] = {}
    for prefix, registry, groups in (
        ("ACTION", actions, refs["actions_weight_groups"]),
        ("ETF", etfs, refs["etf_weight_groups"]),
    ):
        for group in groups:
            vector = registry["weights"][group]
            result[f"{prefix}.{group}"] = _metrics([float(value) for value in vector.values()])
    dynamic = etfs[refs["etf_dynamic_mt_weight_group"]]
    result["ETF.MT_DYNAMIC_38"] = _metrics([float(value) for value in dynamic.values()])
    return result


def _sensitivity(path: Path | None, cfg: dict) -> dict:
    if path is None or not path.exists():
        return {"status": "NOT_AVAILABLE", "reason": "NO_FROZEN_CANDIDATE_FILE"}
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    score_source = frame["score"] if "score" in frame else pd.Series(float("nan"), index=frame.index)
    confidence_field = next(
        (field for field in ("CI_CONFIDENCE_SCORE_V22_2_1", "CI_CONFIDENCE_SCORE_0_100") if field in frame),
        None,
    )
    confidence_source = (
        frame[confidence_field] if confidence_field else pd.Series(float("nan"), index=frame.index)
    )
    score = pd.to_numeric(score_source, errors="coerce")
    confidence = pd.to_numeric(confidence_source, errors="coerce")
    score_base = float(cfg["selection"]["minimum_selection_score"])
    confidence_base = float(cfg["selection"]["minimum_confidence_score"])
    grid: list[dict] = []
    for score_delta in (-2.0, 0.0, 2.0):
        for confidence_delta in (-2.0, 0.0, 2.0):
            eligible = score.ge(score_base + score_delta) & confidence.ge(confidence_base + confidence_delta)
            grid.append(
                {
                    "score_threshold": score_base + score_delta,
                    "confidence_threshold": confidence_base + confidence_delta,
                    "base_eligible": int(eligible.sum()),
                }
            )
    return {
        "status": "DESCRIPTIVE_ONLY_NO_THRESHOLD_OPTIMIZATION",
        "rows": int(len(frame)),
        "numeric_score_rows": int(score.notna().sum()),
        "numeric_confidence_rows": int(confidence.notna().sum()),
        "confidence_field": confidence_field,
        "grid": grid,
    }


def run(root: Path = ROOT, *, candidate_path: Path | None = None, write: bool = True) -> dict:
    cfg = _json(root / CONFIG)
    weight_metrics = _weight_metrics(root, cfg)
    tolerance = float(cfg["referentials"]["weight_sum_tolerance"])
    checks = {
        "all_weight_vectors_normalized": all(abs(item["sum"] - 1.0) <= tolerance for item in weight_metrics.values()),
        "all_weight_metrics_finite": all(
            all(math.isfinite(float(value)) for value in item.values()) for item in weight_metrics.values()
        ),
        "reference_vectors_remain_active": bool(cfg["weight_policy"]["reference_vectors_remain_active"]),
        "automatic_reweighting_forbidden": bool(cfg["weight_policy"]["automatic_reweighting_forbidden"]),
        "pit_oos_required_for_promotion": bool(
            cfg["weight_policy"]["promotion_requires_point_in_time_out_of_sample_evidence"]
        ),
        "sources_have_zero_score_influence": not bool(cfg["weight_policy"]["v4_reweights_reference_scores"]),
    }
    payload = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "version": "WEEKLY_V4_CALIBRATION_AUDIT_1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "weight_concentration": weight_metrics,
        "threshold_sensitivity": _sensitivity(candidate_path, cfg),
        "decision": "KEEP_REFERENCE_WEIGHTS_AND_THRESHOLDS",
        "rationale": "No reproducible point-in-time out-of-sample outcome set is present; the sensitivity grid is descriptive and cannot justify optimization.",
    }
    if write:
        target = root / OUTPUT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = run(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 2)
