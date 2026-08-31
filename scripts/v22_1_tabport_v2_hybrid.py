from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from scripts.v22_1_tabport_portfolio import (
    ANALYSIS_END,
    FEATURES,
    V1_THRESHOLD,
    V3_REGIMES,
    _add_regime,
    _features,
    _load_prices,
    _period_table,
    _summary,
    simulate,
)

BIG_WIN_RETURN = 0.15
MIN_BIG_WIN_RECALL_VS_FULL = 0.90
MIN_KEEP_RATE = 0.50
ACTIONS = ("FULL", "MAE_V1", "MAE_V2")


def metrics(ret: np.ndarray, stop: np.ndarray, keep: np.ndarray) -> dict[str, float | int | None]:
    keep = np.asarray(keep, dtype=bool)
    r = np.asarray(ret, dtype=float)[keep]
    s = np.asarray(stop, dtype=bool)[keep]
    finite = np.isfinite(r)
    r = r[finite]
    s = s[finite]
    if not len(r):
        return {"kept": int(keep.sum()), "mature": 0, "expectancy": None, "profit_factor": None, "stop_rate": None, "win_rate": None, "big_winner_count": 0}
    wins = r[r > 0]
    losses = r[r <= 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float((-losses).sum()) if len(losses) else 0.0
    return {
        "kept": int(keep.sum()),
        "mature": int(len(r)),
        "expectancy": float(r.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "stop_rate": float(s.mean()),
        "win_rate": float((r > 0).mean()),
        "big_winner_count": int((r >= BIG_WIN_RETURN).sum()),
    }


def choose_policy(train_raw: pd.DataFrame, hold_raw: pd.DataFrame) -> tuple[pd.Series, dict[str, object]]:
    train = _features(train_raw, True)
    hold = _features(hold_raw, False)
    split = int(len(train) * 0.80)
    fit, valid = train.iloc[:split].copy(), train.iloc[split:].copy()
    if len(fit) < 1000 or len(valid) < 1000:
        raise SystemExit("BLOCK_TABPORT_V2: insufficient pre2023 fit/validation")

    scaler = StandardScaler().fit(fit[list(FEATURES)])
    v1 = LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42)
    v1.fit(scaler.transform(fit[list(FEATURES)]), fit["label"])
    pv1 = v1.predict_proba(scaler.transform(valid[list(FEATURES)]))[:, 1]
    target_keep = float((pv1 <= V1_THRESHOLD).mean())

    v2 = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=150, l2_regularization=2.0, random_state=42)
    v2.fit(fit[list(FEATURES)], fit["label"])
    pv2 = v2.predict_proba(valid[list(FEATURES)])[:, 1]
    v2_threshold = float(np.quantile(pv2, target_keep))

    atr_cut = float(fit.groupby("date")["atr_14_pct"].median().median())
    valid_r = _add_regime(valid, atr_cut)
    hold_r = _add_regime(hold, atr_cut)

    raw_idx = valid.index
    ret = pd.to_numeric(train_raw.loc[raw_idx, "forward_ret_true_26w"], errors="coerce").to_numpy()
    stop = train_raw.loc[raw_idx, "hit_stop"].astype("boolean").fillna(False).astype(bool).to_numpy()
    full_keep = np.ones(len(valid), dtype=bool)
    v1_keep = pv1 <= V1_THRESHOLD
    v2_keep = pv2 <= v2_threshold
    action_keep = {"FULL": full_keep, "MAE_V1": v1_keep, "MAE_V2": v2_keep}

    full_m = metrics(ret, stop, full_keep)
    full_big = max(int(full_m["big_winner_count"] or 0), 1)
    rows: list[dict[str, object]] = []
    best: tuple[tuple[float, float, float, float], dict[str, str], dict[str, object]] | None = None

    regimes = valid_r["regime"].astype(str).to_numpy()
    for choices in itertools.product(ACTIONS, repeat=len(V3_REGIMES)):
        policy = dict(zip(V3_REGIMES, choices, strict=True))
        keep = np.zeros(len(valid), dtype=bool)
        for regime, action in policy.items():
            idx = regimes == regime
            keep[idx] = action_keep[action][idx]
        keep_rate = float(keep.mean())
        m = metrics(ret, stop, keep)
        big_recall = float((m["big_winner_count"] or 0) / full_big)
        admissible = keep_rate >= MIN_KEEP_RATE and big_recall >= MIN_BIG_WIN_RECALL_VS_FULL
        exp = float(m["expectancy"]) if m["expectancy"] is not None else -1e9
        pf = float(m["profit_factor"]) if m["profit_factor"] is not None else 0.0
        sr = float(m["stop_rate"]) if m["stop_rate"] is not None else 1.0
        # Lexicographic choice: expectancy first, then PF, then fewer stops, then higher big-winner recall.
        key = (exp, pf, -sr, big_recall)
        row = {**{f"policy_{r}": policy[r] for r in V3_REGIMES}, "admissible": admissible, "keep_rate": keep_rate, "big_winner_recall_vs_full": big_recall, **m}
        rows.append(row)
        if admissible and (best is None or key > best[0]):
            best = (key, policy, row)

    if best is None:
        raise SystemExit("BLOCK_TABPORT_V2: no hybrid policy satisfies pre2023 guards")

    policy = best[1]
    ph1 = v1.predict_proba(scaler.transform(hold[list(FEATURES)]))[:, 1]
    ph2 = v2.predict_proba(hold[list(FEATURES)])[:, 1]
    hold_action_keep = {"FULL": np.ones(len(hold), dtype=bool), "MAE_V1": ph1 <= V1_THRESHOLD, "MAE_V2": ph2 <= v2_threshold}
    hold_regimes = hold_r["regime"].astype(str).to_numpy()
    hybrid = np.zeros(len(hold), dtype=bool)
    for regime, action in policy.items():
        idx = hold_regimes == regime
        hybrid[idx] = hold_action_keep[action][idx]

    mask = pd.Series(False, index=hold_raw.index)
    mask.loc[hold.index] = hybrid
    meta = {
        "version": "TABPORT_V2_HYBRID",
        "selection_period": "PRE_2023_VALIDATION_ONLY",
        "holdout_used_for_tuning": False,
        "actions": list(ACTIONS),
        "regimes": list(V3_REGIMES),
        "policy": policy,
        "guards": {"min_big_winner_recall_vs_full": MIN_BIG_WIN_RECALL_VS_FULL, "min_keep_rate": MIN_KEEP_RATE, "big_winner_definition": BIG_WIN_RETURN},
        "v1_threshold": V1_THRESHOLD,
        "v2_threshold": v2_threshold,
        "regime_atr_cut_fit_only": atr_cut,
        "validation_full": full_m,
        "validation_selected": best[2],
        "candidate_count": len(rows),
    }
    return mask, meta, pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--price-parquet", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    train_raw = pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False)
    hold_raw = pd.read_csv(args.input_dir / "V22_1_HOLDOUT.csv", low_memory=False)
    hybrid_mask, meta, grid = choose_policy(train_raw, hold_raw)
    prices = _load_prices(args.price_parquet)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    curve, trades = simulate(hold_raw, prices, hybrid_mask, "PORT_V2_HYBRID", slippage=0.0)
    curve_r, trades_r = simulate(hold_raw, prices, hybrid_mask, "PORT_V2_HYBRID", slippage=0.001)
    if curve.empty:
        raise SystemExit("BLOCK_TABPORT_V2_SIM: empty portfolio curve")

    summary = pd.DataFrame([_summary(curve, trades, "PORT_V2_HYBRID")])
    robust = pd.DataFrame([_summary(curve_r, trades_r, "PORT_V2_HYBRID")])
    robust["scenario"] = "ROBUST_PLUS_0_10PCT_SLIPPAGE_EACH_SIDE"
    annual = _period_table(curve, "PORT_V2_HYBRID", "Y")
    quarter = _period_table(curve, "PORT_V2_HYBRID", "Q")

    summary.to_csv(args.out_dir / "TABPORT_V2_SUMMARY.csv", index=False)
    robust.to_csv(args.out_dir / "TABPORT_V2_ROBUSTNESS.csv", index=False)
    annual.to_csv(args.out_dir / "TABPORT_V2_ANNUAL.csv", index=False)
    quarter.to_csv(args.out_dir / "TABPORT_V2_QUARTERLY.csv", index=False)
    trades.to_csv(args.out_dir / "TABPORT_V2_TRADES.csv", index=False)
    curve.to_csv(args.out_dir / "TABPORT_V2_EQUITY.csv", index=False)
    grid.sort_values(["admissible", "expectancy", "profit_factor"], ascending=[False, False, False]).to_csv(args.out_dir / "TABPORT_V2_POLICY_GRID.csv", index=False)
    (args.out_dir / "TABPORT_V2_CONFIG.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    print("TABPORT V2 POLICY")
    print(json.dumps(meta, indent=2, sort_keys=True))
    print("TABPORT V2 SUMMARY")
    print(summary.to_string(index=False))
    print("TABPORT V2 ANNUAL")
    print(annual.to_string(index=False))
    print("TABPORT V2 ROBUSTNESS")
    print(robust.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
