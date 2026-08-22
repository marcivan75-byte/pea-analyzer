from __future__ import annotations

from pathlib import Path
import argparse
import json
import os

import pandas as pd

from v182.sources.boursorama_public import collect_action_snapshots_cached

ROOT = Path(__file__).resolve().parents[3]


def _read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    for sep in (";", ",", "\t"):
        try:
            frame = pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
            if len(frame.columns) > 1:
                return frame
        except (OSError, ValueError, pd.errors.ParserError):
            continue
    return pd.DataFrame()


def _first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path.exists()), None)


def _pivot_observations(observations: list[dict]) -> pd.DataFrame:
    if not observations:
        return pd.DataFrame()
    frame = pd.DataFrame(observations)
    if frame.empty or not {"isin", "field", "value"}.issubset(frame.columns):
        return pd.DataFrame()
    return frame.pivot_table(index="isin", columns="field", values="value", aggfunc="last").reset_index()


def _numeric_pair(frame: pd.DataFrame, left: str, right: str) -> pd.DataFrame:
    if left not in frame.columns or right not in frame.columns:
        return pd.DataFrame(columns=[left, right])
    out = frame[[left, right]].copy()
    out[left] = pd.to_numeric(out[left], errors="coerce")
    out[right] = pd.to_numeric(out[right], errors="coerce")
    return out.dropna()


def _comparison(frame: pd.DataFrame, left: str, right: str) -> dict:
    pair = _numeric_pair(frame, left, right)
    if pair.empty:
        return {"paired_rows": 0, "mae": None, "median_abs_error": None, "correlation": None}
    delta = (pair[left] - pair[right]).abs()
    correlation = pair[left].corr(pair[right]) if len(pair) >= 2 else None
    return {
        "paired_rows": int(len(pair)),
        "mae": round(float(delta.mean()), 6),
        "median_abs_error": round(float(delta.median()), 6),
        "correlation": round(float(correlation), 6) if pd.notna(correlation) else None,
    }


def build_equivalence_audit(actions: pd.DataFrame, observations: list[dict]) -> dict:
    shadow = _pivot_observations(observations)
    if shadow.empty or "isin" not in actions.columns:
        return {"status": "NO_SHADOW_DATA", "shadow_rows": int(len(shadow)), "comparisons": {}}
    merged = actions.merge(shadow, on="isin", how="inner", suffixes=("", "_shadow"))
    comparisons = {}
    existing_score = next((field for field in ("consensus_score", "consensus_score_yf") if field in merged.columns), None)
    existing_delta = next((field for field in ("consensus_delta_4w", "consensus_delta", "consensus_delta_yf") if field in merged.columns), None)
    existing_analysts = next((field for field in ("n_analysts", "n_analysts_yf") if field in merged.columns), None)
    existing_upside = next((field for field in ("target_upside_pct", "upside_pct", "upside_pct_yf") if field in merged.columns), None)
    if existing_score:
        comparisons["consensus_score_1_to_5"] = _comparison(merged, "boursorama_consensus_score", existing_score)
    if existing_delta:
        comparisons["consensus_delta_4w_0_to_100"] = _comparison(merged, "boursorama_consensus_delta_4w", existing_delta)
    if existing_analysts:
        comparisons["analyst_count"] = _comparison(merged, "boursorama_n_analysts", existing_analysts)
    if existing_upside:
        comparisons["target_upside_median_vs_current_method_DIAGNOSTIC_ONLY"] = _comparison(
            merged, "boursorama_target_upside_pct", existing_upside
        )
    return {
        "status": "SHADOW_ONLY_NO_DECISION_INFLUENCE",
        "shadow_rows": int(len(shadow)),
        "paired_master_rows": int(len(merged)),
        "comparisons": comparisons,
        "target_comparison_semantics": "DIAGNOSTIC_ONLY_BOURSORAMA_MEDIAN_TARGET_MUST_NOT_REPLACE_MEAN_WITHOUT_POLICY_DECISION",
    }


def run(root: Path = ROOT) -> dict:
    cfg_path = root / "config" / "BOURSORAMA_PUBLIC_V21_14.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    action_cfg = cfg.get("actions", {})
    inputs = root / "inputs"
    outputs = root / "outputs"
    audit_dir = outputs / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    actions_path = _first_existing(
        [outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", inputs / "V18.2_PEA_ACTIONS_MASTER.csv"]
    )
    actions = _read_table(actions_path) if actions_path else pd.DataFrame()
    if actions.empty:
        result = {"status": "NO_ACTION_MASTER", "decision_influence": False}
        (audit_dir / "BOURSORAMA_PUBLIC_SHADOW_METRICS.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

    profile = os.environ.get("PEA_RUN_PROFILE", "").strip().upper()
    daily = profile == "DAILY_TACTICAL"
    cache_path = root / str(cfg.get("cache_path", "state/provenance/source_cache/BOURSORAMA_PUBLIC_V1.json"))
    snapshot = collect_action_snapshots_cached(
        actions,
        cache_path,
        refresh_budget=int(action_cfg.get("weekly_refresh_budget", 120)),
        ttl_hours=float(action_cfg.get("ttl_hours", 48)),
        request_start_interval_seconds=float(action_cfg.get("request_start_interval_seconds", 1.0)),
        timeout_seconds=float(action_cfg.get("timeout_seconds", 15)),
        refresh_due=not daily,
        bootstrap_missing=not daily,
        include_key_figures=bool(action_cfg.get("key_figures_enabled", False)),
    )

    observations = pd.DataFrame(snapshot.observations)
    failures = pd.DataFrame(snapshot.failures)
    observations.to_csv(audit_dir / "BOURSORAMA_PUBLIC_SHADOW_OBSERVATIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    failures.to_csv(audit_dir / "BOURSORAMA_PUBLIC_SHADOW_FAILURES.csv", sep=";", index=False, encoding="utf-8-sig")
    equivalence = build_equivalence_audit(actions, snapshot.observations)
    (audit_dir / "BOURSORAMA_PUBLIC_EQUIVALENCE.json").write_text(
        json.dumps(equivalence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = {
        **snapshot.metrics,
        "status": "SHADOW_ONLY_NO_DECISION_INFLUENCE",
        "profile": profile or "UNSPECIFIED",
        "actions_input": str(actions_path.relative_to(root)) if actions_path else None,
        "equivalence_audit": equivalence,
        "decision_influence": False,
        "existing_provider_suppression": False,
    }
    (audit_dir / "BOURSORAMA_PUBLIC_SHADOW_METRICS.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Boursorama public shadow snapshot audit")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    result = run(Path(args.root))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
