from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
VERSION = "TCT_V24.2.1_SHADOW_ANALYTICS"


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


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _profit_factor(returns: pd.Series) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return None
    gains = float(clean[clean > 0].sum())
    losses = float(clean[clean < 0].sum())
    if losses >= 0:
        return None
    return gains / abs(losses)


def _clock_bucket(value) -> str:
    text = str(value or "")
    if "T" not in text:
        return "NO_ENTRY_TIME"
    try:
        hour = int(text.split("T", 1)[1][:2])
    except (TypeError, ValueError):
        return "NO_ENTRY_TIME"
    if hour < 10:
        return "BEFORE_10"
    if hour < 12:
        return "10_12"
    if hour < 14:
        return "12_14"
    if hour < 16:
        return "14_16"
    return "AFTER_16"


def _derive_dimensions(observations: pd.DataFrame) -> pd.DataFrame:
    out = observations.copy()
    if out.empty:
        return out
    out["session_date"] = out["session_date"].astype(str)
    out["source_signal_date"] = out["source_signal_date"].astype(str)
    out["signal_key"] = out["signal_key"].astype(str)
    out["session_lag"] = (
        out.sort_values(["signal_key", "session_date"])
        .groupby("signal_key")["session_date"]
        .rank(method="dense")
        .astype("Int64")
    )
    out["entry_clock_bucket"] = out.get("signal_time", pd.Series(index=out.index, dtype=object)).map(_clock_bucket)
    score = _numeric(out, "score")
    out["score_bucket"] = pd.cut(
        score,
        bins=[-np.inf, 72.0, 82.0, np.inf],
        labels=["LT72", "72_82", "GE82"],
        right=False,
    ).astype(str)
    return out


def _metric_row(frame: pd.DataFrame, group_type: str, group_value: str) -> dict:
    entries = frame[frame.get("status", pd.Series(index=frame.index, dtype=object)).astype(str) == "CAUSAL_ENTRY_EVENT"].copy()
    returns = _numeric(entries, "close_return_pct").dropna()
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    mfe = _numeric(entries, "mfe_to_close_pct")
    mae = _numeric(entries, "mae_to_close_pct")
    return {
        "analysis_version": VERSION,
        "group_type": group_type,
        "group_value": str(group_value),
        "observations": int(len(frame)),
        "entry_events": int(len(entries)),
        "entry_rate_pct": round(len(entries) / len(frame) * 100.0, 4) if len(frame) else np.nan,
        "distinct_isins": int(entries.get("isin", pd.Series(dtype=object)).astype(str).nunique()) if not entries.empty else 0,
        "gross_expectancy_close_pct": round(float(returns.mean()) * 100.0, 5) if not returns.empty else np.nan,
        "median_close_return_pct": round(float(returns.median()) * 100.0, 5) if not returns.empty else np.nan,
        "win_rate_pct": round(float((returns > 0).mean()) * 100.0, 4) if not returns.empty else np.nan,
        "avg_gain_pct": round(float(wins.mean()) * 100.0, 5) if not wins.empty else np.nan,
        "avg_loss_pct": round(float(losses.mean()) * 100.0, 5) if not losses.empty else np.nan,
        "profit_factor_gross": _profit_factor(returns),
        "avg_mfe_pct": round(float(mfe.mean()) * 100.0, 5) if mfe.notna().any() else np.nan,
        "avg_mae_pct": round(float(mae.mean()) * 100.0, 5) if mae.notna().any() else np.nan,
        "worst_close_return_pct": round(float(returns.min()) * 100.0, 5) if not returns.empty else np.nan,
        "best_close_return_pct": round(float(returns.max()) * 100.0, 5) if not returns.empty else np.nan,
        "net_expectancy_status": "NOT_COMPUTED_FRICTION_INCOMPLETE",
        "promotion_authority": False,
    }


