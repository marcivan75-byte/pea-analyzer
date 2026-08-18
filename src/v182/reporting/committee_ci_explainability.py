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


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _safe_num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _latest_provenance(root: Path, isins: set[str], fields: set[str]) -> dict[tuple[str, str], dict]:
    path = root / "state" / "provenance" / "OBSERVATION_PROVENANCE.csv"
    if not path.exists() or not isins or not fields:
        return {}
    usecols = ["recorded_at_utc", "isin", "field", "source", "source_url", "evidence_level", "as_of", "validation_status"]
    kept: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, sep=";", encoding="utf-8-sig", usecols=usecols, dtype=str, chunksize=200_000, low_memory=False):
        mask = chunk["isin"].astype(str).isin(isins) & chunk["field"].astype(str).isin(fields)
        if mask.any():
            kept.append(chunk.loc[mask].copy())
    if not kept:
        return {}
    frame = pd.concat(kept, ignore_index=True)
    frame["recorded_at_utc"] = pd.to_datetime(frame["recorded_at_utc"], errors="coerce", utc=True)
    frame = frame.sort_values("recorded_at_utc").drop_duplicates(["isin", "field"], keep="last")
    return {(str(r["isin"]), str(r["field"])): r.to_dict() for _, r in frame.iterrows()}


def _source_field(resolution: str, criterion: str) -> str:
    text = str(resolution or "")
    if text.startswith("DIRECT:") or text.startswith("ALIAS:"):
        return text.split(":", 1)[1]
    return criterion


def _generic_details(
    source: pd.DataFrame,
    selected: pd.DataFrame,
    registry: dict,
    asset_class: str,
    horizons: Iterable[str],
) -> pd.DataFrame:
    rows: list[dict] = []
    if source.empty or selected.empty:
        return pd.DataFrame()
    source = source.copy()
    source["isin"] = source["isin"].astype(str)
    for horizon in horizons:
        sel = selected[(selected["asset_class"].astype(str) == asset_class) & (selected["horizon"].astype(str) == horizon)]
        if sel.empty:
            continue
        active = active_criteria(registry, horizon)
        if not active:
            continue
        score_map: dict[str, pd.Series] = {}
        raw_map: dict[str, pd.Series] = {}
        resolution_map: dict[str, str] = {}
        denom = pd.Series(0.0, index=source.index, dtype=float)
        for criterion, weight, direction in active:
            vals, resolution = resolve_field(source, criterion)
            resolution_map[criterion] = resolution
            if vals is None:
                score_map[criterion] = pd.Series(np.nan, index=source.index)
                raw_map[criterion] = pd.Series(np.nan, index=source.index)
                continue
            scored = _pct_score(vals, direction)
            score_map[criterion] = scored
            raw_map[criterion] = vals
            denom += scored.notna().astype(float) * float(weight)
        by_isin = {str(v): idx for idx, v in source["isin"].items()}
        for _, decision in sel.iterrows():
            isin = str(decision.get("isin", ""))
            if isin not in by_isin:
                continue
            idx = by_isin[isin]
            row_denom = float(denom.loc[idx]) if pd.notna(denom.loc[idx]) else 0.0
            for criterion, weight, direction in active:
                cscore = _safe_num(score_map[criterion].loc[idx])
                raw = raw_map[criterion].loc[idx]
                available = cscore is not None and row_denom > 0
                effective_weight = float(weight) / row_denom if available else 0.0
                contribution = cscore * effective_weight if available else None
                resolution = resolution_map[criterion]
                rows.append(
                    {
                        "asset_class": asset_class,
                        "horizon": horizon,
                        "isin": isin,
                        "name": decision.get("name"),
                        "decision": decision.get("decision"),
                        "final_score": decision.get("score"),
                        "criterion": criterion,
                        "criterion_status": "ACTIVE" if available else "MISSING",
                        "raw_value": raw if pd.notna(raw) else None,
                        "direction": direction,
                        "resolution": resolution,
                        "source_field": _source_field(resolution, criterion),
                        "criterion_score_0_100": cscore,
                        "theoretical_weight_pct": float(weight) * 100.0,
                        "effective_weight_pct": effective_weight * 100.0,
                        "weighted_contribution_points": contribution,
                        "contribution_scope": "FINAL_SCORE",
                        "explainability_note": "Exact reconstruction using the same cross-sectional rank scorer and active registry as the final decision.",
                    }
                )
    return pd.DataFrame(rows)


