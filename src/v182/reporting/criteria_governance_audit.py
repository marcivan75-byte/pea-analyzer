from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.audit import ohlcv_history_depth
from v182.decision.committee_master import criterion_coverage_report, load_registry
from v182.reporting import calibration_governance_audit

ROOT = Path(__file__).resolve().parents[3]
HORIZONS = ("CT", "MT", "LT", "SHORT", "TOP_DOWN")


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _weights(registry: dict) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for horizon, values in registry.get("weights", {}).items():
        for criterion, weight in values.items():
            value = float(weight or 0.0)
            if value > 0:
                out[(str(horizon), str(criterion))] = value
    return out


def _coverage(master: pd.DataFrame, registry: dict, asset: str) -> pd.DataFrame:
    if master.empty:
        rows = []
        for (horizon, criterion), weight in _weights(registry).items():
            rows.append({
                "asset_class": asset,
                "horizon": horizon,
                "criterion": criterion,
                "weight": weight,
                "direction": registry.get("directions", {}).get(horizon, {}).get(criterion, "HIGH"),
                "resolution": "MISSING_MASTER",
                "available_rows": 0,
                "universe_rows": 0,
                "availability_pct": 0.0,
                "criterion_status": "MISSING",
            })
        return pd.DataFrame(rows)
    return criterion_coverage_report(master, registry, asset, HORIZONS)


def _governance_rows(
    master: pd.DataFrame,
    reference: dict,
    challenger: dict | None,
    asset: str,
) -> pd.DataFrame:
    reference_cov = _coverage(master, reference, asset)
    reference_keys = set(_weights(reference))
    rows: list[dict] = []
    for record in reference_cov.to_dict("records"):
        available = float(record.get("availability_pct", 0.0) or 0.0) > 0
        rows.append({
            **record,
            "governance_status": "ACTIVE",
            "data_status": "AVAILABLE" if available else "MISSING",
            "effective_status": "ACTIVE" if available else "MISSING",
            "decision_influence": 1.0,
            "promotion_status": "REFERENCE_ACTIVE",
        })

    if challenger:
        challenger_cov = _coverage(master, challenger, asset)
        for record in challenger_cov.to_dict("records"):
            key = (str(record.get("horizon")), str(record.get("criterion")))
            if key in reference_keys:
                continue
            available = float(record.get("availability_pct", 0.0) or 0.0) > 0
            rows.append({
                **record,
                "governance_status": "SHADOW",
                "data_status": "AVAILABLE" if available else "MISSING",
                "effective_status": "SHADOW" if available else "MISSING",
                "decision_influence": 0.0,
                "promotion_status": "BLOCKED_UNTIL_PIT_OOS",
            })
    return pd.DataFrame(rows)


def _context_only_rows() -> pd.DataFrame:
    rows = [
        {
            "asset_class": "ACTION_ETF",
            "horizon": "ALL",
            "criterion": "RISK_V1_1_BETA_CORRELATION_CONTEXT",
            "weight": 0.0,
            "direction": "CONTEXT",
            "resolution": "outputs/risk/BETA_CORRELATION_RISK_ROWS.csv",
            "available_rows": None,
            "universe_rows": None,
            "availability_pct": None,
            "criterion_status": "CONTEXT_ONLY",
            "governance_status": "CONTEXT_ONLY",
            "data_status": "AVAILABLE_IF_PUBLISHED",
            "effective_status": "CONTEXT_ONLY",
            "decision_influence": 0.0,
            "promotion_status": "CONTEXT_ONLY",
        },
        {
            "asset_class": "ACTION_ETF",
            "horizon": "ALL",
            "criterion": "SECTOR_THEME_ROTATION_V2_CONTEXT",
            "weight": 0.0,
            "direction": "CONTEXT",
            "resolution": "outputs/committee_master/COMMITTEE_SECTOR_ROTATION_V2_CONTEXT.csv",
            "available_rows": None,
            "universe_rows": None,
            "availability_pct": None,
            "criterion_status": "CONTEXT_ONLY",
            "governance_status": "CONTEXT_ONLY",
            "data_status": "AVAILABLE_IF_PUBLISHED",
            "effective_status": "CONTEXT_ONLY",
            "decision_influence": 0.0,
            "promotion_status": "BLOCKED_UNTIL_PIT_OOS",
        },
    ]
    return pd.DataFrame(rows)


def run(root: Path = ROOT) -> dict:
    calibration = calibration_governance_audit.run(root)
    history_depth = ohlcv_history_depth.run(root)
    action_master = _read(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etf_master = _read(root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_reference = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    action_challenger = load_registry(root / "config" / "V21_ACTIONS_CRITERIA_REGISTRY.json")
    etf_reference = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")

    frames = [
        _governance_rows(action_master, action_reference, action_challenger, "ACTION"),
        _governance_rows(etf_master, etf_reference, None, "ETF"),
        _context_only_rows(),
    ]
    audit = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / "CRITERIA_GOVERNANCE_AUDIT.csv"
    audit.to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")

    counts = audit["effective_status"].value_counts(dropna=False).to_dict() if not audit.empty else {}
    payload = {
        "status": "SUCCESS",
        "version": "CRITERIA_GOVERNANCE_AUDIT_V1",
        "rows": int(len(audit)),
        "counts_by_effective_status": {str(k): int(v) for k, v in counts.items()},
        "calibration_governance": calibration,
        "ohlcv_history_depth": history_depth,
        "weight_or_threshold_changes": False,
        "holdout_unlocked": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "real_orders_enabled": False,
        "output": str(csv_path.relative_to(root)),
        "semantics": {
            "ACTIVE": "criterion belongs to the current production reference and has data",
            "SHADOW": "challenger-only criterion with zero decision authority pending PIT/OOS",
            "CONTEXT_ONLY": "diagnostic context with zero score/decision/sizing/stop authority",
            "MISSING": "governed criterion has no usable current data",
            "BLOCKED": "promotion or decision authority explicitly forbidden by governance",
        },
    }
    (outdir / "CRITERIA_GOVERNANCE_AUDIT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
