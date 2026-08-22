from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.decision.committee_master import decisions_from_scores, load_registry, tct_adapter
from v182.decision.tct_baseline_v24_1_8 import build_tct_baseline, NORMALIZATION_POLICY
from v182.decision.tct_timing_exact_v24_1_7 import build_exact_timing_snapshot
from v182.decision.tct_v24_1_7 import load_tct_config
from v182.risk.entry_exit_governance_v21_8 import (
    STATE_RELATIVE_PATH,
    _attach_temporal_state,
    _load_temporal_state,
    _persist_temporal_state,
    apply_governance,
)

ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_TCT_CT_V1"


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"DAILY_TACTICAL_INPUT_MISSING:{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _android_summary(governed: pd.DataFrame, generated_at: str) -> str:
    rows = governed.copy()
    rows["_score"] = pd.to_numeric(rows.get("score"), errors="coerce")
    priority = rows[rows["decision"].astype(str).isin(["BUY_CANDIDATE", "WATCH", "REVIEW"])].copy()
    priority = priority.sort_values(["horizon", "_score"], ascending=[True, False])
    lines = [
        "# Comité tactique quotidien — TCT / CT",
        "",
        f"Généré UTC : {generated_at}",
        "Périmètre : ACTION TCT + ACTION CT + ETF CT. L'horizon MT et les modules lourds restent hebdomadaires.",
        "Aucun ordre réel. V21.8 est une aide à la décision uniquement.",
        "",
    ]
    if priority.empty:
        lines.append("Aucune priorité BUY/WATCH/REVIEW sur ce run.")
        return "\n".join(lines) + "\n"
    for horizon in ("TCT", "CT"):
        subset = priority[priority["horizon"].astype(str).str.upper() == horizon].head(12)
        if subset.empty:
            continue
        lines.extend([f"## {horizon}", ""])
        for _, row in subset.iterrows():
            name = str(row.get("name") or row.get("isin") or "N/A")
            asset = str(row.get("asset_class") or "")
            score = row.get("_score")
            score_txt = "N/A" if pd.isna(score) else f"{float(score):.1f}"
            decision = str(row.get("decision") or "")
            entry = str(row.get("v21_8_entry_state") or "")
            position = str(row.get("v21_8_position_state") or "")
            reasons = str(row.get("v21_8_entry_reasons") or row.get("v21_8_position_reasons") or "")
            lines.append(f"- **{asset} {name}** — score {score_txt} — {decision} — entrée {entry} — position {position}")
            if reasons:
                lines.append(f"  - V21.8 : {reasons}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT) -> dict:
    outputs = root / "outputs"
    outdir = outputs / "daily_tct_ct"
    mobile = outputs / "mobile"
    auditdir = outputs / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)

    actions = _read(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etfs = _read(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_ref = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    etf_ref = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    tct_cfg = load_tct_config(root / "config" / "TCT_V24_1_7_SHADOW.json")
    v21_cfg = json.loads((root / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))

    parts: list[pd.DataFrame] = []
    parts.append(decisions_from_scores(actions, action_ref, "ACTION", ["CT"]))
    parts.append(decisions_from_scores(etfs, etf_ref, "ETF", ["CT"]))

    actions_with_tct, baseline = build_tct_baseline(actions, tct_cfg)
    actions_with_tct.to_csv(outdir / "TCT_BASELINE_V24_1_8.csv", sep=";", index=False, encoding="utf-8-sig")
    tct_state_path = root / str(tct_cfg.get("state", {}).get("path", "state/TCT_V24_1_7_T1_STATE.json"))
    tct_shadow, exact = build_exact_timing_snapshot(
        actions_with_tct,
        root / "data" / "cache" / "actions",
        tct_state_path,
        tct_cfg,
    )
    tct_shadow.to_csv(outdir / "TCT_SHADOW_V24_1_7.csv", sep=";", index=False, encoding="utf-8-sig")
    parts.append(tct_adapter(tct_shadow))

    decisions = pd.concat([p for p in parts if p is not None and not p.empty], ignore_index=True, sort=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    decisions["generated_at_utc"] = generated_at
    decisions["live_orders_enabled"] = False
    decisions["daily_tactical_scope"] = True
    decisions.to_csv(outdir / "DAILY_TCT_CT_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")

    state_path = root / STATE_RELATIVE_PATH
    previous = _load_temporal_state(state_path)
    with_state = _attach_temporal_state(decisions, previous)
    governed = apply_governance(with_state, v21_cfg)
    state_rows = _persist_temporal_state(governed, state_path)
    governed.to_csv(outdir / "DAILY_TCT_CT_V21_8.csv", sep=";", index=False, encoding="utf-8-sig")

    android_path = mobile / "ANDROID_DAILY_TCT_CT.md"
    android_path.write_text(_android_summary(governed, generated_at), encoding="utf-8")

    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "scope": ["ACTION_TCT", "ACTION_CT", "ETF_CT"],
        "rows": int(len(governed)),
        "rows_by_asset_horizon": governed.groupby(["asset_class", "horizon"], dropna=False).size().reset_index(name="count").to_dict("records"),
        "tct_baseline": {
            "universe_rows": baseline.universe_rows,
            "ranked_rows": baseline.ranked_rows,
            "top20_rows": baseline.top20_rows,
            "normalization_policy": NORMALIZATION_POLICY,
        },
        "tct_exact": {
            "histories_found": exact.histories_found,
            "histories_usable": exact.histories_usable,
            "t1_detected_raw": exact.t1_detected_raw,
            "t2_confirmed": exact.t2_confirmed,
        },
        "entry_exit_v21_8": {
            "position_states": governed["v21_8_position_state"].value_counts(dropna=False).to_dict(),
            "entry_states": governed["v21_8_entry_state"].value_counts(dropna=False).to_dict(),
            "temporal_state_rows": state_rows,
        },
        "weights_unchanged": True,
        "selection_thresholds_unchanged": True,
        "holdout_opened": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "fixed_take_profit_enabled": False,
        "legacy_fixed_stop_enabled": False,
        "real_orders_enabled": False,
        "heavy_modules_executed": [],
        "outputs": {
            "decisions": "outputs/daily_tct_ct/DAILY_TCT_CT_DECISIONS.csv",
            "entry_exit": "outputs/daily_tct_ct/DAILY_TCT_CT_V21_8.csv",
            "android": "outputs/mobile/ANDROID_DAILY_TCT_CT.md",
        },
    }
    (auditdir / "DAILY_TCT_CT_AUDIT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
