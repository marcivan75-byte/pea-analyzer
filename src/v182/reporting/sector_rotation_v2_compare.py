from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import numpy as np
import pandas as pd


def _safe_numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def compare_v1_v2(v1: pd.DataFrame, v2: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compare current V1 baseline and V2 shadow without altering either model."""
    if v2.empty:
        return pd.DataFrame(), {"status": "NO_V2"}

    current = v2.copy()
    current["sector_key"] = current["sector"].astype(str).str.strip().str.casefold()
    if v1.empty or "sector" not in v1.columns:
        current["v1_sector_rotation_score"] = np.nan
        current["v1_rank"] = np.nan
        current["rank_delta_v2_minus_v1"] = np.nan
        return current, {"status": "NO_V1", "matched_sectors": 0, "v2_sectors": int(len(current))}

    baseline = v1.copy()
    baseline["sector_key"] = baseline["sector"].astype(str).str.strip().str.casefold()
    baseline["v1_sector_rotation_score"] = _safe_numeric(baseline, "sector_rotation_score")
    baseline["v1_rank"] = baseline["v1_sector_rotation_score"].rank(method="min", ascending=False)
    baseline = baseline[["sector_key", "v1_sector_rotation_score", "v1_rank"]].drop_duplicates("sector_key", keep="first")

    out = current.merge(baseline, on="sector_key", how="left")
    if "rank" in out.columns:
        out["rank_delta_v2_minus_v1"] = pd.to_numeric(out["rank"], errors="coerce") - pd.to_numeric(out["v1_rank"], errors="coerce")
    else:
        out["rank_delta_v2_minus_v1"] = np.nan
    out["score_delta_rars_minus_v1"] = _safe_numeric(out, "RARS") - _safe_numeric(out, "v1_sector_rotation_score")
    out["matched_v1"] = out["v1_sector_rotation_score"].notna()

    matched = out.loc[out["matched_v1"]].copy()
    spearman = None
    if len(matched) >= 3:
        corr = matched[["RARS", "v1_sector_rotation_score"]].corr(method="spearman").iloc[0, 1]
        spearman = None if pd.isna(corr) else round(float(corr), 6)

    summary = {
        "status": "OK",
        "v1_sectors": int(len(v1)),
        "v2_sectors": int(len(v2)),
        "matched_sectors": int(out["matched_v1"].sum()),
        "spearman_v1_vs_v2": spearman,
        "largest_rank_changes": (
            matched.assign(abs_rank_delta=matched["rank_delta_v2_minus_v1"].abs())
            .sort_values("abs_rank_delta", ascending=False)
            .head(10)[["sector", "v1_rank", "rank", "rank_delta_v2_minus_v1"]]
            .to_dict("records")
            if not matched.empty
            else []
        ),
        "promising_but_overvalued": out.loc[
            out.get("warnings", pd.Series([], dtype=object)).astype(str).str.contains("PROMISING_BUT_OVERVALUED", regex=False),
            "sector",
        ].tolist() if "warnings" in out.columns else [],
    }
    return out.drop(columns=["sector_key"]), summary


def write_comparison(
    v1_path: str | Path,
    v2_path: str | Path,
    output_csv: str | Path,
    output_json: str | Path,
) -> dict[str, Any]:
    v1p, v2p = Path(v1_path), Path(v2_path)
    v1 = pd.read_csv(v1p, sep=";", encoding="utf-8-sig", low_memory=False) if v1p.exists() else pd.DataFrame()
    v2 = pd.read_csv(v2p, sep=";", encoding="utf-8-sig", low_memory=False) if v2p.exists() else pd.DataFrame()
    comparison, summary = compare_v1_v2(v1, v2)
    out_csv, out_json = Path(output_csv), Path(output_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(out_csv, sep=";", index=False, encoding="utf-8-sig")
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary
