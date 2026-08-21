from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.reporting import tct_v24_4_pit_validator as base
from v182.reporting.tct_v24_4_pit_validator_runtime import _spearman_without_scipy


ROOT = Path(__file__).resolve().parents[3]
VERSION = "TCT_V24.4.1_PIT_VALIDATOR"

base.VERSION = VERSION
base._spearman = _spearman_without_scipy


def run(root: Path = ROOT) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    gates = json.loads((root / "config" / "TCT_V24_4_1_VALIDATION_GATES.json").read_text(encoding="utf-8"))
    catalyst_cfg = json.loads((root / "config" / "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    ledger_path = root / catalyst_cfg["state"]["catalyst_ledger_path"]
    ledger = base._read_csv(ledger_path)

    # Separate validation epoch: V24.4.0 observations are never mixed into the
    # V24.4.1 evidence set after scoring semantics changed.
    if not ledger.empty and "version" in ledger.columns:
        ledger = ledger[ledger["version"].astype(str) == "TCT_V24.4.1_NEXT_SESSION_CATALYST_CYCLE_SHADOW"].copy()

    payload, slices, changes = base.validate_ledger(ledger, gates)
    payload["version"] = VERSION
    payload["validation_epoch"] = gates.get("validation_epoch")
    payload["generated_at_utc"] = generated_at
    payload["ledger_path"] = str(ledger_path.relative_to(root))
    payload["ledger_rows"] = int(len(ledger))

    auditdir = root / "outputs" / "audit"
    outdir = root / "outputs" / "daily_tct_ct"
    mobile = root / "outputs" / "mobile"
    auditdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)

    audit_path = auditdir / "TCT_V24_4_1_PIT_VALIDATION.json"
    slice_path = outdir / "TCT_V24_4_1_PIT_SLICES.csv"
    change_path = outdir / "TCT_V24_4_1_PREOPEN_POSTMARKET_CHANGES.csv"
    mobile_path = mobile / "ANDROID_TCT_V24_4_1_PIT_VALIDATION.md"

    base._write_csv(slices, slice_path)
    base._write_csv(changes, change_path)
    mobile_path.write_text(base._android(payload, generated_at).replace("TCT V24.4", "TCT V24.4.1"), encoding="utf-8")
    payload["outputs"] = {
        "audit": str(audit_path.relative_to(root)),
        "slices": str(slice_path.relative_to(root)),
        "preopen_postmarket_changes": str(change_path.relative_to(root)),
        "android": str(mobile_path.relative_to(root)),
    }
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
