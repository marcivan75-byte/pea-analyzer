from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

HOLDOUT_START = pd.Timestamp("2023-01-01")
BIG_WIN = 0.15
MIN_KEEP = 0.50
MIN_BIG_RECALL = 0.90
MAX_EXPECTANCY_GIVEBACK = 0.0025
MIN_PF_RATIO = 0.98
KEEP_LEVELS = (0.50, 0.60, 0.70, 0.80, 0.90)


def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise SystemExit(f"BLOCK_HYBRID_DATA: missing {col}")
    return pd.to_numeric(df[col], errors="coerce")


def build(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["date"] = pd.to_datetime(df["as_of_date"], errors="coerce")
    x["ret26"] = num(df, "forward_ret_true_26w")
    x["stop"] = df["hit_stop"].astype("boolean")
    mom = num(df, "mom_26w")
    dd = num(df, "drawdown_4w")
    rsi = num(df, "rsi_14_hebdo")
    atr = num(df, "atr_14_pct")
    close = num(df, "close")
    sma200 = num(df, "sma200")
    trend = close / sma200 - 1.0
    eps = 0.01

    # Interpretable hybrids built only from already-governed PIT technical inputs.
    x["H_MOM_VOL"] = mom / (atr.abs() + eps)
    x["H_TREND_DD"] = trend - dd.abs()
    x["H_RSI_TREND"] = ((rsi - 50.0) / 25.0) * trend
    x["H_VOL_DD"] = atr * dd.abs()
    x["H_MOM_DD"] = mom * (1.0 - dd.abs())
    x["H_TREND_VOL"] = trend / (atr.abs() + eps)
    x["H_OPPORTUNITY_RISK"] = mom / (atr.abs() + dd.abs() + eps)

    req = ["date", "ret26", "stop", "H_MOM_VOL", "H_TREND_DD", "H_RSI_TREND", "H_VOL_DD", "H_MOM_DD", "H_TREND_VOL", "H_OPPORTUNITY_RISK"]
    valid = x["date"].notna() & x["ret26"].notna() & x["stop"].notna()
    for c in req[3:]:
        valid &= np.isfinite(x[c])
    out = x.loc[valid].copy()
    out["stop"] = out["stop"].astype(bool)
    return out.sort_values("date", kind="stable")


def metrics(g: pd.DataFrame) -> dict[str, float | int | None]:
    if g.empty:
        return {"n": 0, "win_rate": None, "stop_rate": None, "expectancy": None, "profit_factor": None, "payoff_ratio": None, "big_winners": 0}
    r = g["ret26"].astype(float)
    w = r[r > 0]
    l = r[r <= 0]
    gp = float(w.sum()) if len(w) else 0.0
    gl = float((-l).sum()) if len(l) else 0.0
    aw = float(w.mean()) if len(w) else None
    al = float(l.mean()) if len(l) else None
    return {
        "n": int(len(g)),
        "win_rate": float((r > 0).mean()),
        "stop_rate": float(g["stop"].mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "payoff_ratio": float(aw / abs(al)) if aw is not None and al not in (None, 0.0) else None,
        "big_winners": int((r >= BIG_WIN).sum()),
    }


def percentile_risk(valid: pd.DataFrame, target: pd.DataFrame, col: str) -> tuple[np.ndarray, int, float]:
    # Determine risk direction only on pre-2023 validation via stop correlation.
    vv = valid[[col, "stop"]].dropna()
    corr = float(vv[col].corr(vv["stop"].astype(float), method="spearman")) if len(vv) > 2 else 0.0
    direction = 1 if corr >= 0 else -1
    ref = np.sort((valid[col].to_numpy(float) * direction))
    vals = target[col].to_numpy(float) * direction
    ranks = np.searchsorted(ref, vals, side="right") / max(len(ref), 1)
    return ranks, direction, corr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    train = build(pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False))
    hold = build(pd.read_csv(args.input_dir / "V22_1_HOLDOUT.csv", low_memory=False))
    train = train[train["date"] < HOLDOUT_START].copy()
    split = int(len(train) * 0.80)
    fit, valid = train.iloc[:split].copy(), train.iloc[split:].copy()
    if len(valid) < 1000 or hold.empty:
        raise SystemExit("BLOCK_HYBRID_DATA: insufficient validation/holdout")

    hybrids = [c for c in valid.columns if c.startswith("H_")]
    base = metrics(valid)
    base_big = max(int(base["big_winners"] or 0), 1)
    base_pf = float(base["profit_factor"] or 0.0)
    base_exp = float(base["expectancy"] or 0.0)

    valid_risk, hold_risk, directions = {}, {}, {}
    for c in hybrids:
        vr, direction, corr = percentile_risk(valid, valid, c)
        hr, _, _ = percentile_risk(valid, hold, c)
        valid_risk[c] = vr
        hold_risk[c] = hr
        directions[c] = {"risk_direction": direction, "validation_stop_spearman": corr}

    rows = []
    best = None
    candidates = [(c,) for c in hybrids] + list(itertools.combinations(hybrids, 2))
    for combo in candidates:
        risk = np.mean([valid_risk[c] for c in combo], axis=0)
        for keep_level in KEEP_LEVELS:
            keep = risk <= keep_level
            g = valid.loc[keep]
            m = metrics(g)
            keep_rate = float(keep.mean())
            big_recall = float((m["big_winners"] or 0) / base_big)
            pf = float(m["profit_factor"] or 0.0)
            exp = float(m["expectancy"] or -1e9)
            stop_reduction = float(base["stop_rate"] - m["stop_rate"]) if m["stop_rate"] is not None else -1.0
            admissible = (
                keep_rate >= MIN_KEEP
                and big_recall >= MIN_BIG_RECALL
                and exp >= base_exp - MAX_EXPECTANCY_GIVEBACK
                and pf >= base_pf * MIN_PF_RATIO
            )
            row = {
                "criteria": "+".join(combo), "keep_level": keep_level, "keep_rate": keep_rate,
                "big_winner_recall_vs_full": big_recall, "stop_reduction": stop_reduction,
                "admissible": admissible, **m,
            }
            rows.append(row)
            # Objective explicitly targets false positives first, but only after hard guards.
            key = (stop_reduction, exp, pf, big_recall, keep_rate)
            if admissible and (best is None or key > best[0]):
                best = (key, combo, keep_level, row)

    if best is None:
        raise SystemExit("BLOCK_HYBRID_MODEL: no hybrid satisfies pre2023 guards")

    combo, keep_level = best[1], best[2]
    hold_score = np.mean([hold_risk[c] for c in combo], axis=0)
    hold_keep = hold_score <= keep_level
    hold_base = metrics(hold)
    hold_sel = metrics(hold.loc[hold_keep])
    hold_big_base = max(int(hold_base["big_winners"] or 0), 1)

    report = {
        "version": "V22.1_HYBRID_FP_RESEARCH_1",
        "governance": {
            "training_source": "PRE_2023_TECHNICAL_PIT_ONLY",
            "selection_source": "LAST_20_PERCENT_PRE2023_VALIDATION_ONLY",
            "holdout_used_for_tuning": False,
            "holdout_scope": "EVALUATION_ONLY",
            "new_raw_features_added": False,
            "big_winner_definition": BIG_WIN,
            "guards": {"min_keep_rate": MIN_KEEP, "min_big_winner_recall": MIN_BIG_RECALL, "max_expectancy_giveback": MAX_EXPECTANCY_GIVEBACK, "min_pf_ratio_vs_full": MIN_PF_RATIO},
        },
        "hybrid_definitions": {
            "H_MOM_VOL": "mom_26w/(abs(atr_14_pct)+0.01)",
            "H_TREND_DD": "close_vs_sma200-abs(drawdown_4w)",
            "H_RSI_TREND": "((rsi_14_hebdo-50)/25)*close_vs_sma200",
            "H_VOL_DD": "atr_14_pct*abs(drawdown_4w)",
            "H_MOM_DD": "mom_26w*(1-abs(drawdown_4w))",
            "H_TREND_VOL": "close_vs_sma200/(abs(atr_14_pct)+0.01)",
            "H_OPPORTUNITY_RISK": "mom_26w/(abs(atr_14_pct)+abs(drawdown_4w)+0.01)",
        },
        "risk_directions": directions,
        "validation_full": base,
        "selected": {"criteria": list(combo), "keep_level": keep_level, "validation": best[3]},
        "holdout_evaluation": {
            "full": hold_base,
            "hybrid": hold_sel,
            "keep_rate": float(hold_keep.mean()),
            "big_winner_recall_vs_full": float((hold_sel["big_winners"] or 0) / hold_big_base),
            "stop_reduction": float(hold_base["stop_rate"] - hold_sel["stop_rate"]) if hold_sel["stop_rate"] is not None else None,
        },
        "promotion_automatic": False,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["admissible", "stop_reduction", "expectancy"], ascending=[False, False, False]).to_csv(args.out_dir / "HYBRID_FP_GRID.csv", index=False)
    pd.DataFrame({"row_index": hold.index, "hybrid_keep": hold_keep, "hybrid_risk_score": hold_score}).to_csv(args.out_dir / "HYBRID_FP_HOLDOUT_MASK.csv", index=False)
    (args.out_dir / "HYBRID_FP_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
