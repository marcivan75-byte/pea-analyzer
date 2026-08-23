from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

import pandas as pd

from v182.reporting.daily_source_prewarm_v21_16 import persist_seed
from v182.reporting.daily_tct_ct_runner import _android_summary

ROOT = Path(__file__).resolve().parents[3]
VERSION = "FRIDAY_TACTICAL_REUSE_V21_16_2_PREWARM_SEED"


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_INPUT_MISSING:{path}")
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _scope(frame: pd.DataFrame) -> pd.DataFrame:
    horizon = frame.get("horizon", pd.Series("", index=frame.index)).astype(str).str.upper()
    asset = frame.get("asset_class", pd.Series("", index=frame.index)).astype(str).str.upper()
    keep = ((asset == "ACTION") & horizon.isin(["TCT", "CT"])) | ((asset == "ETF") & (horizon == "CT"))
    return frame.loc[keep].copy().reset_index(drop=True)


def _copy_if_present(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def run(root: Path = ROOT) -> dict:
    committee = root / "outputs" / "committee_master"
    outdir = root / "outputs" / "daily_tct_ct"
    mobile = root / "outputs" / "mobile"
    auditdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)

    decisions = _scope(_read(committee / "COMMITTEE_DECISIONS.csv"))
    governed = _scope(_read(committee / "V21_8_ENTRY_EXIT_CHALLENGER.csv"))
    if len(decisions) != len(governed):
        raise RuntimeError(f"FRIDAY_TACTICAL_REUSE_ROW_MISMATCH:{len(decisions)}:{len(governed)}")

    decision_keys = decisions[["asset_class", "horizon", "isin"]].astype(str).agg("|".join, axis=1)
    governed_keys = governed[["asset_class", "horizon", "isin"]].astype(str).agg("|".join, axis=1)
    if set(decision_keys) != set(governed_keys):
        raise RuntimeError("FRIDAY_TACTICAL_REUSE_KEY_MISMATCH")

    decisions.to_csv(outdir / "DAILY_TCT_CT_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    governed.to_csv(outdir / "DAILY_TCT_CT_V21_8.csv", sep=";", index=False, encoding="utf-8-sig")
    copied_baseline = _copy_if_present(committee / "TCT_BASELINE_V24_1_8.csv", outdir / "TCT_BASELINE_V24_1_8.csv")
    copied_shadow = _copy_if_present(committee / "TCT_SHADOW_V24_1_7.csv", outdir / "TCT_SHADOW_V24_1_7.csv")
    prewarm_seed = persist_seed(governed, root)

    generated_at = datetime.now(timezone.utc).isoformat()
    (mobile / "ANDROID_DAILY_TCT_CT.md").write_text(_android_summary(governed, generated_at), encoding="utf-8")

    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "rows": int(len(governed)),
        "scope": ["ACTION_TCT", "ACTION_CT", "ETF_CT"],
        "source": "CURRENT_WEEKLY_COMMITTEE_OUTPUTS",
        "reused_committee_decisions": True,
        "reused_v21_8_governance": True,
        "reused_tct_baseline": copied_baseline,
        "reused_tct_exact_shadow": copied_shadow,
        "next_daily_source_prewarm_seed": prewarm_seed,
        "network_calls": 0,
        "rescoring_calls": 0,
        "source_gate_calls": 0,
        "entry_exit_recompute_calls": 0,
        "decision_logic_changed": False,
        "score_logic_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "real_orders_enabled": False,
    }
    (auditdir / "FRIDAY_TACTICAL_REUSE_V21_16.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
