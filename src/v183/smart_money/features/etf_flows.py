from __future__ import annotations
import pandas as pd
from v183.smart_money.scoring import clamp


def estimated_flow(aum_t: float, aum_prev: float, nav_t: float, nav_prev: float) -> tuple[float, float]:
    if min(aum_t, aum_prev, nav_t, nav_prev) <= 0:
        raise ValueError("AUM and NAV must be positive")
    flow = float(aum_t - aum_prev * (nav_t / nav_prev))
    return flow, float(flow / aum_prev)


def enrich_history(frame: pd.DataFrame, winsorize_daily_flow_pct: float | None = None) -> pd.DataFrame:
    required = {"date", "aum", "nav"}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing columns: {sorted(required - set(frame.columns))}")
    f = frame.copy()
    f["date"] = pd.to_datetime(f["date"], errors="coerce")
    f["aum"] = pd.to_numeric(f["aum"], errors="coerce")
    f["nav"] = pd.to_numeric(f["nav"], errors="coerce")
    f = f.dropna(subset=["date", "aum", "nav"])
    f = f[(f["aum"] > 0) & (f["nav"] > 0)].sort_values("date").drop_duplicates("date", keep="last").copy()
    perf_factor = f["nav"] / f["nav"].shift(1)
    f["estimated_flow"] = f["aum"] - f["aum"].shift(1) * perf_factor
    f["flow_pct"] = f["estimated_flow"] / f["aum"].shift(1)
    if winsorize_daily_flow_pct is not None:
        cap = abs(float(winsorize_daily_flow_pct))
        f["flow_pct"] = f["flow_pct"].clip(-cap, cap)
    f["flow_pct_5d"] = f["flow_pct"].rolling(5, min_periods=1).sum()
    f["flow_pct_20d"] = f["flow_pct"].rolling(20, min_periods=1).sum()
    mean = f["flow_pct"].rolling(20, min_periods=20).mean()
    std = f["flow_pct"].rolling(20, min_periods=20).std(ddof=0)
    f["flow_z20"] = (f["flow_pct"] - mean) / std.replace(0, pd.NA)
    f["positive_days_5"] = (f["flow_pct"] > 0).rolling(5, min_periods=5).sum()
    return f


def score(history: pd.DataFrame, cfg: dict, as_of: str | None = None) -> tuple[float, float, dict]:
    if history.empty:
        return 0.0, 0.0, {"flow_status": "NO_HISTORY", "flow_history_snapshots": 0, "flow_observations": 0}
    flow_cfg = cfg["etf_flows"]
    min_observations = int(flow_cfg.get("min_history_observations", 20))
    f = enrich_history(history, winsorize_daily_flow_pct=flow_cfg.get("winsorize_daily_flow_pct"))
    flow_rows = f["flow_pct"].dropna()
    latest_date = None if f.empty else pd.Timestamp(f.iloc[-1]["date"])
    meta_base = {
        "flow_history_snapshots": int(len(f)),
        "flow_observations": int(len(flow_rows)),
        "flow_latest_date": None if latest_date is None else latest_date.strftime("%Y-%m-%d"),
    }
    if len(flow_rows) < min_observations:
        return 0.0, 0.0, {**meta_base, "flow_status": "INSUFFICIENT_HISTORY"}

    reference = pd.Timestamp(as_of[:10]) if as_of else pd.Timestamp.utcnow().tz_localize(None).normalize()
    age_days = max(0, int((reference - latest_date.normalize()).days)) if latest_date is not None else 99999
    meta_base["flow_age_days"] = age_days
    if age_days > int(flow_cfg.get("max_snapshot_age_days", 10)):
        return 0.0, 0.0, {**meta_base, "flow_status": "STALE_HISTORY"}

    row = f.iloc[-1]
    pct20 = row.get("flow_pct_20d")
    pct20 = 0.0 if pd.isna(pct20) else float(pct20)
    z20 = row.get("flow_z20")
    z20 = 0.0 if pd.isna(z20) else float(z20)
    core = clamp(
        pct20 * float(flow_cfg["flow20_sensitivity"]) + z20 * float(flow_cfg["z20_sensitivity"]),
        -float(cfg["caps"]["flow_core"]),
        float(cfg["caps"]["flow_core"]),
    )
    positive_days = row.get("positive_days_5")
    positive_days = 2.5 if pd.isna(positive_days) else float(positive_days)
    persistence = ((positive_days - 2.5) / 2.5) * float(cfg["caps"]["flow_persistence"])
    persistence = clamp(
        persistence,
        -float(cfg["caps"]["flow_persistence"]),
        float(cfg["caps"]["flow_persistence"]),
    )
    return round(core, 4), round(persistence, 4), {
        **meta_base,
        "flow_status": "OK",
        "flow_pct_1d": float(row.get("flow_pct") or 0.0),
        "flow_pct_5d": float(row.get("flow_pct_5d") or 0.0),
        "flow_pct_20d": pct20,
        "flow_z20": z20,
        "positive_days_5": int(positive_days),
    }
