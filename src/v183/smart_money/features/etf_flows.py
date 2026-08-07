from __future__ import annotations
import pandas as pd
from v183.smart_money.scoring import clamp


def estimated_flow(aum_t: float, aum_prev: float, nav_t: float, nav_prev: float) -> tuple[float, float]:
    if min(aum_t, aum_prev, nav_t, nav_prev) <= 0:
        raise ValueError("AUM and NAV must be positive")
    flow = float(aum_t - aum_prev * (nav_t / nav_prev))
    return flow, float(flow / aum_prev)


def enrich_history(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "aum", "nav"}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing columns: {sorted(required - set(frame.columns))}")
    f = frame.sort_values("date").copy()
    perf_factor = f["nav"] / f["nav"].shift(1)
    f["estimated_flow"] = f["aum"] - f["aum"].shift(1) * perf_factor
    f["flow_pct"] = f["estimated_flow"] / f["aum"].shift(1)
    f["flow_pct_5d"] = f["flow_pct"].rolling(5, min_periods=1).sum()
    f["flow_pct_20d"] = f["flow_pct"].rolling(20, min_periods=1).sum()
    mean = f["flow_pct"].rolling(20).mean()
    std = f["flow_pct"].rolling(20).std(ddof=0)
    f["flow_z20"] = (f["flow_pct"] - mean) / std.replace(0, pd.NA)
    f["positive_days_5"] = (f["flow_pct"] > 0).rolling(5, min_periods=1).sum()
    return f


def score(history: pd.DataFrame, cfg: dict) -> tuple[float, float, dict]:
    if history.empty:
        return 0.0, 0.0, {}
    f = enrich_history(history)
    row = f.iloc[-1]
    pct20 = float(row.get("flow_pct_20d") or 0.0)
    z20 = row.get("flow_z20")
    z20 = 0.0 if pd.isna(z20) else float(z20)
    core = clamp(pct20 * float(cfg["etf_flows"]["flow20_sensitivity"]) +
                 z20 * float(cfg["etf_flows"]["z20_sensitivity"]),
                 -float(cfg["caps"]["flow_core"]), float(cfg["caps"]["flow_core"]))
    positive_days = float(row.get("positive_days_5") or 0.0)
    persistence = ((positive_days - 2.5) / 2.5) * float(cfg["caps"]["flow_persistence"])
    persistence = clamp(persistence, -float(cfg["caps"]["flow_persistence"]),
                        float(cfg["caps"]["flow_persistence"]))
    return round(core, 4), round(persistence, 4), {
        "flow_pct_1d": float(row.get("flow_pct") or 0.0),
        "flow_pct_5d": float(row.get("flow_pct_5d") or 0.0),
        "flow_pct_20d": pct20,
        "flow_z20": z20,
        "positive_days_5": int(positive_days),
    }
