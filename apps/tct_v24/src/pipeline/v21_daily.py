"""Pipeline V21.3 aligné CDC TCT EXPLOSIF DATA-RICH."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.decision.engine import apply_decision_engine, extract_top20
from src.gates.synergies import evaluate_gates
from src.gates.universe_gate import apply_universe_gate
from src.scoring_v21.pillars import WEIGHTS_V21_3, compute_score_v21_3
from src.signals.squeeze_pressure import apply_squeeze_pressure
from src.utils.logger import setup_logger

logger = setup_logger("v21_pipeline")


def _num(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _freshness_from_row(row: pd.Series) -> dict:
    """Consume Free Capture freshness fields when present; retain backward compatibility."""
    generic = str(row.get("data_freshness") or "FRESH").upper()
    valid = {"FRESH", "AGING", "STALE_WARNING", "EXPIRED"}
    if generic not in valid:
        generic = "STALE_WARNING"
    out = {}
    for pillar in WEIGHTS_V21_3:
        value = str(row.get(f"freshness_{pillar}") or generic).upper()
        out[pillar] = value if value in valid else "STALE_WARNING"
    return out


def _save_table(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_parquet(path, index=False)
        return path
    except Exception as exc:
        csv = path.with_suffix(".csv")
        df.to_csv(csv, index=False)
        logger.warning(f"Parquet V21 indisponible ({exc}) → {csv}")
        return csv


def run_v21_pipeline(df: pd.DataFrame, output_dir: str = "output") -> pd.DataFrame:
    if df is None or df.empty:
        logger.error("DataFrame vide – abort")
        return pd.DataFrame()

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")
    run_id = now.strftime("%Y%m%dT%H%M%SZ")

    logger.info(f"V21.3 pipeline – {len(df)} titres en entrée")
    df = apply_universe_gate(df)
    logger.info(f"Universe Gate : {int((df['universe_status'] == 'REJECT').sum())} REJECT | "
                f"{int((df['universe_status'] == 'QUARANTINE').sum())} QUARANTINE")

    try:
        df = apply_squeeze_pressure(df)
        n_cand = int(df["squeeze_candidate"].sum()) if "squeeze_candidate" in df.columns else 0
        logger.info(f"Squeeze Pressure : {n_cand} candidats")
    except Exception as e:
        logger.warning(f"Squeeze Pressure ignoré : {e}")

    scores = df.apply(lambda r: compute_score_v21_3(r, freshness=_freshness_from_row(r)), axis=1)
    df["score_v21_3"] = scores.apply(lambda x: x["score_v21_3"])
    df["coverage_v21"] = scores.apply(lambda x: x["coverage"])
    df["n_pillars"] = scores.apply(lambda x: x["n_pillars_present"])
    df["pillars_detail"] = scores.apply(lambda x: json.dumps(x["pillars"], ensure_ascii=False))

    gates = df.apply(
        lambda r: evaluate_gates(r, _num(r.get("score_v21_3"), 0.0)),
        axis=1,
        result_type="expand",
    )
    for c in gates.columns:
        df[c] = gates[c]

    df = apply_decision_engine(df)
    top20 = extract_top20(df)

    snapshot = {
        "run_id": run_id,
        "ts_utc": now.isoformat(),
        "n_input": len(df),
        "n_coeur": int((df["decision_v21"] == "COEUR").sum()),
        "n_satellite": int((df["decision_v21"] == "SATELLITE").sum()),
        "n_scan": int((df["decision_v21"] == "SCAN").sum()),
        "n_reject": int((df["decision_v21"] == "REJECT").sum()),
        "n_quarantine_universe": int((df["universe_status"] == "QUARANTINE").sum()),
        "n_top20": len(top20),
        "score_version": "V21.3",
    }
    blob = json.dumps(snapshot, sort_keys=True).encode()
    snapshot["point_in_time_hash"] = hashlib.sha256(blob).hexdigest()[:16]

    _save_table(df, out_dir / f"v21_all_{day}.parquet")
    _save_table(top20, out_dir / f"v21_top20_{day}.parquet")
    top20.to_csv(out_dir / f"v21_top20_{day}.csv", index=False)

    pit_dir = Path("data/pit")
    pit_dir.mkdir(parents=True, exist_ok=True)
    with open(pit_dir / f"snapshot_{run_id}.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    logger.info(
        f"V21.3 terminé – CŒUR={snapshot['n_coeur']} SAT={snapshot['n_satellite']} "
        f"SCAN={snapshot['n_scan']} REJECT={snapshot['n_reject']} TOP20={snapshot['n_top20']}"
    )
    return df
