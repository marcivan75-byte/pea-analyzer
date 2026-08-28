from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/OBJECTIVES_RISK_CHALLENGER_V2.json")
OR_INPUT = Path("outputs/committee_master/OBJECTIVES_RISK_CHALLENGER_V2.csv")
SECTOR_INPUT = Path("outputs/committee_master/SECTOR_RANKING.csv")
CHALLENGER_INPUT = Path("outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_4.csv")
ROTATION_INPUT = Path("outputs/sector_rotation/V2_SECTOR_ROTATION_SHADOW.csv")
AUDIT = Path("outputs/audit/SECTOR_OR_SHADOW_V1.json")


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except Exception:
        return pd.DataFrame()


def _key(series: pd.Series) -> pd.Series:
    return series.fillna("MISSING").astype(str).str.upper().str.strip()


def _coverage_multiplier(coverage: pd.Series, cfg: dict) -> pd.Series:
    multipliers = cfg["coverage_multipliers"]
    return pd.Series(
        np.select(
            [coverage.ge(75), coverage.ge(70), coverage.ge(60)],
            [multipliers["FULL_75_PLUS"], multipliers["AMBER_70_75"], multipliers["ORANGE_60_70"]],
            default=multipliers["RED_BELOW_60"],
        ),
        index=coverage.index,
    )


def _instrument_detail(root: Path, cfg: dict) -> pd.DataFrame:
    ranking = _read(root / OR_INPUT)
    sectors = _read(root / SECTOR_INPUT)
    challenger = _read(root / CHALLENGER_INPUT)
    if ranking.empty or sectors.empty:
        return pd.DataFrame()
    sector_fields = [field for field in ("isin", "sector", "horizon", "coverage_pct", "decision") if field in sectors]
    detail = ranking.merge(sectors[sector_fields].drop_duplicates("isin"), on="isin", how="left", suffixes=("", "_sector"))
    if not challenger.empty and "isin" in challenger:
        fields = [field for field in ("isin", "score", "coverage_pct", "decision") if field in challenger]
        challenge = challenger[fields].drop_duplicates("isin").rename(columns={
            "score": "SECTOR_CHALLENGER_SCORE", "coverage_pct": "SECTOR_CHALLENGER_COVERAGE_PCT",
            "decision": "SECTOR_CHALLENGER_DECISION",
        })
        detail = detail.merge(challenge, on="isin", how="left")
    horizon = _key(detail.get("horizon", detail.get("SIM_HORIZON", pd.Series("MISSING", index=detail.index))))
    detail = detail[~horizon.isin(set(cfg["excluded_horizons"]))].copy()
    coverage = pd.to_numeric(detail.get("coverage_pct", pd.Series(np.nan, index=detail.index)), errors="coerce")
    detail["SECTOR_OR_COVERAGE_MULT"] = _coverage_multiplier(coverage, cfg)
    base_score = pd.to_numeric(detail.get("OR_COMPOSITE_SHADOW", pd.Series(np.nan, index=detail.index)), errors="coerce")
    detail["SECTOR_OR_INSTRUMENT_SCORE"] = (base_score * detail["SECTOR_OR_COVERAGE_MULT"]).round(2)
    challenger_decision = _key(detail.get("SECTOR_CHALLENGER_DECISION", pd.Series("MISSING", index=detail.index)))
    baseline_decision = _key(detail.get("decision", pd.Series("MISSING", index=detail.index)))
    sector_missing = _key(detail.get("sector", pd.Series("MISSING", index=detail.index))).eq("MISSING")
    blocked = (
        challenger_decision.eq("BLOCK_DATA")
        | baseline_decision.eq("BLOCK_DATA")
        | coverage.lt(60)
        | coverage.isna()
        | sector_missing
    )
    detail["SECTOR_OR_ELIGIBILITY"] = np.where(blocked, "AUDIT_ONLY_FAIL_CLOSED", "ELIGIBLE_SHADOW")
    detail["SECTOR_OR_GATE_REASON"] = np.select(
        [sector_missing, challenger_decision.eq("BLOCK_DATA"), baseline_decision.eq("BLOCK_DATA"), coverage.lt(60), coverage.isna()],
        ["SECTOR_MISSING", "CHALLENGER_BLOCK_DATA", "BASELINE_BLOCK_DATA", "COVERAGE_BELOW_60", "COVERAGE_MISSING"],
        default="NONE",
    )
    detail["SECTOR_OR_CAN_REOPEN_BLOCK_DATA"] = False
    detail["SECTOR_OR_SHORT_EXCLUDED"] = False
    rotation = _read(root / ROTATION_INPUT)
    detail["SECTOR_ROTATION_NEW_POSITION_ACTION"] = "MISSING"
    detail["SECTOR_ROTATION_CORRECTION_ALERT"] = False
    if not rotation.empty and "sector" in rotation:
        context = rotation.copy()
        context["_sector_key"] = _key(context["sector"])
        fields = ["_sector_key"] + [
            field for field in ("new_position_action", "correction_alert", "RARS", "DQS", "AVCR") if field in context
        ]
        context = context[fields].drop_duplicates("_sector_key")
        detail["_sector_key"] = _key(detail.get("sector", pd.Series("MISSING", index=detail.index)))
        detail = detail.merge(context, on="_sector_key", how="left")
        detail["SECTOR_ROTATION_NEW_POSITION_ACTION"] = _key(
            detail.get("new_position_action", pd.Series("MISSING", index=detail.index))
        )
        detail["SECTOR_ROTATION_CORRECTION_ALERT"] = detail.get(
            "correction_alert", pd.Series(False, index=detail.index)
        ).fillna(False).astype(bool)
    no_chase = detail["SECTOR_ROTATION_NEW_POSITION_ACTION"].str.contains("NO_CHASE", regex=False)
    correction = detail["SECTOR_ROTATION_CORRECTION_ALERT"]
    detail["SECTOR_OR_COMMITTEE_CONFLICT_CAP_SHADOW"] = np.where(
        no_chase | correction, "ATTENDRE_REPLI_SHADOW", "NONE"
    )
    detail["SECTOR_OR_CONFLICT_REASON"] = np.select(
        [correction, no_chase], ["CORRECTION_ALERT", "NO_CHASE"], default="NONE"
    )
    detail["SECTOR_OR_CONFLICT_DECISION_INFLUENCE"] = 0.0
    return detail.sort_values("SECTOR_OR_INSTRUMENT_SCORE", ascending=False, na_position="last")


