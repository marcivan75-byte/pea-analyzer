"""Politique d'append OHLCV — ne change ni scores ni univers."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os


DEFAULT_INCREMENTAL_PERIOD = "10d"
MANIFESTS = (
    Path("data/cache/actions/history_manifest.json"),
    Path("data/cache/etf/history_manifest.json"),
)


def apply_weekly_incremental_env() -> dict:
    """Resserre le refresh yfinance sans forcer un rebuild full."""
    os.environ.setdefault("PEA_YF_INCREMENTAL_PERIOD", DEFAULT_INCREMENTAL_PERIOD)
    os.environ.setdefault("PEA_YF_FORCE_FULL_HISTORY", "0")
    os.environ.setdefault("PEA_YF_FORCE_REFRESH", "0")
    return {
        "incremental_period": os.environ.get("PEA_YF_INCREMENTAL_PERIOD", DEFAULT_INCREMENTAL_PERIOD),
        "force_full_history": os.environ.get("PEA_YF_FORCE_FULL_HISTORY", "0"),
        "force_refresh": os.environ.get("PEA_YF_FORCE_REFRESH", "0"),
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
    }


def inspect_manifests(root: Path) -> dict:
    rows = []
    for relative in MANIFESTS:
        path = root / relative
        payload = {}
        if path.exists() and path.stat().st_size:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {"error": "INVALID_JSON"}
        rows.append(
            {
                "path": str(relative),
                "exists": path.exists(),
                "mode": payload.get("mode"),
                "updated_at_utc": payload.get("updated_at_utc"),
                "requested": payload.get("requested"),
                "cached": len(payload.get("cached_tickers") or []),
                "failed": len(payload.get("failed") or []),
                "incremental_period": payload.get("incremental_period"),
            }
        )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": apply_weekly_incremental_env(),
        "manifests": rows,
        "same_day_skip_supported": True,
        "full_rebuild_default": False,
    }


def write_audit(root: Path) -> dict:
    payload = inspect_manifests(root)
    path = root / "outputs/audit/OHLCV_INCREMENTAL_POLICY.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["audit"] = str(path.relative_to(root))
    return payload
