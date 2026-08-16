from __future__ import annotations

from pathlib import Path
import json
import uuid
import numpy as np
import pandas as pd

from v182.backtest.exceptional_pit_oos import HOLDOUT_START, _load_action_histories, _read_csv
from v182.backtest.action_entry_exit_horizons_v21_8 import (
    PERIOD_START, HORIZON_SESSIONS, _clean_history, _features, _market_proxy,
    _ct_mt_signals, _period,
)
from v182.features.tct_v24_1_7_exact import compute_technical_indicators
from v182.decision.tct_timing_exact_v24_1_7 import detect_exact, FORMULA_VERSION
from v182.sources.yfinance_bulk import download_history

ROOT = Path(__file__).resolve().parents[3]

TRAILING_GRID = {
    "CT": {"activation": (0.05, 0.08, 0.10), "giveback": (0.25, 0.33, 0.50)},
    "MT": {"activation": (0.10, 0.15, 0.20), "giveback": (0.25, 0.33, 0.50)},
}
TCT_CHECKPOINTS = (1, 2, 3, 5, 10)


def simulate_profit_protection(path: pd.DataFrame, activation: float, giveback: float) -> dict:
    """Research-only trailing protection. No fixed take-profit and no initial stop."""
    if path.empty:
        return {"triggered": False}
    entry = float(path.iloc[0]["close"])
    rets = path["close"] / entry - 1.0
    running_peak = rets.cummax()
    armed = running_peak >= float(activation)
    floor = running_peak * (1.0 - float(giveback))
    hit = armed & (rets <= floor)
    if not hit.any():
        return {
            "triggered": False,
            "exit_return": float(rets.iloc[-1]),
            "final_return": float(rets.iloc[-1]),
            "saved_vs_hold": 0.0,
            "max_mfe": float(rets.max()),
        }
    dt = hit[hit].index[0]
    idx = int(path.index.get_loc(dt))
    exit_ret = float(rets.loc[dt])
    final_ret = float(rets.iloc[-1])
    return {
        "triggered": True,
        "trigger_session": idx + 1,
        "exit_return": exit_ret,
        "final_return": final_ret,
        "saved_vs_hold": exit_ret - final_ret,
        "max_mfe": float(rets.max()),
        "peak_at_trigger": float(running_peak.loc[dt]),
    }


def _tct_exact_events(history: pd.DataFrame, cfg: dict) -> list[dict]:
    """Exact T1/T2 timing detector, with baseline eligibility assumed only to isolate timing quality."""
    h = _clean_history(history)
    if len(h) < 130:
        return []
    tech = compute_technical_indicators(h.rename(columns={c: str(c).lower() for c in h.columns}))
    events: list[dict] = []
    state: dict = {}
    last_t1_date: pd.Timestamp | None = None
    for i in range(110, len(tech)):
        dt = pd.Timestamp(tech.index[i])
        if dt < PERIOD_START or dt >= HOLDOUT_START:
            continue
        current = tech.iloc[: i + 1]
        if state and last_t1_date is not None:
            age = int(np.busday_count(last_t1_date.date().isoformat(), dt.date().isoformat()))
            state["age_sessions"] = age
            if age > int(cfg["t1"]["ttl_sessions"]):
                state = {}
                last_t1_date = None
        det = detect_exact(current, state, cfg)
        setup = det.get("setup")
        if setup == "T1":
            upd = dict(det.get("state_update") or {})
            upd.update({
                "event_id": f"T1_RESEARCH_{uuid.uuid4().hex[:12]}",
                "baseline_eligible_at_t1": True,
                "age_sessions": 0,
            })
            state = upd
            last_t1_date = dt
            events.append({
                "event_date": dt,
                "event_type": "T1_EXACT_TIMING",
                "quality": float(det.get("t1_quality") or 0.0),
                "baseline_eligibility_assumed": True,
            })
        elif setup == "T2_CONFIRMATION" and state:
            events.append({
                "event_date": dt,
                "event_type": "T2_EXACT_TIMING_CONFIRMATION",
                "quality": float(det.get("t2_quality") or 0.0),
                "baseline_eligibility_assumed": True,
                "age_sessions": state.get("age_sessions"),
            })
            state = {}
            last_t1_date = None
    return events


def _event_forward_returns(features: pd.DataFrame, dt: pd.Timestamp) -> dict | None:
    future = features[features.index > dt]
    if future.empty or future.index[min(len(future), max(TCT_CHECKPOINTS)) - 1] >= HOLDOUT_START:
        return None
    out = {}
    entry = float(future.iloc[0]["close"])
    for cp in TCT_CHECKPOINTS:
        if len(future) < cp:
            out[f"return_{cp}s"] = None
            out[f"mfe_{cp}s"] = None
            out[f"mae_{cp}s"] = None
            continue
        p = future.iloc[:cp]["close"] / entry - 1.0
        out[f"return_{cp}s"] = float(p.iloc[-1])
        out[f"mfe_{cp}s"] = float(p.max())
        out[f"mae_{cp}s"] = float(p.min())
    return out