def _aggregate(detail: pd.DataFrame, rotation: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    eligible = detail[detail["SECTOR_OR_ELIGIBILITY"].eq("ELIGIBLE_SHADOW")].copy()
    if eligible.empty:
        return pd.DataFrame(columns=["sector", "SECTOR_OR_AGGREGATE_SCORE"])
    eligible["_sector_key"] = _key(eligible.get("sector", pd.Series("MISSING", index=eligible.index)))
    top_n = int(cfg["top_instruments_per_sector"])
    top = eligible.sort_values("SECTOR_OR_INSTRUMENT_SCORE", ascending=False).groupby("_sector_key", sort=False).head(top_n)
    risk = _key(top.get("OR_RISK_VERDICT", pd.Series("MISSING", index=top.index)))
    top["_risk_caution"] = risk.isin({"AMBER", "ORANGE"}).astype(float)
    aggregate = top.groupby("_sector_key", as_index=False).agg(
        sector=("sector", "first"),
        SECTOR_OR_TOP_COUNT=("isin", "count"),
        SECTOR_OR_RAW_SCORE=("SECTOR_OR_INSTRUMENT_SCORE", "mean"),
        SECTOR_OR_RISK_CAUTION_SHARE=("_risk_caution", "mean"),
    )
    caution = aggregate["SECTOR_OR_RISK_CAUTION_SHARE"].gt(float(cfg["risk_caution_share_threshold"]))
    aggregate["SECTOR_OR_RISK_MULT"] = np.where(caution, float(cfg["risk_caution_multiplier"]), 1.0)
    aggregate["SECTOR_ROTATION_ACTION"] = "MISSING"
    aggregate["SECTOR_ROTATION_RARS"] = np.nan
    if not rotation.empty and "sector" in rotation:
        rotation = rotation.copy()
        rotation["_sector_key"] = _key(rotation["sector"])
        action_field = "new_position_action" if "new_position_action" in rotation else "state"
        context = rotation[["_sector_key", action_field, "RARS"]].drop_duplicates("_sector_key").rename(
            columns={action_field: "SECTOR_ROTATION_ACTION", "RARS": "SECTOR_ROTATION_RARS"}
        )
        aggregate = aggregate.drop(columns=["SECTOR_ROTATION_ACTION", "SECTOR_ROTATION_RARS"]).merge(context, on="_sector_key", how="left")
    action = _key(aggregate.get("SECTOR_ROTATION_ACTION", pd.Series("MISSING", index=aggregate.index)))
    aggregate["SECTOR_ROTATION_MULT"] = action.map(cfg["rotation_action_multipliers"]).fillna(cfg["rotation_action_multipliers"]["MISSING"])
    aggregate["SECTOR_OR_AGGREGATE_SCORE"] = (
        aggregate["SECTOR_OR_RAW_SCORE"] * aggregate["SECTOR_OR_RISK_MULT"] * aggregate["SECTOR_ROTATION_MULT"]
    ).round(2)
    aggregate["SECTOR_OR_SCORE_INFLUENCE"] = 0.0
    aggregate["SECTOR_OR_SHADOW_ONLY"] = True
    return aggregate.sort_values("SECTOR_OR_AGGREGATE_SCORE", ascending=False, na_position="last")


def run(root: Path = ROOT) -> dict:
    try:
        config = json.loads((root / CONFIG).read_text(encoding="utf-8"))["sector_or_shadow"]
        detail = _instrument_detail(root, config)
        aggregate = _aggregate(detail, _read(root / ROTATION_INPUT), config)
        date = datetime.now(timezone.utc).date().isoformat()
        detail_path = root / f"outputs/committee_master/SECTOR_OR_RANKING_SHADOW_{date}.csv"
        aggregate_path = root / f"outputs/committee_master/SECTOR_OR_AGGREGATE_{date}.csv"
        for path in (detail_path, aggregate_path, root / AUDIT):
            path.parent.mkdir(parents=True, exist_ok=True)
        detail.to_csv(detail_path, sep=";", index=False, encoding="utf-8-sig")
        aggregate.to_csv(aggregate_path, sep=";", index=False, encoding="utf-8-sig")
        payload = {
            "status": "SUCCESS", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "instrument_rows": len(detail), "eligible_rows": int(detail.get("SECTOR_OR_ELIGIBILITY", pd.Series(dtype=str)).eq("ELIGIBLE_SHADOW").sum()),
            "audit_only_rows": int(detail.get("SECTOR_OR_ELIGIBILITY", pd.Series(dtype=str)).eq("AUDIT_ONLY_FAIL_CLOSED").sum()),
            "sector_rows": len(aggregate), "short_excluded": True, "block_data_reopening_allowed": False,
            "reference_modified": False, "score_influence": 0.0, "real_orders_enabled": False,
            "outputs": [str(detail_path.relative_to(root)), str(aggregate_path.relative_to(root))],
        }
    except Exception as exc:
        payload = {
            "status": f"SHADOW_FAILED:{type(exc).__name__}",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "instrument_rows": 0,
            "eligible_rows": 0,
            "audit_only_rows": 0,
            "sector_rows": 0,
            "short_excluded": True,
            "block_data_reopening_allowed": False,
            "reference_modified": False,
            "score_influence": 0.0,
            "real_orders_enabled": False,
            "error": str(exc)[:400],
            "outputs": [],
        }
    (root / AUDIT).parent.mkdir(parents=True, exist_ok=True)
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
