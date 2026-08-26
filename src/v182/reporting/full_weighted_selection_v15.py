from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _valid(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip().str.lower()
    return series.notna() & ~text.isin({"", "nan", "none", "n/a", "na", "missing", "unknown"})


def _source(root: Path, asset_class: str) -> Path:
    name = "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv" if asset_class == "ACTION" else "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    path = root / "outputs" / name
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _committee_scores(root: Path, asset_class: str) -> pd.DataFrame:
    path = root / "outputs/committee_master/COMMITTEE_DECISIONS.csv"
    frame = _read_csv(path)
    frame = frame[frame["asset_class"].astype(str).str.upper().eq(asset_class)].copy()
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    priority = {"CT": 0, "MT": 1, "SHORT": 2, "TOP_DOWN": 3}
    frame["_priority"] = frame["horizon"].astype(str).str.upper().map(priority).fillna(9)
    return frame.sort_values(["isin", "score", "_priority"], ascending=[True, False, True]).drop_duplicates("isin")


def _evaluate(root: Path, asset_class: str, registry: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = _read_csv(_source(root, asset_class))
    frame["isin"] = frame["isin"].astype(str)
    weight_col = "Poids Actions normalisé %" if asset_class == "ACTION" else "Poids ETF normalisé %"
    criteria = registry[pd.to_numeric(registry[weight_col], errors="coerce").fillna(0).gt(0)].copy()
    criteria[weight_col] = pd.to_numeric(criteria[weight_col], errors="coerce").fillna(0.0)
    mapping = config["criteria"]
    observed_weight = pd.Series(0.0, index=frame.index)
    observed_count = pd.Series(0, index=frame.index, dtype=int)
    audit_rows: list[dict] = []
    for _, criterion in criteria.iterrows():
        criterion_id = str(criterion["ID"])
        aliases = [field for field in mapping.get(criterion_id, []) if field in frame.columns]
        available = pd.Series(False, index=frame.index)
        used: list[str] = []
        for field in aliases:
            mask = _valid(frame[field])
            if mask.any():
                available |= mask
                used.append(field)
        weight = float(criterion[weight_col])
        observed_weight += available.astype(float) * weight
        observed_count += available.astype(int)
        audit_rows.append(
            {
                "asset_class": asset_class,
                "criterion_id": criterion_id,
                "block": criterion["Bloc"],
                "criterion": criterion["Critère"],
                "weight_pct": weight,
                "mapped_fields": "|".join(used),
                "available_rows": int(available.sum()),
                "universe_rows": int(len(frame)),
                "availability_pct": round(float(available.mean() * 100.0), 4),
                "status": "FACTUAL_AVAILABLE" if used else "MISSING_NO_FACTUAL_MAPPING",
            }
        )
    total_weight = float(criteria[weight_col].sum())
    coverage = observed_weight / total_weight * 100.0
    result = frame[["isin", "name"]].copy()
    result["asset_class"] = asset_class
    result["full_weighted_criteria"] = int(len(criteria))
    result["available_criteria"] = observed_count
    result["weighted_coverage_pct"] = coverage.round(4)
    result["minimum_weighted_coverage_pct"] = float(config["minimum_weighted_coverage_pct"])
    result["full_referential_gate"] = "BLOCK_DATA"
    pass_mask = coverage.ge(float(config["minimum_weighted_coverage_pct"]))
    result.loc[pass_mask, "full_referential_gate"] = "PASS"
    committee = _committee_scores(root, asset_class)[["isin", "horizon", "decision", "score"]]
    result = result.merge(committee, on="isin", how="left")
    result["selection_status"] = "BLOCK_DATA_FULL_WEIGHTED_REFERENTIAL"
    selectable = pass_mask & result["score"].notna()
    result.loc[selectable, "selection_status"] = "ELIGIBLE_FOR_COMMITTEE_RANKING"
    return result, pd.DataFrame(audit_rows)


def run(root: Path = ROOT) -> dict:
    config = json.loads((root / "config/V15_FULL_WEIGHTED_FIELD_MAP.json").read_text(encoding="utf-8"))
    registry = pd.read_excel(root / "inputs/V18.2_ALL_CRITERIA.xlsx", sheet_name="Tous_les_criteres")
    outputs: list[pd.DataFrame] = []
    audits: list[pd.DataFrame] = []
    for asset_class in ("ACTION", "ETF"):
        selected, audit = _evaluate(root, asset_class, registry, config)
        outputs.append(selected)
        audits.append(audit)
    result = pd.concat(outputs, ignore_index=True)
    audit = pd.concat(audits, ignore_index=True)
    result = result.sort_values(["full_referential_gate", "score"], ascending=[False, False])
    outdir = root / "outputs/committee_master"
    outdir.mkdir(parents=True, exist_ok=True)
    result.to_csv(outdir / "FULL_WEIGHTED_SELECTION_V15.csv", sep=";", index=False, encoding="utf-8-sig")
    audit.to_csv(outdir / "FULL_WEIGHTED_CRITERIA_AUDIT_V15.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS",
        "version": config["version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_criteria_rows": int(len(registry)),
        "weighted_criteria": {
            "ACTION": int((pd.to_numeric(registry["Poids Actions normalisé %"], errors="coerce").fillna(0) > 0).sum()),
            "ETF": int((pd.to_numeric(registry["Poids ETF normalisé %"], errors="coerce").fillna(0) > 0).sum()),
        },
        "universe_rows": int(len(result)),
        "pass_full_weighted_gate": int(result["full_referential_gate"].eq("PASS").sum()),
        "blocked_data": int(result["full_referential_gate"].eq("BLOCK_DATA").sum()),
        "selected": int(result["selection_status"].eq("ELIGIBLE_FOR_COMMITTEE_RANKING").sum()),
        "median_weighted_coverage_pct": round(float(result["weighted_coverage_pct"].median()), 4),
        "maximum_weighted_coverage_pct": round(float(result["weighted_coverage_pct"].max()), 4),
        "missing_is_not_neutral": True,
        "t1_t2_influence": 0.0,
        "decision_mutation": False,
        "real_orders_enabled": False,
        "outputs": {
            "selection": "outputs/committee_master/FULL_WEIGHTED_SELECTION_V15.csv",
            "criteria_audit": "outputs/committee_master/FULL_WEIGHTED_CRITERIA_AUDIT_V15.csv",
        },
    }
    audit_path = root / "outputs/audit/FULL_WEIGHTED_SELECTION_V15.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
