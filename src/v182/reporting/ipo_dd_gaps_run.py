from __future__ import annotations

import json
from pathlib import Path

from v182.decision.ipo_dd_gaps_v1 import write_gap_worklist

ROOT = Path(__file__).resolve().parents[3]


def run(root: Path = ROOT) -> dict:
    config = json.loads((root / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))
    return write_gap_worklist(root, config)


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
