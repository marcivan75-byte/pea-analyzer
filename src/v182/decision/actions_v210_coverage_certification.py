from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "outputs/V21.0_ACTIONS_PEA_1829_PREPARED.csv"
CONFIG = ROOT / "data/reference/V21.0_ACTIONS_PEA_CONFIG.json"
AUDIT = ROOT / "outputs/audit/V21.0_ACTIONS_COVERAGE_CERTIFICATION.json"
SUMMARY = ROOT / "outputs/V21.0_ACTIONS_COVERAGE_CERTIFICATION.csv"

THRESHOLDS = {
    "fundamental_adequate_rows_pct": 85.0,
    "valuation_adequate_rows_pct": 85.0,
    "prospective_adequate_rows_pct": 80.0,
    "analyst_process_checked_rows_pct": 85.0,
    "decision_ready_rows_pct": 80.0,
    "weighted_coverage_mt_pct": 75.0,
    "weighted_coverage_lt_pct": 75.0,
}

FUNDAMENTAL_FIELDS = [
    "roe_v21_pct", "roa_v21_pct", "operating_margin_v21_pct", "net_margin_v21_pct",
    "revenue_growth_v21_pct", "earnings_growth_v21_pct", "debt_to_ebitda_v21",
    "debt_to_equity_v21", "current_ratio_v21", "fcf_yield_v21",
]
VALUATION_FIELDS = ["per_forward_v21", "pb_v21", "fcf_yield_v21"]
PROSPECTIVE_FIELDS = [
    "target_mean_v21", "consensus_score_100_v21", "consensus_delta_4w", "next_earnings_date",
    "eps_estimate_current_y_v21", "eps_estimate_next_y_v21", "revenue_estimate_current_y_v21",
    "revenue_estimate_next_y_v21", "estimate_revision_score_v21",
]


def _observed(df: pd.DataFrame, field: str) -> pd.Series:
    if field not in df.columns:
        return pd.Series(False, index=df.index)
    if field == "next_earnings_date":
        s = df[field].astype("string")
        return s.notna() & s.str.strip().fillna("").ne("") & ~s.str.lower().isin({"nan", "none", "n/a", "<na>"})
    return pd.to_numeric(df[field], errors="coerce").notna()


def _row_fraction(df: pd.DataFrame, fields: list[str]) -> tuple[pd.Series, pd.Series]:
    matrix = pd.concat([_observed(df, f).rename(f) for f in fields], axis=1)
    count = matrix.sum(axis=1)
    return count / float(len(fields)), count


