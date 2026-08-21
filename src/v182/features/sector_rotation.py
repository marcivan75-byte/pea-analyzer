from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


MISSING_TEXT = {"", "nan", "none", "n/a", "na", "unknown"}


def _num(frame: pd.DataFrame, field: str) -> pd.Series:
    if field not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[field], errors="coerce")


def _bool_pct(series: pd.Series) -> float | None:
    if series.empty:
        return None
    text = series.astype(str).str.strip().str.lower()
    values = pd.Series(np.nan, index=series.index, dtype=float)
    values.loc[text.isin({"true", "1", "yes", "oui"})] = 1.0
    values.loc[text.isin({"false", "0", "no", "non"})] = 0.0
    numeric = pd.to_numeric(series, errors="coerce")
    values = values.where(values.notna(), numeric.where(numeric.isin([0, 1])))
    return float(values.mean() * 100.0) if values.notna().any() else None


def _sector_series(frame: pd.DataFrame) -> pd.Series:
    result = pd.Series("NON_CLASSE", index=frame.index, dtype=object)
    for field in ("sector_yf", "sector_yahoo", "sector", "sector_bucket", "industry_yf"):
        if field not in frame.columns:
            continue
        raw = frame[field].astype(str).str.strip()
        valid = ~raw.str.lower().isin(MISSING_TEXT)
        result = result.where(~((result == "NON_CLASSE") & valid), raw)
    return result


