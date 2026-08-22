from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DEFAULT_LIMITS = {"TCT": 80, "CT": 250, "MT": 500}
PRIORITY_STATE_COLUMNS = ["asset_class", "horizon", "isin", "score", "status", "decision", "generated_at_utc"]


def _read_decisions(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except Exception:
        return pd.DataFrame()


def write_horizon_priority_state(
    decisions: pd.DataFrame,
    path: Path,
    *,
    generated_at_utc: str | None = None,
) -> dict:
    """Persist the minimal prior-run ranking needed by the next enrichment run.

    This state is operational cache metadata only. It does not replace Committee
    outputs and cannot alter current-run scores, decisions or universe membership.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if decisions.empty:
        pd.DataFrame(columns=PRIORITY_STATE_COLUMNS).to_csv(path, sep=";", index=False, encoding="utf-8-sig")
        return {"status": "EMPTY", "rows": 0, "path": str(path)}
    required = {"asset_class", "horizon", "isin", "score"}
    missing = required - set(decisions.columns)
    if missing:
        return {"status": "SKIPPED_INVALID_DECISIONS", "rows": 0, "path": str(path), "missing_columns": sorted(missing)}
    frame = decisions.copy()
    frame["asset_class"] = frame["asset_class"].astype(str).str.upper()
    frame["horizon"] = frame["horizon"].astype(str).str.upper()
    frame["isin"] = frame["isin"].astype(str).str.strip()
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame[
        frame["asset_class"].isin(["ACTION", "ETF"])
        & frame["horizon"].isin(["TCT", "CT", "MT"])
        & frame["isin"].ne("")
        & frame["isin"].ne("nan")
        & frame["score"].notna()
    ].copy()
    if "status" not in frame.columns:
        frame["status"] = ""
    if "decision" not in frame.columns:
        frame["decision"] = ""
    frame = frame[~frame["status"].astype(str).str.upper().isin(["FAILED", "BLOCKED_DATA"])].copy()
    stamp = generated_at_utc or datetime.now(timezone.utc).isoformat()
    frame["generated_at_utc"] = stamp
    frame = frame.sort_values(["asset_class", "horizon", "score"], ascending=[True, True, False])
    frame = frame.drop_duplicates(["asset_class", "horizon", "isin"], keep="first")
    frame = frame[PRIORITY_STATE_COLUMNS]
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    counts = frame.groupby(["asset_class", "horizon"]).size().to_dict() if not frame.empty else {}
    return {
        "status": "SUCCESS",
        "rows": int(len(frame)),
        "path": str(path),
        "counts": {f"{asset}:{horizon}": int(count) for (asset, horizon), count in counts.items()},
        "decision_logic_changed": False,
    }


def previous_horizon_candidates(
    root: Path,
    asset_class: str,
    *,
    decisions_path: str = "state/provenance/HORIZON_REFRESH_PRIORITY_V1.csv",
    limits: dict[str, int] | None = None,
) -> tuple[dict[str, set[str]], dict]:
    """Return prior-run top ISINs by horizon without affecting current scores."""
    path = root / decisions_path
    frame = _read_decisions(path)
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    empty = {horizon: set() for horizon in limits}
    if frame.empty:
        return empty, {"mode": "FALLBACK_NO_PREVIOUS_DECISIONS", "path": decisions_path, "rows": 0}
    required = {"asset_class", "horizon", "isin", "score"}
    if not required.issubset(frame.columns):
        return empty, {
            "mode": "FALLBACK_INVALID_PREVIOUS_DECISIONS",
            "path": decisions_path,
            "rows": int(len(frame)),
            "missing_columns": sorted(required - set(frame.columns)),
        }
    subset = frame[frame["asset_class"].astype(str).str.upper().eq(str(asset_class).upper())].copy()
    subset["score"] = pd.to_numeric(subset["score"], errors="coerce")
    subset = subset[subset["isin"].notna() & subset["score"].notna()].copy()
    if "status" in subset.columns:
        subset = subset[~subset["status"].astype(str).str.upper().isin(["FAILED", "BLOCKED_DATA"])]
    result: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for horizon, limit in limits.items():
        horizon_rows = subset[subset["horizon"].astype(str).str.upper().eq(horizon.upper())]
        horizon_rows = horizon_rows.sort_values("score", ascending=False).head(max(0, int(limit)))
        values = set(horizon_rows["isin"].astype(str))
        result[horizon] = values
        counts[horizon] = len(values)
    return result, {
        "mode": "PREVIOUS_HORIZON_RANKING",
        "path": decisions_path,
        "rows": int(len(subset)),
        "selected_by_horizon": counts,
    }


def assign_refresh_tiers(
    frame: pd.DataFrame,
    root: Path,
    *,
    asset_class: str,
    policy: dict,
    fallback_warm_n: int,
    ticker_col: str = "yahoo_ticker",
    isin_col: str = "isin",
) -> tuple[dict[str, str], dict]:
    """Map each ticker to HOT/WARM/COLD from the horizons that consume a source.

    The policy changes only source refresh cadence. It never changes a trading score,
    criterion, decision, universe membership or missing-value policy.
    """
    tiers: dict[str, str] = {}
    if ticker_col not in frame.columns or isin_col not in frame.columns:
        return tiers, {"mode": "NO_TICKER_OR_ISIN", "full_universe_preserved": True}
    clean = frame[[isin_col, ticker_col]].copy()
    clean[ticker_col] = clean[ticker_col].astype(str).str.strip()
    clean = clean[~clean[ticker_col].isin(["", "nan", "None", "<NA>"])]
    for ticker in clean[ticker_col]:
        tiers[str(ticker)] = "COLD"

    decisions_path = str(policy.get("previous_decisions_path", "state/provenance/HORIZON_REFRESH_PRIORITY_V1.csv"))
    limits = policy.get("candidate_limits", DEFAULT_LIMITS)
    candidates, audit = previous_horizon_candidates(
        root,
        asset_class,
        decisions_path=decisions_path,
        limits=limits,
    )
    isin_to_ticker = dict(zip(clean[isin_col].astype(str), clean[ticker_col].astype(str)))
    consumer_horizons = [str(value).upper() for value in policy.get("consumer_horizons", ["CT", "MT"])]
    hot_horizons = [str(value).upper() for value in policy.get("hot_horizons", ["CT"])]
    warm_horizons = [str(value).upper() for value in policy.get("warm_horizons", ["MT"])]

    if audit["mode"] == "PREVIOUS_HORIZON_RANKING":
        for horizon in warm_horizons:
            if horizon not in consumer_horizons:
                continue
            for isin in candidates.get(horizon, set()):
                ticker = isin_to_ticker.get(str(isin))
                if ticker:
                    tiers[ticker] = "WARM"
        for horizon in hot_horizons:
            if horizon not in consumer_horizons:
                continue
            for isin in candidates.get(horizon, set()):
                ticker = isin_to_ticker.get(str(isin))
                if ticker:
                    tiers[ticker] = "HOT"

    # Promotion buffer catches names that can emerge between two committee runs.
    promotion_n = int(policy.get("promotion_buffer_top_n", fallback_warm_n))
    if "score_brut" in frame.columns and promotion_n > 0:
        scored = frame[[ticker_col, "score_brut"]].copy()
        scored["_score"] = pd.to_numeric(scored["score_brut"], errors="coerce")
        scored = scored.dropna(subset=["_score"]).sort_values("_score", ascending=False).head(promotion_n)
        for ticker in scored[ticker_col].astype(str):
            if ticker in tiers and tiers[ticker] == "COLD":
                tiers[ticker] = "WARM"

    # Existing committee/watch and earnings events always override cache economy.
    if "comite_status" in frame.columns:
        urgent = frame[frame["comite_status"].astype(str).str.upper().isin(["COMMITTEE", "WATCH"])]
        for ticker in urgent[ticker_col].astype(str):
            if ticker in tiers:
                tiers[ticker] = "HOT"
    if "earnings_within_30d_flag" in frame.columns:
        flags = pd.to_numeric(frame["earnings_within_30d_flag"], errors="coerce").fillna(0)
        for ticker in frame.loc[flags.gt(0), ticker_col].astype(str):
            if ticker in tiers and tiers[ticker] == "COLD":
                tiers[ticker] = "WARM"
    if "earnings_within_7d_flag" in frame.columns:
        flags = pd.to_numeric(frame["earnings_within_7d_flag"], errors="coerce").fillna(0)
        for ticker in frame.loc[flags.gt(0), ticker_col].astype(str):
            if ticker in tiers:
                tiers[ticker] = "HOT"

    counts = {tier: sum(1 for value in tiers.values() if value == tier) for tier in ("HOT", "WARM", "COLD")}
    return tiers, {
        **audit,
        "asset_class": str(asset_class).upper(),
        "consumer_horizons": consumer_horizons,
        "hot_horizons": hot_horizons,
        "warm_horizons": warm_horizons,
        "promotion_buffer_top_n": promotion_n,
        "tier_counts": counts,
        "full_universe_preserved": True,
        "decision_logic_changed": False,
    }
