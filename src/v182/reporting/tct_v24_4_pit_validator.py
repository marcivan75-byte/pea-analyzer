from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
VERSION = "TCT_V24.4.0_PIT_VALIDATOR"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _snapshot_id(frame: pd.DataFrame) -> pd.Series:
    if "snapshot_generated_at_utc" in frame.columns:
        return frame["snapshot_generated_at_utc"].astype(str)
    if "snapshot_key" in frame.columns:
        return frame["snapshot_key"].astype(str).str.split("|", n=1).str[0]
    return pd.Series("UNKNOWN", index=frame.index, dtype=str)


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator / denominator)


def _mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return None if clean.empty else float(clean.mean())


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    pair = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": pd.to_numeric(y, errors="coerce")}).dropna()
    if len(pair) < 3 or pair["x"].nunique() < 2 or pair["y"].nunique() < 2:
        return None
    value = pair["x"].corr(pair["y"], method="spearman")
    if pd.isna(value):
        return None
    return float(value)


def _top_k_recall(frame: pd.DataFrame, score_col: str, *, k: int = 10) -> tuple[float | None, int, int]:
    hits = 0
    targets = 0
    if frame.empty:
        return None, hits, targets
    work = frame.copy()
    work["_snapshot"] = _snapshot_id(work)
    for _, group in work.groupby("_snapshot", dropna=False):
        group = group.copy()
        group["_score"] = _num(group, score_col)
        group["_abs"] = _num(group, "realized_abs_return_pct")
        group = group.dropna(subset=["_score", "_abs"])
        if len(group) < 2:
            continue
        kk = min(int(k), len(group))
        predicted = set(group.nlargest(kk, "_score").index)
        realized = set(group.nlargest(kk, "_abs").index)
        hits += len(predicted & realized)
        targets += len(realized)
    if targets == 0:
        return None, hits, targets
    return float(hits / targets), hits, targets


def _top_decile_lift(frame: pd.DataFrame, score_col: str) -> tuple[float | None, int]:
    if frame.empty:
        return None, 0
    work = frame.copy()
    work["_snapshot"] = _snapshot_id(work)
    selected_indices: list[int] = []
    for _, group in work.groupby("_snapshot", dropna=False):
        group = group.copy()
        group["_score"] = _num(group, score_col)
        group["_abs"] = _num(group, "realized_abs_return_pct")
        group = group.dropna(subset=["_score", "_abs"])
        if group.empty:
            continue
        n = max(1, int(math.ceil(len(group) * 0.10)))
        selected_indices.extend(group.nlargest(n, "_score").index.tolist())
    if not selected_indices:
        return None, 0
    all_abs = _num(work, "realized_abs_return_pct").dropna()
    selected_abs = _num(work.loc[selected_indices], "realized_abs_return_pct").dropna()
    if all_abs.empty or selected_abs.empty or float(all_abs.mean()) <= 0:
        return None, len(selected_abs)
    return float(selected_abs.mean() / all_abs.mean()), len(selected_abs)


def _false_high_potential_rate(frame: pd.DataFrame, threshold: float) -> tuple[float | None, int]:
    """False alert = high-potential score but realized move outside snapshot top quartile."""
    if frame.empty:
        return None, 0
    work = frame.copy()
    work["_snapshot"] = _snapshot_id(work)
    work["_score"] = _num(work, "movement_potential_score")
    work["_abs"] = _num(work, "realized_abs_return_pct")
    false_count = 0
    total = 0
    for _, group in work.groupby("_snapshot", dropna=False):
        group = group.dropna(subset=["_score", "_abs"]).copy()
        alerts = group[group["_score"] >= float(threshold)]
        if alerts.empty:
            continue
        top_q_n = max(1, int(math.ceil(len(group) * 0.25)))
        realized_top_q = set(group.nlargest(top_q_n, "_abs").index)
        total += len(alerts)
        false_count += sum(idx not in realized_top_q for idx in alerts.index)
    if total == 0:
        return None, 0
    return float(false_count / total), total


def _direction_hit(frame: pd.DataFrame, score_col: str, threshold: float) -> tuple[float | None, int]:
    if frame.empty:
        return None, 0
    score = _num(frame, score_col)
    realized = _num(frame, "realized_close_to_close_return_pct")
    mask = score.abs() >= float(threshold)
    work = pd.DataFrame({"score": score[mask], "realized": realized[mask]}).dropna()
    work = work[work["realized"] != 0]
    if work.empty:
        return None, 0
    hit = (np.sign(work["score"]) == np.sign(work["realized"])).astype(float)
    return float(hit.mean()), int(len(hit))