def _etf_mt_details(root: Path, selected: pd.DataFrame) -> pd.DataFrame:
    sel = selected[(selected["asset_class"].astype(str) == "ETF") & (selected["horizon"].astype(str) == "MT")]
    ranking = _read_csv(root / "outputs" / "etf_mt_v2081" / "V20.8.2_ETF_MT_DYNAMIC_RANKING.csv")
    if sel.empty or ranking.empty:
        return pd.DataFrame()
    cfg = json.loads((root / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json").read_text(encoding="utf-8"))
    dynamic_cfg = json.loads((root / "config" / "V20.8.2_ETF_MT_DYNAMIC.json").read_text(encoding="utf-8"))
    criteria_cfg = cfg["dynamic_criteria"]
    expected = list(criteria_cfg)
    weights = {name: float(spec["backtested_weight"]) for name, spec in criteria_cfg.items()}
    raw = ranking.set_index("instrument_id")[expected].apply(pd.to_numeric, errors="coerce")
    scores = _criterion_scores(raw, criteria_cfg)
    denom = pd.Series(0.0, index=raw.index, dtype=float)
    for criterion, weight in weights.items():
        denom += pd.to_numeric(scores[criterion], errors="coerce").notna().astype(float) * weight
    raw_weight = float(cfg["score"]["score_raw_weight"])
    rank_weight = float(cfg["score"]["cross_section_rank_weight"])
    rows: list[dict] = []
    ranking_index = ranking.drop_duplicates("instrument_id").set_index("instrument_id")
    for _, decision in sel.iterrows():
        isin = str(decision.get("isin", ""))
        if isin not in raw.index or isin not in ranking_index.index:
            continue
        row_denom = float(denom.loc[isin]) if pd.notna(denom.loc[isin]) else 0.0
        rank_row = ranking_index.loc[isin]
        for criterion, weight in weights.items():
            cscore = _safe_num(scores.loc[isin, criterion])
            available = cscore is not None and row_denom > 0
            effective_weight = weight / row_denom if available else 0.0
            dynamic_contribution = cscore * effective_weight if available else None
            final_contribution = dynamic_contribution * raw_weight if dynamic_contribution is not None else None
            rows.append(
                {
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
                    "effective_weight_pct": effective_weight * 100.0,
                    "weighted_contribution_points": final_contribution,
                    "contribution_scope": f"FINAL_SCORE_RAW_COMPONENT_{raw_weight:.2f}",
                    "explainability_note": "Exact criterion contribution to the dynamic raw component; final score also contains the cross-sectional rank component.",
                }
            )
        rank_score = _safe_num(rank_row.get("dynamic_score_rank_pct"))
        rows.append(
            {
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
                "theoretical_weight_pct": rank_weight * 100.0,
                "effective_weight_pct": rank_weight * 100.0,
                "weighted_contribution_points": rank_score * rank_weight if rank_score is not None else None,
                "contribution_scope": "FINAL_SCORE_RANK_COMPONENT",
                "explainability_note": "Exact cross-sectional rank component of ETF MT dynamic final score.",
            }
        )
    return pd.DataFrame(rows)


def _attach_provenance(root: Path, detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return detail
    isins = set(detail["isin"].astype(str))
    fields = set(detail["source_field"].dropna().astype(str))
    lookup = _latest_provenance(root, isins, fields)
    out = detail.copy()
    source = []
    source_url = []
    evidence_level = []
    as_of = []
    validation_status = []
    for _, row in out.iterrows():
        meta = lookup.get((str(row["isin"]), str(row["source_field"])), {})
        source.append(meta.get("source"))
        source_url.append(meta.get("source_url"))
        evidence_level.append(meta.get("evidence_level"))
        as_of.append(meta.get("as_of"))
        validation_status.append(meta.get("validation_status"))
    out["source"] = source
    out["source_url"] = source_url
    out["evidence_level"] = evidence_level
    out["as_of"] = as_of
    out["validation_status"] = validation_status
    return out


def _join_context(root: Path, selected: pd.DataFrame) -> pd.DataFrame:
    out = selected.copy()
    for path, columns in (
        (
            root / "outputs" / "committee_master" / "V21_8_ENTRY_EXIT_CHALLENGER.csv",
            ["asset_class", "horizon", "isin", "v21_8_entry_state", "v21_8_position_state", "v21_8_entry_reasons", "v21_8_position_reasons"],
        ),
        (
            root / "outputs" / "committee_master" / "COMMITTEE_SECTOR_ROTATION_V2_CONTEXT.csv",
            ["asset_class", "horizon", "isin", "sector_v2_status", "theme_v2_status", "valuation_warning", "correction_alert"],
        ),
    ):
        frame = _read_csv(path)
        if frame.empty:
            continue
        keep = [c for c in columns if c in frame.columns]
        keys = [c for c in ["asset_class", "horizon", "isin"] if c in keep]
        if len(keys) == 3:
            frame = frame[keep].drop_duplicates(keys, keep="last")
            out = out.merge(frame, on=keys, how="left", suffixes=("", "_context"))
    risk = _read_csv(root / "outputs" / "risk" / "BETA_CORRELATION_RISK_ROWS.csv")
    if not risk.empty and "isin" in risk.columns:
        risk_keep = [c for c in ["isin", "risk_beta_reliability", "risk_metric_status", "risk_verdict", "risk_score_0_100_shadow"] if c in risk.columns]
        risk = risk[risk_keep].drop_duplicates("isin", keep="last")
        out = out.merge(risk, on="isin", how="left", suffixes=("", "_risk"))
    return out


def _factor_summary(detail: pd.DataFrame, isin: str, horizon: str, *, positive: bool) -> str:
    subset = detail[(detail["isin"].astype(str) == isin) & (detail["horizon"].astype(str) == horizon)].copy()
    subset = subset[subset["criterion_status"].astype(str) == "ACTIVE"]
    if subset.empty:
        return "n/a"
    subset["criterion_score_0_100"] = pd.to_numeric(subset["criterion_score_0_100"], errors="coerce")
    subset["weighted_contribution_points"] = pd.to_numeric(subset["weighted_contribution_points"], errors="coerce")
    subset = subset.dropna(subset=["criterion_score_0_100"])
    if subset.empty:
        return "n/a"
    if positive:
        ranked = subset.sort_values(["weighted_contribution_points", "criterion_score_0_100"], ascending=[False, False]).head(3)
    else:
        ranked = subset.sort_values(["criterion_score_0_100", "weighted_contribution_points"], ascending=[True, True]).head(3)
    return ", ".join(f"{r.criterion}={float(r.criterion_score_0_100):.0f}" for r in ranked.itertuples())


def _android_markdown(context: pd.DataFrame, detail: pd.DataFrame, run_label: str) -> str:
    lines = [
        f"# Comité d'investissement — Android — {run_label}",
        "",
        "> Aide à la décision. Aucun ordre réel. T1/T2 = ACTION TCT uniquement. V21.8 = Entry/Exit decision-support.",
        "",
    ]
    context = context.copy()
    context["_priority"] = context["decision"].astype(str).map(DECISION_PRIORITY).fillna(9)
    context["_score"] = pd.to_numeric(context["score"], errors="coerce")
    context = context.sort_values(["_priority", "_score"], ascending=[True, False])
    for (asset, horizon), group in context.groupby(["asset_class", "horizon"], sort=False):
        lines.append(f"## {asset} — {horizon}")
        for row in group.head(12).itertuples():
            isin = str(row.isin)
            score = _safe_num(row.score)
            coverage = _safe_num(getattr(row, "coverage_pct", None))
            positives = _factor_summary(detail, isin, str(row.horizon), positive=True)
            negatives = _factor_summary(detail, isin, str(row.horizon), positive=False)
            entry_state = getattr(row, "v21_8_entry_state", None)
            position_state = getattr(row, "v21_8_position_state", None)
            risk = getattr(row, "risk_verdict", None)
            valuation = getattr(row, "valuation_warning", None)
            correction = getattr(row, "correction_alert", None)
            warnings = [str(x) for x in [risk, valuation, correction] if pd.notna(x) and str(x).strip() and str(x).lower() != "nan"]
            lines.append(
                f"- **{row.name}** ({isin}) — {row.decision} — score {score:.1f if score is not None else 0}"
            )
            lines.append(f"  - couverture: {coverage:.1f}%" if coverage is not None else "  - couverture: n/a")
            lines.append(f"  - +: {positives}")
            lines.append(f"  - -: {negatives}")
            lines.append(f"  - V21.8: entrée={entry_state or 'n/a'} ; position={position_state or 'n/a'}")
            lines.append(f"  - warnings: {' | '.join(warnings) if warnings else 'aucun warning contextuel publié'}")
        lines.append("")
    return "\n".join(lines)


def _reconstruction_audit(selected: pd.DataFrame, detail: pd.DataFrame) -> dict:
    checks = []
    if detail.empty:
        return {"rows": 0, "reconstruction_checks": [], "max_abs_delta": None}
    grouped = detail.groupby(["asset_class", "horizon", "isin"], dropna=False)["weighted_contribution_points"].sum(min_count=1).reset_index(name="reconstructed_score")
    merged = selected.merge(grouped, on=["asset_class", "horizon", "isin"], how="left")
    merged["score_num"] = pd.to_numeric(merged["score"], errors="coerce")
    merged["delta"] = merged["reconstructed_score"] - merged["score_num"]
    for row in merged.itertuples():
        checks.append(
            {
                "asset_class": row.asset_class,
                "horizon": row.horizon,
                "isin": row.isin,
                "published_score": _safe_num(row.score_num),
                "reconstructed_score": _safe_num(row.reconstructed_score),
                "delta": _safe_num(row.delta),
            }
        )
    valid = pd.to_numeric(merged["delta"], errors="coerce").abs().dropna()
    return {
        "rows": int(len(merged)),
        "reconstruction_checks": checks,
        "max_abs_delta": float(valid.max()) if not valid.empty else None,
        "within_0_02_points": bool((valid <= 0.02).all()) if not valid.empty else False,
    }


def run(root: Path = ROOT) -> dict:
    decisions_path = root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv"
    decisions = _read_csv(decisions_path)
    if decisions.empty:
        return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING", "real_orders_enabled": False}
    selected = decisions[decisions["decision"].astype(str).isin(SELECTED_CODES)].copy()
    selected = selected[selected["asset_class"].astype(str).isin({"ACTION", "ETF"})]

    action_source = _read_csv(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etf_source = _read_csv(root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_registry = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    etf_registry = load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")

    detail_parts = [
        _generic_details(action_source, selected, action_registry, "ACTION", ["CT", "MT", "LT", "SHORT", "TOP_DOWN"]),
        _generic_details(etf_source, selected, etf_registry, "ETF", ["CT", "LT", "SHORT", "TOP_DOWN"]),
        _etf_mt_details(root, selected),
    ]
    detail = pd.concat([d for d in detail_parts if not d.empty], ignore_index=True) if any(not d.empty for d in detail_parts) else pd.DataFrame()
    detail = _attach_provenance(root, detail)
    context = _join_context(root, selected)

    out_mobile = root / "outputs" / "mobile"
    out_committee = root / "outputs" / "committee_master"
    out_audit = root / "outputs" / "audit"
    out_mobile.mkdir(parents=True, exist_ok=True)
    out_committee.mkdir(parents=True, exist_ok=True)
    out_audit.mkdir(parents=True, exist_ok=True)

    run_label = "V21.8.1 canonical run"
    android_path = out_mobile / "ANDROID_CI_CONTROL_CENTER.md"
    android_path.write_text(_android_markdown(context, detail, run_label), encoding="utf-8")

    xlsx_path = out_committee / "CI_PC_EXPLAINABILITY.xlsx"
    metadata = pd.DataFrame(
        [
            {"field": "source_decisions", "value": str(decisions_path.relative_to(root))},
            {"field": "selected_rows", "value": int(len(selected))},
            {"field": "criteria_detail_rows", "value": int(len(detail))},
            {"field": "t1_t2_scope", "value": "ACTION_TCT_ONLY"},
            {"field": "entry_exit", "value": "V21.8 decision-support"},
            {"field": "real_orders_enabled", "value": False},
            {"field": "weight_changes", "value": False},
        ]
    )
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        context.to_excel(writer, sheet_name="Selections", index=False)
        detail.to_excel(writer, sheet_name="Criteria_Detail", index=False)
        metadata.to_excel(writer, sheet_name="Metadata_Audit", index=False)

    reconstruction = _reconstruction_audit(selected, detail)
    payload = {
        "status": "SUCCESS",
        "version": "CI_EXPLAINABILITY_V1",
        "selected_rows": int(len(selected)),
        "criteria_detail_rows": int(len(detail)),
        "android_output": str(android_path.relative_to(root)),
        "pc_output": str(xlsx_path.relative_to(root)),
        "same_canonical_decisions_for_android_pc": True,
        "score_or_decision_mutation": False,
        "weight_or_threshold_changes": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "real_orders_enabled": False,
        "reconstruction": reconstruction,
    }
    audit_path = out_audit / "CI_EXPLAINABILITY_AUDIT.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
