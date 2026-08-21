from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

from v182.reporting import tct_v24_4_pit_validator as base
from v182.reporting.tct_v24_4_pit_validator_runtime import _spearman_without_scipy


ROOT = Path(__file__).resolve().parents[3]
VERSION = "TCT_V24.4.2_PIT_VALIDATOR"


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame.get(column, pd.Series(np.nan, index=frame.index)), errors="coerce")


def _primary_metrics(frame: pd.DataFrame) -> dict:
    recall, hits, targets = base._top_k_recall(frame, "movement_potential_score", k=10)
    tech_recall, _, _ = base._top_k_recall(frame, "technical_impulse_score", k=10)
    decile, decile_rows = base._top_decile_lift(frame, "movement_potential_score")
    tech_decile, _ = base._top_decile_lift(frame, "technical_impulse_score")
    spearman = _spearman_without_scipy(_num(frame, "movement_potential_score"), _num(frame, "realized_abs_return_pct"))
    tech_spearman = _spearman_without_scipy(_num(frame, "technical_impulse_score"), _num(frame, "realized_abs_return_pct"))
    return {
        "top10_absolute_mover_recall": recall,
        "top10_absolute_mover_recall_hits": hits,
        "top10_absolute_mover_recall_targets": targets,
        "technical_only_top10_recall": tech_recall,
        "top10_recall_improvement_pp_vs_technical": None if recall is None or tech_recall is None else (recall - tech_recall) * 100.0,
        "top_decile_absolute_return_lift": decile,
        "top_decile_rows": decile_rows,
        "technical_only_top_decile_lift": tech_decile,
        "top_decile_lift_improvement_vs_technical": None if decile is None or tech_decile is None else decile - tech_decile,
        "spearman_movement_score_vs_abs_return": spearman,
        "technical_only_spearman_vs_abs_return": tech_spearman,
        "spearman_improvement_vs_technical": None if spearman is None or tech_spearman is None else spearman - tech_spearman,
    }


def _significant_metrics(frame: pd.DataFrame, high_threshold: float = 70.0) -> dict:
    if frame.empty:
        return {"significant_move_precision": None, "significant_move_recall": None}
    actual = _num(frame, "significant_session_move_flag") == 1.0
    score = _num(frame, "movement_potential_score")
    technical = _num(frame, "technical_impulse_score")

    def metrics(predicted: pd.Series) -> tuple[float | None, float | None, int, int]:
        tp = int((predicted & actual).sum())
        predicted_n = int(predicted.sum())
        actual_n = int(actual.sum())
        return (None if predicted_n == 0 else tp / predicted_n, None if actual_n == 0 else tp / actual_n, predicted_n, actual_n)

    precision, recall, predicted_n, actual_n = metrics(score >= float(high_threshold))
    tech_precision, tech_recall, tech_n, _ = metrics(technical >= float(high_threshold))
    return {
        "significant_move_precision": precision,
        "significant_move_recall": recall,
        "significant_move_predictions": predicted_n,
        "significant_moves_observed": actual_n,
        "technical_significant_move_precision": tech_precision,
        "technical_significant_move_recall": tech_recall,
        "technical_significant_move_predictions": tech_n,
        "significant_move_definition": "SESSION_ABS_EXTREME_GE_MAX_2PCT_1P25_X_ATR14",
    }


def _ohlc_metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {}
    return {
        "mean_open_gap_abs_pct": base._mean(_num(frame, "realized_open_gap_pct").abs()),
        "mean_session_range_pct": base._mean(_num(frame, "realized_session_range_pct")),
        "mean_session_abs_extreme_pct": base._mean(_num(frame, "realized_session_abs_extreme_pct")),
        "mean_max_adverse_excursion_pct": base._mean(_num(frame, "realized_max_adverse_excursion_pct")),
        "spearman_score_vs_open_gap_abs": _spearman_without_scipy(_num(frame, "movement_potential_score"), _num(frame, "realized_open_gap_pct").abs()),
        "spearman_score_vs_session_range": _spearman_without_scipy(_num(frame, "movement_potential_score"), _num(frame, "realized_session_range_pct")),
    }


def _regime(value) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if not np.isfinite(x):
        return "UNKNOWN"
    if x >= 60:
        return "RISK_ON"
    if x <= 40:
        return "RISK_OFF"
    return "NEUTRAL"


