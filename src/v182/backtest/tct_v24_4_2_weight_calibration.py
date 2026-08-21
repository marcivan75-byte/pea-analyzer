from __future__ import annotations

from itertools import permutations
from pathlib import Path
import json

import pandas as pd

from v182.reporting.tct_v24_4_2_pit_validator import _spearman_without_scipy
from v182.reporting.tct_v24_4_pit_validator import _read_csv


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json"
GATES = "TCT_V24_4_2_VALIDATION_GATES.json"


def _candidate_weights(base: dict[str, float], step: float = 0.05) -> list[dict[str, float]]:
    keys = list(base)
    candidates = [dict(base)]
    for gain, loss in permutations(keys, 2):
        if base[loss] < step:
            continue
        trial = dict(base)
        trial[gain] += step
        trial[loss] -= step
        candidates.append({k: round(float(v), 8) for k, v in trial.items()})
    unique = {json.dumps(x, sort_keys=True): x for x in candidates}
    return list(unique.values())


def _score_complete_rows(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    columns = {
        "news_magnitude": "news_magnitude_score",
        "technical_impulse": "technical_impulse_score",
        "global_market_shock": "global_market_shock_score",
        "known_event_proximity": "known_event_proximity_score",
    }
    result = pd.Series(index=frame.index, dtype=float)
    for idx, row in frame.iterrows():
        values = {}
        complete = True
        for key, column in columns.items():
            value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
            if pd.isna(value):
                complete = False
                break
            values[key] = float(value)
        if complete:
            result.at[idx] = sum(values[key] * float(weights[key]) for key in weights)
    return result


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    gates = json.loads((root / "config" / GATES).read_text(encoding="utf-8"))
    ledger = _read_csv(root / cfg["state"]["catalyst_ledger_path"])
    if not ledger.empty:
        ledger = ledger[
            ledger.get("phase", pd.Series(index=ledger.index, dtype=object)).astype(str).eq("PREOPEN")
            & ledger.get("version", pd.Series(index=ledger.index, dtype=object)).astype(str).eq(cfg["version"])
        ].copy()
    label = pd.to_numeric(ledger.get("realized_session_abs_extreme_pct", pd.Series(index=ledger.index, dtype=float)), errors="coerce")
    labeled = ledger[label.notna()].copy()
    sessions = labeled.get("snapshot_generated_at_utc", pd.Series(dtype=object)).astype(str).str[:10].nunique() if not labeled.empty else 0
    mature = bool(
        len(labeled) >= int(gates["maturity"]["minimum_labeled_preopen_rows"])
        and labeled.get("isin", pd.Series(dtype=object)).astype(str).nunique() >= int(gates["maturity"]["minimum_distinct_isins"])
        and sessions >= int(gates["maturity"]["minimum_observed_sessions"])
    )
    payload = {
        "status": "NOT_MATURE_NO_CALIBRATION_CANDIDATE" if not mature else "CALIBRATION_RESEARCH_COMPLETE",
        "version": "TCT_V24.4.2_OFFLINE_WEIGHT_CALIBRATION_RESEARCH",
        "source_epoch": gates["validation_epoch"],
        "labeled_rows": int(len(labeled)),
        "observed_sessions": int(sessions),
        "runtime_config_modified": False,
        "candidate_auto_applied": False,
        "production_influence": 0.0,
        "holdout_opened": False,
        "promotion_authority": False,
        "note": "Exploratory candidate only; any semantic weight change requires a new preregistered epoch and explicit review.",
    }
    if mature:
        target = pd.to_numeric(labeled["realized_session_abs_extreme_pct"], errors="coerce")
        base_weights = {k: float(v) for k, v in cfg["movement_potential_weights"].items()}
        rows = []
        for weights in _candidate_weights(base_weights):
            score = _score_complete_rows(labeled, weights)
            metric = _spearman_without_scipy(score, target)
            rows.append({"weights": weights, "spearman_complete_cases": metric, "complete_rows": int(score.notna().sum())})
        rows.sort(key=lambda x: -999.0 if x["spearman_complete_cases"] is None else float(x["spearman_complete_cases"]), reverse=True)
        payload["baseline_weights"] = base_weights
        payload["candidates_tested"] = len(rows)
        payload["best_candidate"] = rows[0] if rows else None
        baseline = next((x for x in rows if x["weights"] == base_weights), None)
        payload["baseline_metric"] = None if baseline is None else baseline["spearman_complete_cases"]
        if rows and baseline and rows[0]["spearman_complete_cases"] is not None and baseline["spearman_complete_cases"] is not None:
            payload["best_improvement_vs_baseline"] = float(rows[0]["spearman_complete_cases"] - baseline["spearman_complete_cases"])
    output = root / "outputs" / "research" / "TCT_V24_4_2_CALIBRATION_CANDIDATE.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["output"] = str(output.relative_to(root))
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
