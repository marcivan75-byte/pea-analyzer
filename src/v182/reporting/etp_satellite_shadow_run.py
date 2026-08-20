from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.features.etp_satellite_v1 import build_satellite_context, load_config, write_satellite_outputs

ROOT = Path(__file__).resolve().parents[3]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _read_json(path: Path) -> dict | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def run(root: Path = ROOT) -> dict:
    cfg = load_config(root / "config" / "ETP_SATELLITE_V1_SHADOW.json")
    external = _read_csv(root / cfg["inputs"]["external_universe"])
    if external.empty:
        raise RuntimeError("ETP_SATELLITE_EXTERNAL_UNIVERSE_EMPTY")
    flows = _read_csv(root / cfg["inputs"]["flow_instruments"])
    gold = _read_json(root / cfg["inputs"]["gold_decision_optional"])
    context, summary = build_satellite_context(external, flows, gold, cfg)
    summary.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "flow_input_available": not flows.empty,
            "gold_decision_input_available": gold is not None,
            "external_universe_rows": int(len(external)),
            "governance": cfg["governance"],
        }
    )
    paths = write_satellite_outputs(context, summary, root)
    summary["outputs"] = paths
    (root / paths["audit_json"]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
