from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import argparse
import json

import pandas as pd

from v182.reporting.ci_entry_watch_v22_2_1 import govern_existing_frame


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("outputs/committee_master/CI_ENTRY_WATCH_V22_2_1.csv")
AUDIT = Path("outputs/audit/WEEKLY_V4_FROZEN_UPSTREAM.json")


def run(source: Path, root: Path = ROOT, *, maximum_rows: int = 40) -> dict:
    if not source.exists() or source.stat().st_size == 0:
        raise FileNotFoundError(source)
    frame = pd.read_csv(source, sep=";", encoding="utf-8-sig", low_memory=False)
    if frame.empty or "isin" not in frame:
        raise ValueError("FROZEN_V22_2_FRAME_REQUIRED")
    if len(frame) > maximum_rows or frame["isin"].astype(str).nunique() > maximum_rows:
        raise ValueError("FROZEN_UPSTREAM_NOT_BOUNDED")
    input_isins = frame["isin"].astype(str).tolist()
    governed = govern_existing_frame(frame, root)
    if governed["isin"].astype(str).tolist() != input_isins or len(governed) != len(frame):
        raise RuntimeError("V22_2_1_OVERLAY_CHANGED_CANDIDATE_SET")
    target = root / OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    governed.to_csv(target, sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "PASS",
        "version": "WEEKLY_V4_FROZEN_UPSTREAM_1",
        "input": str(source),
        "input_sha256": sha256(source.read_bytes()).hexdigest(),
        "rows": int(len(governed)),
        "unique_isins": int(governed["isin"].astype(str).nunique()),
        "candidate_set_changed": False,
        "selection_score_changed": False,
        "real_orders_enabled": False,
        "output": OUTPUT.as_posix(),
    }
    audit = root / AUDIT
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    result = run(args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