def _weighted_coverage(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    out = pd.Series(0.0, index=df.index)
    for field, weight in weights.items():
        if field == "distribution_policy":
            obs = df.get(field, pd.Series(index=df.index, dtype=object)).astype(str).str.upper().isin({"DIST", "ACC", "ACC_OR_DIST"})
        elif field in {"positive_reversal_flag", "stoch_bull_cross_flag", "stoch_bear_cross_flag", "breakout_20d_flag"}:
            s = df.get(field, pd.Series(index=df.index, dtype=object)).astype("string")
            obs = s.notna() & s.str.strip().fillna("").ne("") & ~s.str.lower().isin({"nan", "none", "<na>"})
        else:
            obs = _observed(df, field)
        out += obs.astype(float) * float(weight)
    return out


def main() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected = int(cfg["canonical_universe_size"])
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != expected or df["isin"].astype(str).nunique() != expected:
        raise RuntimeError(f"Coverage certification requires canonical {expected} Actions universe")

    fund_frac, fund_count = _row_fraction(df, FUNDAMENTAL_FIELDS)
    val_frac, val_count = _row_fraction(df, VALUATION_FIELDS)
    pro_frac, pro_count = _row_fraction(df, PROSPECTIVE_FIELDS)

    analyst_status = df.get("analyst_coverage_status_v21", pd.Series(index=df.index, dtype=object)).astype("string").fillna("")
    analyst_observed = _observed(df, "consensus_score_100_v21")
    analyst_no_coverage_confirmed = analyst_status.str.startswith("NO_ANALYST_COVERAGE_CONFIRMED")
    analyst_checked = analyst_observed | analyst_no_coverage_confirmed

    fundamental_adequate = fund_count.ge(7)
    valuation_adequate = val_count.ge(2)
    prospective_adequate = pro_count.ge(4)

    mt_cov = _weighted_coverage(df, cfg["horizon_weights"]["MT"])
    lt_cov = _weighted_coverage(df, cfg["horizon_weights"]["LT"])
    decision_ready = fundamental_adequate & valuation_adequate & prospective_adequate & analyst_checked & mt_cov.ge(0.70) & lt_cov.ge(0.70)

    df["coverage_fundamental_core_pct_v21"] = (fund_frac * 100.0).round(2)
    df["coverage_valuation_pct_v21"] = (val_frac * 100.0).round(2)
    df["coverage_prospective_pct_v21"] = (pro_frac * 100.0).round(2)
    df["coverage_analyst_process_checked_v21"] = analyst_checked
    df["coverage_analyst_no_coverage_confirmed_v21"] = analyst_no_coverage_confirmed
    df["coverage_decision_ready_v21"] = decision_ready
    df["coverage_grade_v21"] = np.select(
        [decision_ready, fundamental_adequate & valuation_adequate & analyst_checked, fundamental_adequate | valuation_adequate],
        ["A_DECISION_READY", "B_PARTIAL_FORWARD", "C_PARTIAL"],
        default="D_INSUFFICIENT",
    )

    metrics = {
        "fundamental_adequate_rows_pct": round(float(fundamental_adequate.mean() * 100.0), 2),
        "valuation_adequate_rows_pct": round(float(valuation_adequate.mean() * 100.0), 2),
        "prospective_adequate_rows_pct": round(float(prospective_adequate.mean() * 100.0), 2),
        "analyst_process_checked_rows_pct": round(float(analyst_checked.mean() * 100.0), 2),
        "analyst_no_coverage_confirmed_rows_pct": round(float(analyst_no_coverage_confirmed.mean() * 100.0), 2),
        "decision_ready_rows_pct": round(float(decision_ready.mean() * 100.0), 2),
        "weighted_coverage_mt_pct": round(float(mt_cov.mean() * 100.0), 2),
        "weighted_coverage_lt_pct": round(float(lt_cov.mean() * 100.0), 2),
    }
    gates = {name: metrics[name] >= target for name, target in THRESHOLDS.items()}
    certified = all(gates.values())
    status = "VALIDATED" if certified else "NOT_VALIDATED"

    df["process_validation_status_v21"] = status
    df["process_validation_generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")

    field_coverage = {field: round(float(_observed(df, field).mean() * 100.0), 2) for field in sorted(set(FUNDAMENTAL_FIELDS + VALUATION_FIELDS + PROSPECTIVE_FIELDS))}
    rows = []
    for name, target in THRESHOLDS.items():
        rows.append({"metric": name, "actual_pct": metrics[name], "required_pct": target, "gate": "PASS" if gates[name] else "FAIL"})
    for field, actual in field_coverage.items():
        rows.append({"metric": f"field:{field}", "actual_pct": actual, "required_pct": "", "gate": "INFO"})
    pd.DataFrame(rows).to_csv(SUMMARY, sep=";", index=False, encoding="utf-8-sig")

    audit = {
        "passed": True, "certified": certified, "process_validation_status": status,
        "version": cfg["version"], "rows": len(df), "expected_rows": expected,
        "thresholds_pct": THRESHOLDS, "metrics_pct": metrics, "gates": gates, "field_coverage_pct": field_coverage,
        "adequacy_contract": {
            "fundamentals": {"required_observed": 7, "total": len(FUNDAMENTAL_FIELDS), "fields": FUNDAMENTAL_FIELDS},
            "valuation": {"required_observed": 2, "total": len(VALUATION_FIELDS), "fields": VALUATION_FIELDS},
            "prospective": {"required_observed": 4, "total": len(PROSPECTIVE_FIELDS), "fields": PROSPECTIVE_FIELDS},
            "analyst_process": "consensus observed OR explicit source-confirmed absence of analyst coverage",
            "decision_ready": "all three adequacy blocks + analyst process checked + row MT/LT weighted coverage >=70%",
        },
        "no_neutral_imputation": True, "research_outputs_allowed_when_not_validated": True,
        "validation_claim_forbidden_when_not_validated": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V21_ACTIONS_COVERAGE_CERTIFICATION_1829", json.dumps({"status": status, **metrics}, ensure_ascii=False))


if __name__ == "__main__":
    main()