def build_metrics(observations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return enriched observations and descriptive metrics without retuning."""
    enriched = _derive_dimensions(observations)
    if enriched.empty:
        return enriched, pd.DataFrame()

    rows = [_metric_row(enriched, "ALL", "ALL")]
    dimensions = [
        ("setup", "SETUP"),
        ("source_decision", "SOURCE_T1_T2"),
        ("session_lag", "SESSION_LAG"),
        ("entry_clock_bucket", "ENTRY_CLOCK"),
        ("score_bucket", "SCORE_BUCKET"),
        ("shadow_state", "SHADOW_STATE"),
    ]
    for column, group_type in dimensions:
        if column not in enriched.columns:
            continue
        for value, group in enriched.groupby(column, dropna=False):
            rows.append(_metric_row(group, group_type, str(value)))
    return enriched, pd.DataFrame(rows)


def _maturity(enriched: pd.DataFrame, cfg: dict) -> dict:
    analysis = cfg["analysis"]
    if enriched.empty:
        return {
            "status": "ACCUMULATING_NO_OBSERVATIONS",
            "entry_events": 0,
            "distinct_isins": 0,
            "setups_with_minimum_sample": 0,
            "promotion_authority": False,
            "retuning_allowed": False,
        }
    entries = enriched[enriched["status"].astype(str) == "CAUSAL_ENTRY_EVENT"].copy()
    n_entries = int(len(entries))
    distinct = int(entries["isin"].astype(str).nunique()) if "isin" in entries.columns and not entries.empty else 0
    per_setup = entries.groupby("setup").size() if "setup" in entries.columns and not entries.empty else pd.Series(dtype=int)
    setup_min = int(analysis["minimum_entry_events_per_setup_for_review"])
    setups_ready = int((per_setup >= setup_min).sum()) if not per_setup.empty else 0
    descriptive_min = int(analysis["minimum_entry_events_for_descriptive_metrics"])
    review_min = int(analysis["minimum_entry_events_for_candidate_review"])
    isin_min = int(analysis["minimum_distinct_isins_for_candidate_review"])

    if n_entries < descriptive_min:
        status = "ACCUMULATING_EARLY"
    elif n_entries < review_min:
        status = "ACCUMULATING_DESCRIPTIVE_ONLY"
    elif distinct < isin_min:
        status = "SAMPLE_DIVERSITY_INSUFFICIENT"
    elif setups_ready == 0:
        status = "PER_SETUP_SAMPLE_INSUFFICIENT"
    else:
        status = "READY_FOR_PRE_REGISTERED_REVIEW_NOT_PROMOTION"
    return {
        "status": status,
        "entry_events": n_entries,
        "distinct_isins": distinct,
        "entries_by_setup": {str(k): int(v) for k, v in per_setup.to_dict().items()},
        "setups_with_minimum_sample": setups_ready,
        "minimum_entry_events_for_descriptive_metrics": descriptive_min,
        "minimum_entry_events_for_candidate_review": review_min,
        "minimum_entry_events_per_setup_for_review": setup_min,
        "minimum_distinct_isins_for_candidate_review": isin_min,
        "promotion_authority": False,
        "retuning_allowed": False,
    }


def _android(metrics: pd.DataFrame, maturity: dict, generated_at: str) -> str:
    lines = [
        "# TCT V24.2.1 — Analytics SHADOW",
        "",
        f"Généré UTC : {generated_at}",
        f"Maturité : **{maturity['status']}**",
        f"Entrées SHADOW observées : **{maturity['entry_events']}**",
        f"ISIN distincts : **{maturity['distinct_isins']}**",
        "Aucune autorité de promotion, aucun retuning automatique.",
        "",
    ]
    if metrics.empty:
        lines.append("Pas encore de métriques exploitables.")
        return "\n".join(lines) + "\n"
    all_row = metrics[(metrics["group_type"] == "ALL") & (metrics["group_value"] == "ALL")]
    if not all_row.empty:
        row = all_row.iloc[0]
        exp = row.get("gross_expectancy_close_pct")
        wr = row.get("win_rate_pct")
        pf = row.get("profit_factor_gross")
        lines.extend(
            [
                "## Ensemble",
                "",
                f"- Expectancy brute à la clôture : {'N/A' if pd.isna(exp) else f'{float(exp):.3f}%'}",
                f"- Taux positif : {'N/A' if pd.isna(wr) else f'{float(wr):.1f}%'}",
                f"- Profit factor brut : {'N/A' if pd.isna(pf) else f'{float(pf):.2f}'}",
                "- Expectancy nette : non calculée tant que la friction n'est pas suffisamment observée.",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    cfg = json.loads((root / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").read_text(encoding="utf-8"))
    observation_path = root / cfg["signal_bridge"]["observation_ledger_path"]
    observations = _read_csv(observation_path)
    enriched, metrics = build_metrics(observations)
    maturity = _maturity(enriched, cfg)

    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)

    metrics_path = outdir / "TCT_INTRADAY_V24_2_1_ANALYTICS.csv"
    _write_csv(metrics, metrics_path)
    enriched_path = outdir / "TCT_INTRADAY_V24_2_1_ENRICHED_OBSERVATIONS.csv"
    _write_csv(enriched, enriched_path)
    mobile_path = mobile / "ANDROID_TCT_INTRADAY_ANALYTICS.md"
    mobile_path.write_text(_android(metrics, maturity, generated_at), encoding="utf-8")

    all_metrics = {}
    if not metrics.empty:
        row = metrics[(metrics["group_type"] == "ALL") & (metrics["group_value"] == "ALL")]
        if not row.empty:
            all_metrics = {
                key: (None if (isinstance(value, float) and (math.isnan(value) or math.isinf(value))) else value)
                for key, value in row.iloc[0].to_dict().items()
            }

    payload = {
        "status": "SUCCESS_SHADOW_ANALYTICS",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "maturity": maturity,
        "all_metrics": all_metrics,
        "gross_metrics_only": True,
        "net_expectancy_computed": False,
        "reason_net_not_computed": "FRICTION_COVERAGE_INCOMPLETE",
        "promotion_authority": False,
        "retuning_allowed": False,
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_execution_influence": 0.0,
        "stop_loss_influence": 0.0,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "outputs": {
            "metrics": str(metrics_path.relative_to(root)),
            "enriched_observations": str(enriched_path.relative_to(root)),
            "android": str(mobile_path.relative_to(root)),
        },
    }
    audit_path = auditdir / "TCT_INTRADAY_V24_2_1_ANALYTICS.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