def run(root: Path = ROOT) -> dict:
    actions = _read_csv(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in actions.columns else "ticker_yahoo_final"
    ticker_to_isin = {
        str(t).strip(): str(i).strip() for t, i in zip(actions[ticker_col], actions["isin"])
        if str(t).strip() and str(t).strip().lower() not in {"nan", "none", "<na>"}
    }
    cache = root / "data" / "cache" / "action_management_v21_8"
    dl = download_history(list(ticker_to_isin), str(cache), period="max", interval="1d", batch_size=30, auto_adjust=True, include_actions=False)
    histories = _load_action_histories(cache, ticker_to_isin)
    market = _market_proxy(histories)
    feats = {isin: _features(h, market) for isin, h in histories.items()}
    feats = {k: v for k, v in feats.items() if not v.empty}

    outdir = root / "outputs" / "research" / "action_management_v21_8"
    outdir.mkdir(parents=True, exist_ok=True)

    # TCT exact timing cascade study.
    tct_cfg = json.loads((root / "config" / "TCT_V24_1_7_SHADOW.json").read_text(encoding="utf-8"))
    tct_rows = []
    for isin, hist in histories.items():
        if isin not in feats:
            continue
        try:
            events = _tct_exact_events(hist, tct_cfg)
        except Exception:
            continue
        for ev in events:
            fr = _event_forward_returns(feats[isin], pd.Timestamp(ev["event_date"]))
            if fr is None:
                continue
            dt = pd.Timestamp(ev["event_date"])
            tct_rows.append({"isin": isin, "period": _period(dt), **ev, **fr})
    tct = pd.DataFrame(tct_rows)
    tct.to_csv(outdir / "TCT_EXACT_TIMING_EVENTS.csv", sep=";", index=False, encoding="utf-8-sig")

    # CT/MT profit-protection grid: let winners run, then test relative giveback from running peak.
    protection_rows = []
    for hz in ("CT", "MT"):
        sigs = _ct_mt_signals(feats, hz)
        for _, s in sigs.iterrows():
            isin = str(s["isin"]); dt = pd.Timestamp(s["signal_date"])
            if isin not in feats:
                continue
            future = feats[isin][feats[isin].index > dt].iloc[: HORIZON_SESSIONS[hz]]
            if len(future) < max(10, HORIZON_SESSIONS[hz] // 3) or future.index[-1] >= HOLDOUT_START:
                continue
            for activation in TRAILING_GRID[hz]["activation"]:
                for giveback in TRAILING_GRID[hz]["giveback"]:
                    sim = simulate_profit_protection(future, activation, giveback)
                    protection_rows.append({
                        "horizon": hz,
                        "period": _period(dt),
                        "isin": isin,
                        "signal_date": dt.date().isoformat(),
                        "activation_pct": activation * 100,
                        "giveback_fraction": giveback,
                        **sim,
                    })
    protection = pd.DataFrame(protection_rows)
    protection.to_csv(outdir / "CT_MT_PROFIT_PROTECTION_GRID.csv", sep=";", index=False, encoding="utf-8-sig")

    agg_rows = []
    if not protection.empty:
        for keys, g in protection.groupby(["horizon", "period", "activation_pct", "giveback_fraction"]):
            hz, period, act, gb = keys
            trig = g["triggered"].fillna(False).astype(bool)
            agg_rows.append({
                "horizon": hz,
                "period": period,
                "activation_pct": act,
                "giveback_fraction": gb,
                "signals": len(g),
                "trigger_rate": float(trig.mean()),
                "median_exit_return_pct": 100 * pd.to_numeric(g["exit_return"], errors="coerce").median(),
                "median_hold_return_pct": 100 * pd.to_numeric(g["final_return"], errors="coerce").median(),
                "median_saved_vs_hold_pct": 100 * pd.to_numeric(g["saved_vs_hold"], errors="coerce").median(),
                "positive_exit_rate": float((pd.to_numeric(g["exit_return"], errors="coerce") > 0).mean()),
            })
    agg = pd.DataFrame(agg_rows)
    agg.to_csv(outdir / "CT_MT_PROFIT_PROTECTION_AGGREGATES.csv", sep=";", index=False, encoding="utf-8-sig")

    if not tct.empty:
        tct_agg = tct.groupby(["period", "event_type"]).agg(
            events=("isin", "size"),
            median_r1=("return_1s", "median"), median_r2=("return_2s", "median"),
            median_r3=("return_3s", "median"), median_r5=("return_5s", "median"), median_r10=("return_10s", "median"),
            positive_5s=("return_5s", lambda x: float((pd.to_numeric(x, errors="coerce") > 0).mean())),
            positive_10s=("return_10s", lambda x: float((pd.to_numeric(x, errors="coerce") > 0).mean())),
        ).reset_index()
    else:
        tct_agg = pd.DataFrame()
    tct_agg.to_csv(outdir / "TCT_EXACT_TIMING_AGGREGATES.csv", sep=";", index=False, encoding="utf-8-sig")

    payload = {
        "status": "SUCCESS",
        "study": "ACTION_MANAGEMENT_V21_8_IMPROVEMENT",
        "holdout_opened": False,
        "real_orders_enabled": False,
        "fixed_take_profit": False,
        "initial_stop_executed": False,
        "weights_or_selection_thresholds_changed": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "tct_formula_version": FORMULA_VERSION,
        "tct_baseline_note": "Exact T1/T2 timing detector; baseline eligibility is assumed only to isolate timing quality. This is not a full TCT baseline backtest.",
        "ct_mt_note": "Price/technical proxy signals only; profit protection is a research grid based on relative giveback from running peak, not a promoted production rule.",
        "download": {"requested": dl.requested, "successful": len(dl.successful), "failed": len(dl.failed)},
        "histories_loaded": len(histories),
        "tct_events": int(len(tct)),
        "ct_mt_grid_rows": int(len(protection)),
    }
    (outdir / "SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