def _vix_quintiles(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = pd.Series("UNKNOWN", index=series.index, dtype=object)
    clean = numeric.dropna()
    if len(clean) < 10 or clean.nunique() < 5:
        return out
    try:
        buckets = pd.qcut(clean.rank(method="first"), 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
    except ValueError:
        return out
    out.loc[clean.index] = buckets.astype(str)
    return out


def _stability(frame: pd.DataFrame, gates: dict) -> tuple[dict, pd.DataFrame]:
    if frame.empty:
        return {"status": "NOT_EVALUABLE", "qualified_slices": 0}, pd.DataFrame()
    min_rows = int(gates.get("stability", {}).get("minimum_rows_per_slice_for_gate", 8))
    work = frame.copy()
    work["_ret"] = _num(work, "realized_close_to_close_return_pct")
    work["_dir"] = _num(work, "direction_bias_score")
    work["_tech_dir"] = _num(work, "technical_direction_score")
    work["market_regime_v242"] = work.get("global_risk_on_score", pd.Series(index=work.index, dtype=float)).map(_regime)
    if "global_vix_return_pct" in work.columns:
        work["vix_change_quintile"] = _vix_quintiles(work["global_vix_return_pct"])
    rows: list[dict] = []
    dimensions = [
        ("sector_yf", "SECTOR"),
        ("market_regime_v242", "MARKET_REGIME"),
        ("vix_change_quintile", "VIX_CHANGE_QUINTILE"),
        ("candidate_rank_reason", "CANDIDATE_RANK_REASON"),
    ]
    for column, label in dimensions:
        if column not in work.columns:
            continue
        for value, group in work.groupby(column, dropna=False):
            confident = group[group["_dir"].abs() >= 25].dropna(subset=["_ret", "_dir"])
            technical = group[group["_tech_dir"].abs() >= 25].dropna(subset=["_ret", "_tech_dir"])
            hit = None if confident.empty else float((np.sign(confident["_dir"]) == np.sign(confident["_ret"])).mean())
            tech_hit = None if technical.empty else float((np.sign(technical["_tech_dir"]) == np.sign(technical["_ret"])).mean())
            delta = None if hit is None or tech_hit is None else (hit - tech_hit) * 100.0
            rows.append({
                "slice_type": label,
                "slice_value": str(value),
                "rows": int(len(group)),
                "qualified_for_stability_gate": bool(len(group) >= min_rows and str(value) != "UNKNOWN"),
                "mean_session_abs_extreme_pct": base._mean(_num(group, "realized_session_abs_extreme_pct")),
                "direction_hit_rate": hit,
                "technical_direction_hit_rate": tech_hit,
                "direction_delta_pp": delta,
            })
    table = pd.DataFrame(rows)
    qualified = table[table["qualified_for_stability_gate"]] if not table.empty else table
    max_degradation = float(gates.get("stability", {}).get("maximum_allowed_direction_hit_degradation_pp_in_qualified_slice", 10.0))
    degradations = qualified["direction_delta_pp"].dropna() if not qualified.empty else pd.Series(dtype=float)
    no_bad_degradation = bool(degradations.empty or (degradations >= -max_degradation).all())
    regimes = qualified[qualified["slice_type"] == "MARKET_REGIME"]["slice_value"].nunique() if not qualified.empty else 0
    sectors = qualified[qualified["slice_type"] == "SECTOR"]["slice_value"].nunique() if not qualified.empty else 0
    vix_quintiles = qualified[qualified["slice_type"] == "VIX_CHANGE_QUINTILE"]["slice_value"].nunique() if not qualified.empty else 0
    enough = bool(
        regimes >= int(gates.get("stability", {}).get("required_regimes_when_available", 2))
        and sectors >= int(gates.get("stability", {}).get("minimum_qualified_sector_slices_for_review", 2))
    )
    return {
        "status": "PASS_STABILITY_REVIEW" if no_bad_degradation and enough else "INSUFFICIENT_OR_UNSTABLE",
        "qualified_slices": int(len(qualified)),
        "qualified_regimes": int(regimes),
        "qualified_sectors": int(sectors),
        "qualified_vix_quintiles": int(vix_quintiles),
        "no_direction_degradation_beyond_limit": no_bad_degradation,
        "manual_review_only": True,
    }, table


def validate_ledger(ledger: pd.DataFrame, gates: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    if ledger is None or ledger.empty:
        maturity = base._maturity(pd.DataFrame(), gates)
        return {
            "version": VERSION,
            "validation_epoch": gates.get("validation_epoch"),
            "amplitude_label": gates.get("primary_amplitude_label"),
            "pit_mechanics": "DAILY_OHLC_MULTILABEL_CAUSAL_LINEAGE",
            "maturity": maturity,
            "primary_metrics": {},
            "secondary_metrics": {},
            "stability": {"status": "NOT_EVALUABLE", "qualified_slices": 0},
            "research_verdict": {"status": "NOT_EVALUABLE_BEFORE_MATURITY", "movement_validation": "NOT_EVALUABLE", "direction_validation": "NOT_EVALUABLE", "promotion_authority": False},
        }, pd.DataFrame(), pd.DataFrame()

    work = ledger.copy()
    target = str(gates.get("target_phase", "PREOPEN"))
    work = work[work.get("phase", pd.Series(index=work.index, dtype=object)).astype(str).eq(target)].copy()
    work["realized_session_abs_extreme_pct"] = _num(work, "realized_session_abs_extreme_pct")
    labeled = work.dropna(subset=["realized_session_abs_extreme_pct"]).copy()
    adapted = labeled.copy()
    adapted["realized_abs_return_pct"] = adapted["realized_session_abs_extreme_pct"]
    maturity = base._maturity(adapted, gates)
    primary = _primary_metrics(adapted)
    primary.update(_significant_metrics(labeled, 70.0))
    secondary = base._secondary_metrics(adapted, 70.0, 25.0)
    secondary.update(_ohlc_metrics(labeled))
    verdict = base._research_verdict(primary, secondary, maturity, gates)
    base_slices = base._slice_rows(adapted)
    stability, stability_table = _stability(labeled, gates)
    slices = pd.concat([base_slices, stability_table], ignore_index=True, sort=False) if not stability_table.empty else base_slices
    changes = base._preopen_postmarket_changes(ledger)
    return {
        "version": VERSION,
        "validation_epoch": gates.get("validation_epoch"),
        "pit_mechanics": "DAILY_OHLC_MULTILABEL_CAUSAL_LINEAGE",
        "amplitude_label": gates.get("primary_amplitude_label"),
        "maturity": maturity,
        "primary_metrics": primary,
        "secondary_metrics": secondary,
        "stability": stability,
        "research_verdict": verdict,
        "labeled_rows_used": int(len(labeled)),
        "unlabeled_preopen_rows": int(len(work) - len(labeled)),
        "production_influence": 0.0,
        "holdout_opened": False,
        "promotion_authority": False,
        "retuning_allowed": False,
        "module_global_mutation": False,
    }, slices, changes


def _android(payload: dict, generated_at: str) -> str:
    maturity = payload["maturity"]
    primary = payload.get("primary_metrics", {})
    secondary = payload.get("secondary_metrics", {})
    stability = payload.get("stability", {})
    values = maturity["values"]
    requirements = maturity["requirements"]
    lines = [
        "# TCT V24.4.2 — Validation PIT OHLC",
        "",
        f"Généré UTC : {generated_at}",
        f"Maturité : **{maturity['status']}**",
        f"Verdict recherche : **{payload['research_verdict']['status']}**",
        f"Stabilité : **{stability.get('status', 'N/A')}**",
        "Production influence : 0. Holdout fermé.",
        "",
        f"- PREOPEN étiquetés : {values['labeled_preopen_rows']} / {requirements['labeled_preopen_rows']}",
        f"- ISIN : {values['distinct_isins']} / {requirements['distinct_isins']}",
        f"- Séances : {values['observed_sessions']} / {requirements['observed_sessions']}",
        "",
        "## Amplitude prochaine séance",
        f"- Recall Top10 : {primary.get('top10_absolute_mover_recall', 'N/A')}",
        f"- Spearman : {primary.get('spearman_movement_score_vs_abs_return', 'N/A')}",
        f"- Précision mouvement significatif : {primary.get('significant_move_precision', 'N/A')}",
        f"- Recall mouvement significatif : {primary.get('significant_move_recall', 'N/A')}",
        f"- Range moyen : {secondary.get('mean_session_range_pct', 'N/A')} %",
        f"- Gap absolu moyen : {secondary.get('mean_open_gap_abs_pct', 'N/A')} %",
    ]
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    gates = json.loads((root / "config" / "TCT_V24_4_2_VALIDATION_GATES.json").read_text(encoding="utf-8"))
    catalyst_cfg = json.loads((root / "config" / "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    ledger_path = root / catalyst_cfg["state"]["catalyst_ledger_path"]
    ledger = base._read_csv(ledger_path)
    if not ledger.empty and "version" in ledger.columns:
        ledger = ledger[ledger["version"].astype(str) == "TCT_V24.4.2_NEXT_SESSION_CATALYST_CYCLE_SHADOW"].copy()
    payload, slices, changes = validate_ledger(ledger, gates)
    payload["generated_at_utc"] = generated_at
    payload["ledger_path"] = str(ledger_path.relative_to(root))
    payload["ledger_rows"] = int(len(ledger))
    auditdir = root / "outputs" / "audit"
    outdir = root / "outputs" / "daily_tct_ct"
    mobile = root / "outputs" / "mobile"
    for path in (auditdir, outdir, mobile):
        path.mkdir(parents=True, exist_ok=True)
    audit_path = auditdir / "TCT_V24_4_2_PIT_VALIDATION.json"
    slice_path = outdir / "TCT_V24_4_2_PIT_SLICES.csv"
    change_path = outdir / "TCT_V24_4_2_PREOPEN_POSTMARKET_CHANGES.csv"
    mobile_path = mobile / "ANDROID_TCT_V24_4_2_PIT_VALIDATION.md"
    base._write_csv(slices, slice_path)
    base._write_csv(changes, change_path)
    mobile_path.write_text(_android(payload, generated_at), encoding="utf-8")
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