def _score_buckets(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    score = _num(work, "movement_potential_score")
    work["movement_score_bucket"] = pd.cut(
        score,
        bins=[-np.inf, 50.0, 70.0, 85.0, np.inf],
        labels=["LT50", "50_70", "70_85", "GE85"],
        right=False,
    ).astype(str)
    work["_abs"] = _num(work, "realized_abs_return_pct")
    rows: list[dict] = []
    for value, group in work.groupby("movement_score_bucket", dropna=False):
        clean = group["_abs"].dropna()
        rows.append(
            {
                "slice_type": "MOVEMENT_SCORE_BUCKET",
                "slice_value": str(value),
                "rows": int(len(group)),
                "labeled_rows": int(len(clean)),
                "mean_abs_return_pct": None if clean.empty else float(clean.mean()),
                "median_abs_return_pct": None if clean.empty else float(clean.median()),
            }
        )
    return pd.DataFrame(rows)


def _market_regime(value) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if not math.isfinite(x):
        return "UNKNOWN"
    if x >= 60:
        return "RISK_ON"
    if x <= 40:
        return "RISK_OFF"
    return "NEUTRAL"


def _earnings_bucket(value) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if not math.isfinite(x):
        return "UNKNOWN"
    if x < 0:
        return "PAST_OR_UNKNOWN"
    if x <= 2:
        return "0_2D"
    if x <= 7:
        return "3_7D"
    return "GT7D"


def _slice_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    work["_abs"] = _num(work, "realized_abs_return_pct")
    work["_ret"] = _num(work, "realized_close_to_close_return_pct")
    work["_direction"] = _num(work, "direction_bias_score")
    work["market_regime"] = work.get("global_risk_on_score", pd.Series(index=work.index, dtype=float)).map(_market_regime)
    work["earnings_proximity"] = work.get("days_to_earnings", pd.Series(index=work.index, dtype=float)).map(_earnings_bucket)

    dimensions = [
        ("news_event_types", "NEWS_EVENT_TYPE"),
        ("sector_yf", "SECTOR"),
        ("entry_state", "ENTRY_STATE"),
        ("market_regime", "MARKET_REGIME"),
        ("earnings_proximity", "EARNINGS_PROXIMITY"),
    ]
    rows: list[dict] = []
    for column, label in dimensions:
        if column not in work.columns:
            continue
        for value, group in work.groupby(column, dropna=False):
            abs_ret = group["_abs"].dropna()
            confident = group[group["_direction"].abs() >= 25].dropna(subset=["_direction", "_ret"])
            direction_hit = None
            if not confident.empty:
                direction_hit = float((np.sign(confident["_direction"]) == np.sign(confident["_ret"])).mean())
            rows.append(
                {
                    "slice_type": label,
                    "slice_value": str(value),
                    "rows": int(len(group)),
                    "labeled_rows": int(len(abs_ret)),
                    "mean_abs_return_pct": None if abs_ret.empty else float(abs_ret.mean()),
                    "direction_calls": int(len(confident)),
                    "direction_hit_rate": direction_hit,
                }
            )
    buckets = _score_buckets(work)
    if not buckets.empty:
        return pd.concat([pd.DataFrame(rows), buckets], ignore_index=True, sort=False)
    return pd.DataFrame(rows)


def _preopen_postmarket_changes(all_ledger: pd.DataFrame) -> pd.DataFrame:
    if all_ledger.empty or "phase" not in all_ledger.columns or "isin" not in all_ledger.columns:
        return pd.DataFrame()
    work = all_ledger.copy()
    work["snapshot_day"] = _snapshot_id(work).astype(str).str[:10]
    pre = work[work["phase"].astype(str) == "PREOPEN"].copy()
    post = work[work["phase"].astype(str) == "POSTMARKET"].copy()
    if pre.empty or post.empty:
        return pd.DataFrame()
    fields = ["movement_potential_score", "direction_bias_score"]
    pre_keep = pre[["snapshot_day", "isin", *[c for c in fields if c in pre.columns]]].copy()
    post_keep = post[["snapshot_day", "isin", *[c for c in fields if c in post.columns]]].copy()
    pre_keep = pre_keep.rename(columns={c: f"pre_{c}" for c in fields if c in pre_keep.columns})
    post_keep = post_keep.rename(columns={c: f"post_{c}" for c in fields if c in post_keep.columns})
    merged = pre_keep.merge(post_keep, on=["snapshot_day", "isin"], how="inner")
    if merged.empty:
        return merged
    if "pre_movement_potential_score" in merged.columns and "post_movement_potential_score" in merged.columns:
        merged["movement_score_change_post_to_pre"] = _num(merged, "pre_movement_potential_score") - _num(merged, "post_movement_potential_score")
    if "pre_direction_bias_score" in merged.columns and "post_direction_bias_score" in merged.columns:
        merged["direction_score_change_post_to_pre"] = _num(merged, "pre_direction_bias_score") - _num(merged, "post_direction_bias_score")
    return merged


def _maturity(frame: pd.DataFrame, gates: dict) -> dict:
    maturity = gates["maturity"]
    movement = _num(frame, "movement_potential_score")
    direction = _num(frame, "direction_bias_score")
    snapshot_days = _snapshot_id(frame).astype(str).str[:10]
    values = {
        "labeled_preopen_rows": int(len(frame)),
        "distinct_isins": int(frame.get("isin", pd.Series(dtype=object)).astype(str).nunique()) if not frame.empty else 0,
        "high_potential_predictions": int((movement >= 70.0).sum()),
        "direction_calls": int((direction.abs() >= 25.0).sum()),
        "observed_sessions": int(snapshot_days.nunique()) if not frame.empty else 0,
    }
    requirements = {
        "labeled_preopen_rows": int(maturity["minimum_labeled_preopen_rows"]),
        "distinct_isins": int(maturity["minimum_distinct_isins"]),
        "high_potential_predictions": int(maturity["minimum_high_potential_predictions"]),
        "direction_calls": int(maturity["minimum_direction_calls"]),
        "observed_sessions": int(maturity["minimum_observed_sessions"]),
    }
    passed = {key: values[key] >= requirements[key] for key in requirements}
    return {
        "status": "MATURE_FOR_REVIEW_NOT_PROMOTION" if all(passed.values()) else "NOT_MATURE_ACCUMULATING_PIT",
        "values": values,
        "requirements": requirements,
        "passed": passed,
        "all_maturity_gates_passed": bool(all(passed.values())),
    }


def _primary_metrics(frame: pd.DataFrame) -> dict:
    v244_recall, v244_hits, v244_targets = _top_k_recall(frame, "movement_potential_score", k=10)
    tech_recall, _, _ = _top_k_recall(frame, "technical_impulse_score", k=10)
    v244_decile, v244_decile_rows = _top_decile_lift(frame, "movement_potential_score")
    tech_decile, _ = _top_decile_lift(frame, "technical_impulse_score")
    v244_spearman = _spearman(_num(frame, "movement_potential_score"), _num(frame, "realized_abs_return_pct"))
    tech_spearman = _spearman(_num(frame, "technical_impulse_score"), _num(frame, "realized_abs_return_pct"))
    return {
        "top10_absolute_mover_recall": v244_recall,
        "top10_absolute_mover_recall_hits": v244_hits,
        "top10_absolute_mover_recall_targets": v244_targets,
        "technical_only_top10_recall": tech_recall,
        "top10_recall_improvement_pp_vs_technical": None if v244_recall is None or tech_recall is None else (v244_recall - tech_recall) * 100.0,
        "top_decile_absolute_return_lift": v244_decile,
        "top_decile_rows": v244_decile_rows,
        "technical_only_top_decile_lift": tech_decile,
        "top_decile_lift_improvement_vs_technical": None if v244_decile is None or tech_decile is None else v244_decile - tech_decile,
        "spearman_movement_score_vs_abs_return": v244_spearman,
        "technical_only_spearman_vs_abs_return": tech_spearman,
        "spearman_improvement_vs_technical": None if v244_spearman is None or tech_spearman is None else v244_spearman - tech_spearman,
    }


def _secondary_metrics(frame: pd.DataFrame, high_threshold: float, direction_threshold: float) -> dict:
    direction_hit, calls = _direction_hit(frame, "direction_bias_score", direction_threshold)
    technical_hit, technical_calls = _direction_hit(frame, "technical_direction_score", direction_threshold)
    states = frame.get("catalyst_state", pd.Series(index=frame.index, dtype=object)).astype(str)
    returns = _num(frame, "realized_close_to_close_return_pct")
    up_mask = states == "UP_CATALYST_SHADOW"
    down_mask = states == "DOWN_CATALYST_SHADOW"
    conflict_mask = states == "NEWS_CONFLICT_SHADOW"
    false_rate, high_count = _false_high_potential_rate(frame, high_threshold)
    return {
        "direction_hit_rate_confident_biases": direction_hit,
        "direction_calls": calls,
        "technical_only_direction_hit_rate": technical_hit,
        "technical_only_direction_calls": technical_calls,
        "direction_hit_improvement_pp_vs_technical": None if direction_hit is None or technical_hit is None else (direction_hit - technical_hit) * 100.0,
        "up_catalyst_positive_rate": _mean((returns[up_mask] > 0).astype(float)) if up_mask.any() else None,
        "up_catalyst_rows": int(up_mask.sum()),
        "down_catalyst_negative_rate": _mean((returns[down_mask] < 0).astype(float)) if down_mask.any() else None,
        "down_catalyst_rows": int(down_mask.sum()),
        "news_conflict_mean_abs_return_pct": _mean(_num(frame[conflict_mask], "realized_abs_return_pct")) if conflict_mask.any() else None,
        "news_conflict_rows": int(conflict_mask.sum()),
        "false_high_potential_rate": false_rate,
        "high_potential_rows_evaluated": high_count,
        "false_high_potential_definition": "SCORE_GE_HIGH_THRESHOLD_BUT_REALIZED_ABS_MOVE_OUTSIDE_SNAPSHOT_TOP_QUARTILE",
    }


def _research_verdict(primary: dict, secondary: dict, maturity: dict, gates: dict) -> dict:
    acceptance = gates.get("acceptance", {})
    if not maturity["all_maturity_gates_passed"]:
        return {
            "status": "NOT_EVALUABLE_BEFORE_MATURITY",
            "movement_validation": "NOT_EVALUABLE",
            "direction_validation": "NOT_EVALUABLE",
            "promotion_authority": False,
        }

    checks = {
        "top10_recall_improvement": (
            primary.get("top10_recall_improvement_pp_vs_technical") is not None
            and primary["top10_recall_improvement_pp_vs_technical"] >= float(acceptance["minimum_top10_recall_improvement_pp_vs_technical"])
        ),
        "top_decile_lift_improvement": (
            primary.get("top_decile_lift_improvement_vs_technical") is not None
            and primary["top_decile_lift_improvement_vs_technical"] >= float(acceptance["minimum_top_decile_lift_improvement_vs_technical"])
        ),
        "spearman_improvement": (
            primary.get("spearman_improvement_vs_technical") is not None
            and primary["spearman_improvement_vs_technical"] >= float(acceptance["minimum_spearman_improvement_vs_technical"])
        ),
    }
    required_primary = int(acceptance["minimum_primary_improvement_checks_passed"])
    movement_pass = sum(bool(x) for x in checks.values()) >= required_primary
    false_rate = secondary.get("false_high_potential_rate")
    if false_rate is None or false_rate > float(acceptance["maximum_false_high_potential_rate"]):
        movement_pass = False

    direction_hit = secondary.get("direction_hit_rate_confident_biases")
    direction_improvement = secondary.get("direction_hit_improvement_pp_vs_technical")
    direction_pass = bool(
        direction_hit is not None
        and direction_hit >= float(acceptance["minimum_direction_hit_rate"])
        and (direction_improvement is None or direction_improvement >= float(acceptance["minimum_direction_hit_improvement_pp_vs_technical"]))
    )
    return {
        "status": "RESEARCH_CRITERIA_MET" if movement_pass else "RESEARCH_CRITERIA_NOT_MET",
        "movement_validation": "PASS" if movement_pass else "FAIL",
        "direction_validation": "PASS" if direction_pass else "FAIL",
        "primary_improvement_checks": checks,
        "primary_improvement_checks_passed": int(sum(bool(x) for x in checks.values())),
        "promotion_authority": False,
        "note": "PASS permits manual preregistered review only; it is not a production promotion.",
    }


def validate_ledger(ledger: pd.DataFrame, gates: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    if ledger is None or ledger.empty:
        maturity = _maturity(pd.DataFrame(), gates)
        payload = {
            "version": VERSION,
            "pit_mechanics": "VALIDATED_BY_CODE_TESTS_ONLY",
            "maturity": maturity,
            "primary_metrics": {},
            "secondary_metrics": {},
            "research_verdict": {
                "status": "NOT_EVALUABLE_BEFORE_MATURITY",
                "movement_validation": "NOT_EVALUABLE",
                "direction_validation": "NOT_EVALUABLE",
                "promotion_authority": False,
            },
        }
        return payload, pd.DataFrame(), pd.DataFrame()

    work = ledger.copy()
    work = work[work.get("phase", pd.Series(index=work.index, dtype=object)).astype(str) == str(gates.get("target_phase", "PREOPEN"))].copy()
    work["realized_abs_return_pct"] = _num(work, "realized_abs_return_pct")
    labeled = work.dropna(subset=["realized_abs_return_pct"]).copy()
    maturity = _maturity(labeled, gates)
    primary = _primary_metrics(labeled)
    high_threshold = 70.0
    direction_threshold = 25.0
    secondary = _secondary_metrics(labeled, high_threshold, direction_threshold)
    verdict = _research_verdict(primary, secondary, maturity, gates)
    slices = _slice_rows(labeled)
    changes = _preopen_postmarket_changes(ledger)
    payload = {
        "version": VERSION,
        "pit_mechanics": "VALIDATED_BY_CODE_TESTS_AND_CAUSAL_LEDGER_DESIGN",
        "maturity": maturity,
        "primary_metrics": primary,
        "secondary_metrics": secondary,
        "research_verdict": verdict,
        "labeled_rows_used": int(len(labeled)),
        "unlabeled_preopen_rows": int(len(work) - len(labeled)),
        "production_influence": 0.0,
        "holdout_opened": False,
        "promotion_authority": False,
        "retuning_allowed": False,
    }
    return payload, slices, changes


def _android(payload: dict, generated_at: str) -> str:
    maturity = payload["maturity"]
    verdict = payload["research_verdict"]
    values = maturity["values"]
    requirements = maturity["requirements"]
    lines = [
        "# TCT V24.4 — Validation PIT",
        "",
        f"Généré UTC : {generated_at}",
        f"Maturité : **{maturity['status']}**",
        f"Verdict recherche : **{verdict['status']}**",
        "Production influence : 0. Holdout fermé. Aucun retuning automatique.",
        "",
        "## Échantillon",
        "",
        f"- Observations PREOPEN étiquetées : {values['labeled_preopen_rows']} / {requirements['labeled_preopen_rows']}",
        f"- ISIN distincts : {values['distinct_isins']} / {requirements['distinct_isins']}",
        f"- Alertes fort potentiel : {values['high_potential_predictions']} / {requirements['high_potential_predictions']}",
        f"- Appels directionnels : {values['direction_calls']} / {requirements['direction_calls']}",
        f"- Séances : {values['observed_sessions']} / {requirements['observed_sessions']}",
    ]
    primary = payload.get("primary_metrics", {})
    if primary:
        lines.extend(["", "## Mesures actuelles", ""])
        for label, key in [
            ("Recall Top 10", "top10_absolute_mover_recall"),
            ("Lift décile supérieur", "top_decile_absolute_return_lift"),
            ("Spearman score/amplitude", "spearman_movement_score_vs_abs_return"),
        ]:
            value = primary.get(key)
            lines.append(f"- {label} : {'N/A' if value is None else f'{float(value):.3f}'}")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    gates = json.loads((root / "config" / "TCT_V24_4_0_VALIDATION_GATES.json").read_text(encoding="utf-8"))
    catalyst_cfg = json.loads((root / "config" / "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    ledger_path = root / catalyst_cfg["state"]["catalyst_ledger_path"]
    ledger = _read_csv(ledger_path)
    payload, slices, changes = validate_ledger(ledger, gates)
    payload["generated_at_utc"] = generated_at
    payload["ledger_path"] = str(ledger_path.relative_to(root))
    payload["ledger_rows"] = int(len(ledger))

    auditdir = root / "outputs" / "audit"
    outdir = root / "outputs" / "daily_tct_ct"
    mobile = root / "outputs" / "mobile"
    auditdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)

    audit_path = auditdir / "TCT_V24_4_0_PIT_VALIDATION.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    slice_path = outdir / "TCT_V24_4_0_PIT_SLICES.csv"
    change_path = outdir / "TCT_V24_4_0_PREOPEN_POSTMARKET_CHANGES.csv"
    _write_csv(slices, slice_path)
    _write_csv(changes, change_path)
    mobile_path = mobile / "ANDROID_TCT_V24_4_PIT_VALIDATION.md"
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
