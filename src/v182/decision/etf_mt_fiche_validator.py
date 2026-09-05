from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Any, Mapping


DRAFT = "DRAFT_AWAITING_THESIS"
READY = "READY_FOR_REVIEW"
INCOMPLETE = "INCOMPLETE"

MIN_THESIS = 40
MIN_INVALIDATION = 20
MIN_PEERS = 3
HORIZON_MIN = 18
HORIZON_MAX = 60


def _text(value: Any) -> str:
    return str(value or "").strip()


def _peers(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _text(value)
    if not text:
        return []
    parts = re.split(r"[\n;,|/]+", text)
    return [part.strip() for part in parts if part.strip()]


def _horizon_months(value: Any) -> int | None:
    text = _text(value).lower().replace(",", ".")
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    if "an" in text or "year" in text:
        number *= 12.0
    months = int(round(number))
    return months


def missing_required_fields(fiche: Mapping[str, Any]) -> list[str]:
    todo = fiche.get("to_complete") or {}
    pre = fiche.get("prefilled") or {}
    missing: list[str] = []
    if len(_text(todo.get("thesis_8_12_lines"))) < MIN_THESIS:
        missing.append("THESIS_TOO_SHORT")
    if len(_text(todo.get("invalidation"))) < MIN_INVALIDATION:
        missing.append("INVALIDATION_TOO_SHORT")
    if len(_peers(todo.get("peers_rejected"))) < MIN_PEERS:
        missing.append("PEERS_BELOW_3")
    months = _horizon_months(todo.get("job_horizon"))
    if months is None:
        missing.append("HORIZON_MISSING")
    elif months < HORIZON_MIN or months > HORIZON_MAX:
        missing.append("HORIZON_OUT_OF_18_60")
    if _text(pre.get("gate_status")).upper() == "BLOCK":
        missing.append("GATE_BLOCK")
    decision = _text(pre.get("precision_decision")).upper()
    if decision and decision != "BUY_CANDIDATE":
        missing.append("NOT_PRECISION_BUY_CANDIDATE")
    return missing


def validate_fiche(fiche: Mapping[str, Any]) -> dict:
    """Upgrade DRAFT to READY_FOR_REVIEW only. Never enables live orders."""
    out = json.loads(json.dumps(fiche))
    missing = missing_required_fields(out)
    out["validation_missing"] = missing
    out["live_orders_enabled"] = False
    out["promotion_allowed"] = False
    out["decision_influence"] = 0.0
    if missing:
        if not _text((out.get("to_complete") or {}).get("thesis_8_12_lines")) and not _text((out.get("to_complete") or {}).get("invalidation")):
            out["status"] = DRAFT
        else:
            out["status"] = INCOMPLETE
    else:
        out["status"] = READY
    return out


def merge_human_fields(generated: Mapping[str, Any], existing: Mapping[str, Any] | None) -> dict:
    """Keep operator-written thesis fields across daily reruns."""
    merged = json.loads(json.dumps(generated))
    if not existing:
        return validate_fiche(merged)
    existing_todo = existing.get("to_complete") or {}
    todo = merged.setdefault("to_complete", {})
    for key in (
        "job_horizon",
        "thesis_8_12_lines",
        "invalidation",
        "peers_rejected",
        "lookthrough_top10",
        "overlap",
        "replication",
        "review_date",
    ):
        previous = existing_todo.get(key)
        if _text(previous) and not _text(todo.get(key)):
            todo[key] = previous
    if existing_todo.get("sizing_pct") and not todo.get("sizing_pct"):
        todo["sizing_pct"] = existing_todo.get("sizing_pct")
    return validate_fiche(merged)


def validate_fiche_file(path: str | Path) -> dict:
    path = Path(path)
    fiche = json.loads(path.read_text(encoding="utf-8"))
    validated = validate_fiche(fiche)
    path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    return validated


def validate_fiche_dir(directory: str | Path) -> dict:
    directory = Path(directory)
    files = sorted(directory.glob("*.json"))
    results = []
    counts = {DRAFT: 0, INCOMPLETE: 0, READY: 0}
    for file_path in files:
        validated = validate_fiche_file(file_path)
        counts[validated["status"]] = counts.get(validated["status"], 0) + 1
        results.append(
            {
                "path": str(file_path),
                "isin": (validated.get("prefilled") or {}).get("isin"),
                "status": validated["status"],
                "missing": validated.get("validation_missing", []),
            }
        )
    return {
        "files": len(files),
        "status_counts": counts,
        "ready_for_review": counts.get(READY, 0),
        "results": results,
        "live_orders_enabled": False,
        "score_influence": 0.0,
    }
