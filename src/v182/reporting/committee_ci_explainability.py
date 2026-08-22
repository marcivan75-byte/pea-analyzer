from __future__ import annotations

from pathlib import Path
import json
from typing import Iterable

import numpy as np
import pandas as pd

from v182.decision.committee_master import _pct_score, active_criteria, load_registry, resolve_field
from v182.features.etf_mt_v2081 import _criterion_scores

ROOT = Path(__file__).resolve().parents[3]
SELECTED_CODES = {"BUY_CANDIDATE", "WATCH", "REVIEW", "WATCH_NOT_TOP2"}
DECISION_PRIORITY = {"BUY_CANDIDATE": 0, "WATCH": 1, "WATCH_NOT_TOP2": 2, "REVIEW": 3}


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _source_field(resolution: str, criterion: str) -> str:
    text = str(resolution or "")
    if text.startswith(("DIRECT:", "ALIAS:")):
        return text.split(":", 1)[1]
    return criterion


def _generic_details(source: pd.DataFrame, selected: pd.DataFrame, registry: dict, asset: str, horizons: Iterable[str]) -> pd.DataFrame:
    rows: list[dict] = []
    if source.empty:
        return pd.DataFrame()
    source = source.copy()
    source["isin"] = source["isin"].astype(str)
    index_by_isin = {str(v): idx for idx, v in source["isin"].items()}
    for horizon in horizons:
        chosen = selected[(selected["asset_class"].astype(str) == asset) & (selected["horizon"].astype(str) == horizon)]
        active = active_criteria(registry, horizon)
        if chosen.empty or not active:
            continue
        raw_map: dict[str, pd.Series] = {}
        score_map: dict[str, pd.Series] = {}
        resolution_map: dict[str, str] = {}
        denom = pd.Series(0.0, index=source.index, dtype=float)
        for criterion, weight, direction in active:
            values, resolution = resolve_field(source, criterion)
            resolution_map[criterion] = resolution
            if values is None:
                raw_map[criterion] = pd.Series(np.nan, index=source.index)
                score_map[criterion] = pd.Series(np.nan, index=source.index)
                continue
            scored = _pct_score(values, direction)
            raw_map[criterion] = values
            score_map[criterion] = scored
            denom += scored.notna().astype(float) * float(weight)
        for _, decision in chosen.iterrows():
            isin = str(decision.get("isin", ""))
            idx = index_by_isin.get(isin)
            if idx is None:
                continue
            row_denom = float(denom.loc[idx]) if pd.notna(denom.loc[idx]) else 0.0
            for criterion, weight, direction in active:
                cscore = _num(score_map[criterion].loc[idx])
                raw_value = raw_map[criterion].loc[idx]
                available = cscore is not None and row_denom > 0
                eff = float(weight) / row_denom if available else 0.0
                resolution = resolution_map[criterion]
                rows.append({
                    "asset_class": asset,
                    "horizon": horizon,
                    "isin": isin,
                    "name": decision.get("name"),
                    "decision": decision.get("decision"),
                    "final_score": decision.get("score"),
                    "criterion": criterion,
                    "criterion_status": "ACTIVE" if available else "MISSING",
                    "raw_value": raw_value if pd.notna(raw_value) else None,
                    "direction": direction,
                    "resolution": resolution,
                    "source_field": _source_field(resolution, criterion),
                    "criterion_score_0_100": cscore,
                    "theoretical_weight_pct": float(weight) * 100.0,
                    "effective_weight_pct": eff * 100.0,
                    "weighted_contribution_points": cscore * eff if available else None,
                    "contribution_scope": "FINAL_SCORE",
                })
    return pd.DataFrame(rows)


