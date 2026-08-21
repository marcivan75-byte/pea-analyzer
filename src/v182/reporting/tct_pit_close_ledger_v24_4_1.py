from __future__ import annotations

from pathlib import Path
import json

from v182.reporting import tct_pit_close_ledger as base


ROOT = Path(__file__).resolve().parents[3]
CONFIG_V244 = "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json"
VERSION = "TCT_V24.4.1_PIT_DAILY_CLOSE_LEDGER_V1"

base.CONFIG_V244 = CONFIG_V244
base.VERSION = VERSION


def run(root: Path = ROOT) -> dict:
    payload = base.run(root=root)
    old_audit = root / "outputs" / "audit" / "TCT_V24_4_0_PIT_CLOSE_LEDGER_AUDIT.json"
    new_audit = root / "outputs" / "audit" / "TCT_V24_4_1_PIT_CLOSE_LEDGER_AUDIT.json"
    new_audit.parent.mkdir(parents=True, exist_ok=True)
    new_audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if old_audit.exists():
        old_audit.unlink()
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
