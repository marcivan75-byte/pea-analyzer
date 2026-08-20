from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.io.frames import apply_observations, is_missing, save_master
from v182.state.etf_structure_state import load_replay_observations, load_state_config

ROOT = Path(__file__).resolve().parents[3]


def _coverage(frame: pd.DataFrame, field: str) -> float:
    if field not in frame.columns or frame.empty:
        return 0.0
    return round(float((~frame[field].apply(is_missing)).mean() * 100.0), 2)


def run(root: Path = ROOT) -> dict:
    config = load_state_config(root / "config" / "ETF_STRUCTURE_STATE_V21_15.json")
    master_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not master_path.exists() or master_path.stat().st_size == 0:
        raise FileNotFoundError("ETF_STRUCTURE_STATE_REPLAY_REQUIRES_CURRENT_ENRICHED_MASTER")
    frame = pd.read_csv(master_path, sep=";", encoding="utf-8-sig", low_memory=False)
    if len(frame) != 102 or frame.get("isin", pd.Series(dtype=str)).nunique() != 102:
        raise RuntimeError(f"ETF_STRUCTURE_STATE_REPLAY_CANONICAL_UNIVERSE_REQUIRED:{len(frame)}")

    fields = tuple((config.get("fields") or {}).keys())
    before = {field: _coverage(frame, field) for field in fields}
    observations, state_diag = load_replay_observations(config, root=root)
    quarantined: list[dict] = []
    if observations:
        frame, quarantined = apply_observations(frame, observations)
        save_master(frame, master_path)
    after = {field: _coverage(frame, field) for field in fields}

    audit = {
        "version": config.get("version"),
        "status": "SUCCESS" if state_diag.get("status") in {"SUCCESS", "NO_STATE", "NO_ELIGIBLE_STATE_ROWS"} else "STATE_INVALID_FAIL_CLOSED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "canonical_universe_count": 102,
        "state": state_diag,
        "replay_observations": int(len(observations)),
        "merge_quarantined": int(len(quarantined)),
        "coverage_before_pct": before,
        "coverage_after_pct": after,
        "governance": config.get("governance", {}),
    }
    audit_path = root / str(config["audit_replay_path"])
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
    return audit


if __name__ == "__main__":
    run()