def _etf_mt_details(root: Path, selected: pd.DataFrame) -> pd.DataFrame:
    chosen = selected[(selected["asset_class"].astype(str) == "ETF") & (selected["horizon"].astype(str) == "MT")]
    ranking = _read(root / "outputs" / "etf_mt_v2081" / "V20.8.2_ETF_MT_DYNAMIC_RANKING.csv")
    if chosen.empty or ranking.empty:
        return pd.DataFrame()
    cfg = json.loads((root / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json").read_text(encoding="utf-8"))
    criteria_cfg = cfg["dynamic_criteria"]
    expected = list(criteria_cfg)
    weights = {name: float(spec["backtested_weight"]) for name, spec in criteria_cfg.items()}
    raw = ranking.set_index("instrument_id")[expected].apply(pd.to_numeric, errors="coerce")
    scores = _criterion_scores(raw, criteria_cfg)
    denom = pd.Series(0.0, index=raw.index, dtype=float)
    for criterion, weight in weights.items():
        denom += pd.to_numeric(scores[criterion], errors="coerce").notna().astype(float) * weight
    raw_component = float(cfg["score"]["score_raw_weight"])
    rank_component = float(cfg["score"]["cross_section_rank_weight"])
    ranked = ranking.drop_duplicates("instrument_id").set_index("instrument_id")
    rows: list[dict] = []
    for _, decision in chosen.iterrows():
        isin = str(decision.get("isin", ""))
        if isin not in raw.index or isin not in ranked.index:
            continue
        row_denom = float(denom.loc[isin]) if pd.notna(denom.loc[isin]) else 0.0
        for criterion, weight in weights.items():
            cscore = _num(scores.loc[isin, criterion])
            available = cscore is not None and row_denom > 0
            eff = weight / row_denom if available else 0.0
            rows.append({
                "asset_class": "ETF",
                "horizon": "MT",
                "isin": isin,
                "name": decision.get("name"),
                "decision": decision.get("decision"),
                "final_score": decision.get("score"),
                "criterion": criterion,
                "criterion_status": "ACTIVE" if available else "MISSING",
                "raw_value": raw.loc[isin, criterion] if pd.notna(raw.loc[isin, criterion]) else None,
                "direction": criteria_cfg[criterion].get("direction", "HIGH"),
                "resolution": f"DIRECT:{criterion}",
                "source_field": criterion,
                "criterion_score_0_100": cscore,
                "theoretical_weight_pct": weight * 100.0,
                "effective_weight_pct": eff * 100.0,
                "weighted_contribution_points": cscore * eff * raw_component if available else None,
                "contribution_scope": f"FINAL_SCORE_RAW_COMPONENT_{raw_component:.2f}",
            })
        rank_score = _num(ranked.loc[isin].get("dynamic_score_rank_pct"))
        rows.append({
            "asset_class": "ETF",
            "horizon": "MT",
            "isin": isin,
            "name": decision.get("name"),
            "decision": decision.get("decision"),
            "final_score": decision.get("score"),
            "criterion": "CROSS_SECTION_RANK_COMPONENT",
            "criterion_status": "ACTIVE" if rank_score is not None else "MISSING",
            "raw_value": rank_score,
            "direction": "HIGH",
            "resolution": "DERIVED:dynamic_score_rank_pct",
            "source_field": "dynamic_score_rank_pct",
            "criterion_score_0_100": rank_score,
            "theoretical_weight_pct": rank_component * 100.0,
            "effective_weight_pct": rank_component * 100.0,
            "weighted_contribution_points": rank_score * rank_component if rank_score is not None else None,
            "contribution_scope": "FINAL_SCORE_RANK_COMPONENT",
        })
    return pd.DataFrame(rows)


def _latest_provenance(root: Path, isins: set[str], fields: set[str]) -> dict[tuple[str, str], dict]:
    path = root / "state" / "provenance" / "OBSERVATION_PROVENANCE.csv"
    if not path.exists() or not isins or not fields:
        return {}
    usecols = ["recorded_at_utc", "isin", "field", "source", "source_url", "evidence_level", "as_of", "validation_status"]
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, sep=";", encoding="utf-8-sig", usecols=usecols, dtype=str, chunksize=200_000, low_memory=False):
        mask = chunk["isin"].isin(isins) & chunk["field"].isin(fields)
        if mask.any():
            chunks.append(chunk.loc[mask].copy())
    if not chunks:
        return {}
    frame = pd.concat(chunks, ignore_index=True)
    frame["recorded_at_utc"] = pd.to_datetime(frame["recorded_at_utc"], errors="coerce", utc=True)
    frame = frame.sort_values("recorded_at_utc").drop_duplicates(["isin", "field"], keep="last")
    return {(str(r["isin"]), str(r["field"])): r.to_dict() for _, r in frame.iterrows()}


def _attach_provenance(root: Path, detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return detail
    lookup = _latest_provenance(root, set(detail["isin"].astype(str)), set(detail["source_field"].astype(str)))
    out = detail.copy()
    for column in ["source", "source_url", "evidence_level", "as_of", "validation_status"]:
        out[column] = [lookup.get((str(r.isin), str(r.source_field)), {}).get(column) for r in out.itertuples()]
    return out


def _join_context(root: Path, selected: pd.DataFrame) -> pd.DataFrame:
    out = selected.copy()
    entry = _read(root / "outputs" / "committee_master" / "V21_8_ENTRY_EXIT_CHALLENGER.csv")
    if not entry.empty:
        keys = ["asset_class", "horizon", "isin"]
        keep = keys + [c for c in ["v21_8_entry_state", "v21_8_position_state", "v21_8_entry_reasons", "v21_8_position_reasons"] if c in entry.columns]
        out = out.merge(entry[keep].drop_duplicates(keys, keep="last"), on=keys, how="left")
    sector = _read(root / "outputs" / "committee_master" / "COMMITTEE_SECTOR_ROTATION_V2_CONTEXT.csv")
    if not sector.empty:
        keys = ["asset_class", "horizon", "isin"]
        keep = keys + [c for c in ["valuation_warning", "correction_alert"] if c in sector.columns]
        out = out.merge(sector[keep].drop_duplicates(keys, keep="last"), on=keys, how="left")
    risk = _read(root / "outputs" / "risk" / "BETA_CORRELATION_RISK_ROWS.csv")
    if not risk.empty and "isin" in risk.columns:
        keep = ["isin"] + [c for c in ["risk_beta_reliability", "risk_metric_status", "risk_verdict"] if c in risk.columns]
        out = out.merge(risk[keep].drop_duplicates("isin", keep="last"), on="isin", how="left")
    return out


def _factor_summary(detail: pd.DataFrame, isin: str, horizon: str, positive: bool) -> str:
    subset = detail[(detail["isin"].astype(str) == isin) & (detail["horizon"].astype(str) == horizon)].copy()
    subset = subset[subset["criterion_status"].astype(str) == "ACTIVE"]
    if subset.empty:
        return "n/a"
    subset["criterion_score_0_100"] = pd.to_numeric(subset["criterion_score_0_100"], errors="coerce")
    subset["weighted_contribution_points"] = pd.to_numeric(subset["weighted_contribution_points"], errors="coerce")
    subset = subset.dropna(subset=["criterion_score_0_100"])
    if positive:
        subset = subset.sort_values(["weighted_contribution_points", "criterion_score_0_100"], ascending=[False, False]).head(3)
    else:
        subset = subset.sort_values(["criterion_score_0_100", "weighted_contribution_points"], ascending=[True, True]).head(3)
    return ", ".join(f"{r.criterion}={float(r.criterion_score_0_100):.0f}" for r in subset.itertuples()) or "n/a"


def _android(context: pd.DataFrame, detail: pd.DataFrame) -> str:
    lines = [
        "# Comité d'investissement — Android — V21.8.1",
        "",
        "> Aucun ordre réel. T1/T2 = ACTION TCT uniquement. V21.8 = aide entrée/conservation/protection/sortie.",
        "",
    ]
    frame = context.copy()
    frame["_priority"] = frame["decision"].astype(str).map(DECISION_PRIORITY).fillna(9)
    frame["_score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.sort_values(["_priority", "_score"], ascending=[True, False])
    for (asset, horizon), group in frame.groupby(["asset_class", "horizon"], sort=False):
        lines.append(f"## {asset} — {horizon}")
        for row in group.head(12).itertuples():
            score = _num(row.score)
            score_text = f"{score:.1f}" if score is not None else "n/a"
            coverage = _num(getattr(row, "coverage_pct", None))
            coverage_text = f"{coverage:.1f}%" if coverage is not None else "n/a"
            isin = str(row.isin)
            warnings = []
            for field in ["risk_verdict", "valuation_warning", "correction_alert"]:
                value = getattr(row, field, None)
                if value is not None and pd.notna(value) and str(value).strip() and str(value).lower() != "nan":
                    warnings.append(str(value))
            lines.append(f"- **{row.name}** ({isin}) — {row.decision} — score {score_text} — couverture {coverage_text}")
            lines.append(f"  - +: {_factor_summary(detail, isin, str(row.horizon), True)}")
            lines.append(f"  - -: {_factor_summary(detail, isin, str(row.horizon), False)}")
            lines.append(
                f"  - V21.8: entrée={getattr(row, 'v21_8_entry_state', 'n/a') or 'n/a'} ; "
                f"position={getattr(row, 'v21_8_position_state', 'n/a') or 'n/a'}"
            )
            lines.append(f"  - warnings: {' | '.join(warnings) if warnings else 'aucun warning contextuel publié'}")
        lines.append("")
    return "\n".join(lines)


def _reconstruction(selected: pd.DataFrame, detail: pd.DataFrame) -> dict:
    if detail.empty:
        return {"rows": 0, "within_0_02_points": False, "max_abs_delta": None}
    grouped = detail.groupby(["asset_class", "horizon", "isin"], dropna=False)["weighted_contribution_points"].sum(min_count=1).reset_index(name="reconstructed_score")
    merged = selected.merge(grouped, on=["asset_class", "horizon", "isin"], how="left")
    merged["published"] = pd.to_numeric(merged["score"], errors="coerce")
    merged["delta"] = pd.to_numeric(merged["reconstructed_score"], errors="coerce") - merged["published"]
    valid = merged["delta"].abs().dropna()
    return {
        "rows": int(len(merged)),
        "within_0_02_points": bool((valid <= 0.02).all()) if not valid.empty else False,
        "max_abs_delta": float(valid.max()) if not valid.empty else None,
    }


def run(root: Path = ROOT) -> dict:
    decisions = _read(root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv")
    if decisions.empty:
        return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING", "real_orders_enabled": False}
    selected = decisions[decisions["decision"].astype(str).isin(SELECTED_CODES)].copy()
    selected = selected[selected["asset_class"].astype(str).isin({"ACTION", "ETF"})]
    action_source = _read(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etf_source = _read(root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_registry = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    etf_registry = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    parts = [
        _generic_details(action_source, selected, action_registry, "ACTION", ["CT", "MT", "SHORT", "TOP_DOWN"]),
        _generic_details(etf_source, selected, etf_registry, "ETF", ["CT", "SHORT", "TOP_DOWN"]),
        _etf_mt_details(root, selected),
    ]
    detail = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) else pd.DataFrame()
    detail = _attach_provenance(root, detail)
    context = _join_context(root, selected)
    mobile_dir = root / "outputs" / "mobile"
    committee_dir = root / "outputs" / "committee_master"
    audit_dir = root / "outputs" / "audit"
    mobile_dir.mkdir(parents=True, exist_ok=True)
    committee_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    android_path = mobile_dir / "ANDROID_CI_CONTROL_CENTER.md"
    android_path.write_text(_android(context, detail), encoding="utf-8")
    pc_path = committee_dir / "CI_PC_EXPLAINABILITY.xlsx"
    metadata = pd.DataFrame([
        {"field": "selected_rows", "value": int(len(selected))},
        {"field": "criteria_detail_rows", "value": int(len(detail))},
        {"field": "same_canonical_run_android_pc", "value": True},
        {"field": "weight_or_threshold_changes", "value": False},
        {"field": "t1_t2_scope", "value": "ACTION_TCT_ONLY"},
        {"field": "real_orders_enabled", "value": False},
    ])
    with pd.ExcelWriter(pc_path, engine="openpyxl") as writer:
        context.to_excel(writer, sheet_name="Selections", index=False)
        detail.to_excel(writer, sheet_name="Criteria_Detail", index=False)
        metadata.to_excel(writer, sheet_name="Metadata_Audit", index=False)
    payload = {
        "status": "SUCCESS",
        "version": "CI_EXPLAINABILITY_V1",
        "selected_rows": int(len(selected)),
        "criteria_detail_rows": int(len(detail)),
        "android_output": str(android_path.relative_to(root)),
        "pc_output": str(pc_path.relative_to(root)),
        "same_canonical_run_android_pc": True,
        "score_or_decision_mutation": False,
        "weight_or_threshold_changes": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "real_orders_enabled": False,
        "reconstruction": _reconstruction(selected, detail),
    }
    (audit_dir / "CI_EXPLAINABILITY_AUDIT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