def _pct_rank(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    return values.rank(method="average", pct=True, ascending=True) * 100.0


def _rotation_parameters(cfg: dict[str, Any] | None) -> dict[str, float]:
    rotation = (cfg or {}).get("context_overlay", {}).get("sector_rotation", {})
    return {
        "min_sector_size": float(rotation.get("min_sector_size", 3)),
        "catchup_distance_scale_pct": float(rotation.get("catchup_distance_scale_pct", 25.0)),
        "recovery_gate_cap": float(rotation.get("recovery_gate_cap", 50.0)),
        "candidate_market_min": float(rotation.get("candidate_market_min", 65.0)),
        "candidate_sector_min": float(rotation.get("candidate_sector_min", 65.0)),
        "candidate_action_min": float(rotation.get("candidate_action_min", 60.0)),
        "hhi_warning_threshold": float(rotation.get("hhi_warning_threshold", 2500.0)),
    }


def _rotation_hhi(sectors: pd.DataFrame) -> float | None:
    if sectors.empty or "sector_rotation_score" not in sectors.columns:
        return None
    positive = pd.to_numeric(sectors["sector_rotation_score"], errors="coerce").clip(lower=0).fillna(0.0)
    total = float(positive.sum())
    if total <= 0:
        return None
    shares = positive / total
    return float((shares.pow(2).sum()) * 10000.0)


def build_rotation_observations(
    actions: pd.DataFrame,
    *,
    cfg: dict[str, Any] | None = None,
) -> tuple[list[dict], pd.DataFrame, dict]:
    """Detect sector catch-up/rotation without hard-coding a market regime.

    A large distance from the 52-week high helps only when short-term recovery
    evidence exists. Sector-size, catch-up scale and recovery cap are governed
    parameters so they can be audited and changed only through versioned config.
    """
    if actions.empty:
        return [], pd.DataFrame(), {"status": "EMPTY"}

    params = _rotation_parameters(cfg)
    work = actions.copy()
    work["_sector"] = _sector_series(work)
    dist = _num(work, "distance_high_52w_pct")
    p1 = _num(work, "perf_1m_pct")
    p3 = _num(work, "perf_3m_pct")
    above50 = work["above_mm50"] if "above_mm50" in work.columns else pd.Series(pd.NA, index=work.index)
    above200 = work["above_mm200"] if "above_mm200" in work.columns else pd.Series(pd.NA, index=work.index)

    near_high_share = float((dist <= 5).mean() * 100.0) if dist.notna().any() else None
    breadth200 = _bool_pct(above200)
    market_high_score = None
    if near_high_share is not None and breadth200 is not None:
        near_component = min(100.0, near_high_share / 40.0 * 100.0)
        market_high_score = round(0.60 * near_component + 0.40 * breadth200, 4)

    rows: list[dict[str, Any]] = []
    minimum_sector_size = int(params["min_sector_size"])
    for sector, indices in work.groupby("_sector").groups.items():
        if sector == "NON_CLASSE" or len(indices) < minimum_sector_size:
            continue
        sector_dist = dist.loc[indices]
        sector_p1 = p1.loc[indices]
        sector_p3 = p3.loc[indices]
        med_dist = float(sector_dist.median()) if sector_dist.notna().any() else np.nan
        med_p1 = float(sector_p1.median()) if sector_p1.notna().any() else np.nan
        med_p3 = float(sector_p3.median()) if sector_p3.notna().any() else np.nan
        acceleration = med_p1 - med_p3 / 3.0 if np.isfinite(med_p1) and np.isfinite(med_p3) else np.nan
        rows.append(
            {
                "sector": sector,
                "n_actions": len(indices),
                "median_distance_high_52w_pct": med_dist,
                "median_perf_1m_pct": med_p1,
                "median_perf_3m_pct": med_p3,
                "momentum_acceleration": acceleration,
                "breadth_above_mm50_pct": _bool_pct(above50.loc[indices]),
                "breadth_above_mm200_pct": _bool_pct(above200.loc[indices]),
            }
        )

    sectors = pd.DataFrame(rows)
    if sectors.empty:
        return [], sectors, {
            "status": "NO_SECTORS",
            "market_high_regime_score": market_high_score,
            "min_sector_size": minimum_sector_size,
        }

    distance_scale = max(params["catchup_distance_scale_pct"], 1e-9)
    sectors["catchup_gap_score"] = (
        pd.to_numeric(sectors["median_distance_high_52w_pct"], errors="coerce") / distance_scale * 100.0
    ).clip(0, 100)
    sectors["momentum_rank"] = _pct_rank(sectors["median_perf_1m_pct"])
    sectors["acceleration_rank"] = _pct_rank(sectors["momentum_acceleration"])
    breadth = sectors[["breadth_above_mm50_pct", "breadth_above_mm200_pct"]].mean(axis=1, skipna=True)
    market_p1 = float(p1.median()) if p1.notna().any() else np.nan
    sectors["rs_inflection"] = pd.to_numeric(sectors["median_perf_1m_pct"], errors="coerce") - market_p1
    sectors["rs_rank"] = _pct_rank(sectors["rs_inflection"])
    sectors["sector_rotation_score"] = (
        0.30 * sectors["catchup_gap_score"]
        + 0.25 * sectors["momentum_rank"]
        + 0.20 * sectors["acceleration_rank"]
        + 0.15 * breadth
        + 0.10 * sectors["rs_rank"]
    )
    recovery = (
        pd.to_numeric(sectors["median_perf_1m_pct"], errors="coerce").gt(0)
        & (
            pd.to_numeric(sectors["momentum_acceleration"], errors="coerce").gt(0)
            | pd.to_numeric(sectors["breadth_above_mm50_pct"], errors="coerce").ge(50)
        )
    )
    sectors.loc[~recovery, "sector_rotation_score"] = sectors.loc[~recovery, "sector_rotation_score"].clip(
        upper=params["recovery_gate_cap"]
    )
    sectors["sector_rotation_score"] = sectors["sector_rotation_score"].clip(0, 100).round(4)
    sectors["recovery_gate"] = recovery

    sector_score = work["_sector"].map(sectors.set_index("sector")["sector_rotation_score"])
    sector_gap = work["_sector"].map(sectors.set_index("sector")["catchup_gap_score"])
    catch = pd.to_numeric(work.get("catchup_52w_score", pd.Series(np.nan, index=work.index)), errors="coerce")
    action_score = catch.where(sector_score.isna(), sector_score)
    both = catch.notna() & sector_score.notna()
    action_score.loc[both] = 0.55 * catch.loc[both] + 0.45 * sector_score.loc[both]

    candidate = pd.Series(False, index=work.index)
    if market_high_score is not None:
        candidate = (
            float(market_high_score) >= params["candidate_market_min"]
        ) & sector_score.ge(params["candidate_sector_min"]) & action_score.ge(params["candidate_action_min"])

    now = datetime.now(timezone.utc).isoformat()
    observations: list[dict[str, Any]] = []
    for idx in work.index:
        isin = str(work.at[idx, "isin"] if "isin" in work.columns else "").strip()
        values: dict[str, Any] = {
            "sector_rotation_score": sector_score.loc[idx],
            "sector_catchup_score": sector_gap.loc[idx],
            "action_catchup_score": action_score.loc[idx],
            "market_high_regime_score": market_high_score,
            "rotation_candidate_flag": bool(candidate.loc[idx]) if pd.notna(action_score.loc[idx]) else None,
        }
        for field, value in values.items():
            if value is None or (isinstance(value, (float, np.floating)) and not np.isfinite(float(value))):
                continue
            observations.append(
                {
                    "universe": "ACTION",
                    "isin": isin,
                    "field": field,
                    "value": bool(value) if isinstance(value, (bool, np.bool_)) else round(float(value), 4),
                    "source": "INTERNAL_PIT_SECTOR_ROTATION",
                    "collected_at": now,
                    "as_of": now[:10],
                    "evidence_level": "C",
                    "validation_status": "AUTO_MATCH",
                }
            )

    hhi = _rotation_hhi(sectors)
    diagnostic = {
        "status": "OK",
        "market_high_regime_score": market_high_score,
        "near_high_share_pct": near_high_share,
        "breadth_above_mm200_pct": breadth200,
        "sector_count": int(len(sectors)),
        "min_sector_size": minimum_sector_size,
        "catchup_distance_scale_pct": params["catchup_distance_scale_pct"],
        "recovery_gate_cap": params["recovery_gate_cap"],
        "rotation_hhi_10000": None if hhi is None else round(hhi, 4),
        "rotation_concentration_warning": bool(hhi is not None and hhi >= params["hhi_warning_threshold"]),
        "rotation_candidates_sectors": sectors.loc[sectors["sector_rotation_score"] >= params["candidate_sector_min"], "sector"].tolist(),
    }
    return observations, sectors, diagnostic
