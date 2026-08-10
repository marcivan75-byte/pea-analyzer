from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TCTLabelConfig:
    horizon_sessions: int = 20
    target_return_pct: float = 15.0
    mae_floor_pct: float = -12.0


def _normalise_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for c in frame.columns:
        lc = str(c).strip().lower()
        if lc in {"date", "timestamp", "datetime", "session_date"}:
            rename[c] = "date"
        elif lc in {"instrument", "instrument_id", "isin", "ticker", "symbol"}:
            rename[c] = "instrument_id"
        elif lc in {"open", "high", "low", "close"}:
            rename[c] = lc
    out = frame.rename(columns=rename).copy()
    required = {"date", "instrument_id", "open", "high", "low", "close"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for c in ["open", "high", "low", "close"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["date", "instrument_id", "open", "high", "low", "close"])
    return out.sort_values(["instrument_id", "date"]).reset_index(drop=True)


def make_forward_labels(ohlc: pd.DataFrame, cfg: TCTLabelConfig | None = None) -> pd.DataFrame:
    """Point-in-time label: signal after close, entry at NEXT trading-session open."""
    cfg = cfg or TCTLabelConfig()
    data = _normalise_ohlc(ohlc)
    rows: list[dict[str, object]] = []
    for instrument, g in data.groupby("instrument_id", sort=False):
        g = g.reset_index(drop=True)
        n = len(g)
        for signal_idx in range(n - 1):
            entry_idx = signal_idx + 1
            end_idx = min(n - 1, entry_idx + cfg.horizon_sessions - 1)
            entry = float(g.loc[entry_idx, "open"])
            if not math.isfinite(entry) or entry <= 0:
                continue
            window = g.loc[entry_idx:end_idx]
            high_ret = (window["high"].astype(float) / entry - 1.0) * 100.0
            low_ret = (window["low"].astype(float) / entry - 1.0) * 100.0
            max_forward = float(high_ret.max())
            max_adverse = float(low_ret.min())
            hits = np.flatnonzero(high_ret.to_numpy() >= cfg.target_return_pct)
            hit = len(hits) > 0
            first_hit_sessions = int(hits[0] + 1) if hit else None
            if hit:
                pre_hit_lows = low_ret.iloc[:first_hit_sessions]
                mae_to_hit = float(pre_hit_lows.min())
                controlled = mae_to_hit >= cfg.mae_floor_pct
            else:
                mae_to_hit = max_adverse
                controlled = False
            rows.append({
                "snapshot_date": g.loc[signal_idx, "date"],
                "instrument_id": str(instrument),
                "entry_date": g.loc[entry_idx, "date"],
                "entry_price_next_open": entry,
                "max_forward_return_pct": max_forward,
                "mae_20d_pct": max_adverse,
                "mae_to_first_hit_pct": mae_to_hit,
                "explosion_hit_15pct_20d": bool(hit),
                "controlled_explosion_hit": bool(hit and controlled),
                "sessions_to_first_hit": first_hit_sessions,
                "label_horizon_sessions": int(cfg.horizon_sessions),
            })
    return pd.DataFrame(rows)


def _precision_at_k(df: pd.DataFrame, k: int, label_col: str) -> float:
    if df.empty:
        return math.nan
    vals = []
    for _, g in df.groupby("snapshot_date"):
        chosen = g.nlargest(min(k, len(g)), "score")
        if len(chosen):
            vals.append(float(chosen[label_col].astype(bool).mean()))
    return float(np.mean(vals)) if vals else math.nan


def evaluate_scores(
    scored: pd.DataFrame,
    label_col: str = "controlled_explosion_hit",
    decision_threshold: float = 72.0,
    k: int = 20,
    probability_col: str | None = None,
) -> dict[str, float]:
    required = {"snapshot_date", "instrument_id", "score", label_col}
    missing = required - set(scored.columns)
    if missing:
        raise ValueError(f"Missing evaluation columns: {sorted(missing)}")
    d = scored.copy()
    d["score"] = pd.to_numeric(d["score"], errors="coerce")
    d = d.dropna(subset=["score", label_col])
    y = d[label_col].astype(bool)
    selected = d["score"] >= float(decision_threshold)
    tp = int((selected & y).sum())
    fp = int((selected & ~y).sum())
    fn = int((~selected & y).sum())
    precision = tp / (tp + fp) if tp + fp else math.nan
    recall = tp / (tp + fn) if tp + fn else math.nan
    base = float(y.mean()) if len(y) else math.nan
    lift = precision / base if pd.notna(precision) and base > 0 else math.nan

    pos = d[selected & y]
    neg = d[selected & ~y]
    avg_win = float(pos.get("max_forward_return_pct", pd.Series(dtype=float)).mean()) if len(pos) else math.nan
    avg_loss = float(neg.get("mae_20d_pct", pd.Series(dtype=float)).mean()) if len(neg) else math.nan
    if pd.notna(avg_win) and pd.notna(avg_loss) and avg_loss < 0:
        pf_proxy = (precision * avg_win) / ((1.0 - precision) * abs(avg_loss)) if precision < 1 else math.inf
    else:
        pf_proxy = math.nan

    brier = math.nan
    if probability_col and probability_col in d.columns:
        p = pd.to_numeric(d[probability_col], errors="coerce")
        mask = p.notna()
        if mask.any():
            p01 = p.where(p <= 1.0, p / 100.0).clip(0, 1)
            brier = float(((p01[mask] - y[mask].astype(float)) ** 2).mean())

    return {
        "observations": float(len(d)),
        "positives": float(y.sum()),
        "selected": float(selected.sum()),
        "precision": float(precision) if pd.notna(precision) else math.nan,
        "recall": float(recall) if pd.notna(recall) else math.nan,
        "base_rate": base,
        "lift_vs_base": float(lift) if pd.notna(lift) else math.nan,
        "precision_at_k": _precision_at_k(d, k, label_col),
        "brier": brier,
        "mean_max_forward_return": float(d.loc[selected, "max_forward_return_pct"].mean()) if selected.any() and "max_forward_return_pct" in d else math.nan,
        "mean_mae": float(d.loc[selected, "mae_20d_pct"].mean()) if selected.any() and "mae_20d_pct" in d else math.nan,
        "profit_factor_proxy": float(pf_proxy) if pd.notna(pf_proxy) else math.nan,
    }


def _pava(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted pool-adjacent-violators for non-decreasing event probabilities."""
    blocks = [{"v": float(v), "w": float(w), "n": 1} for v, w in zip(values, weights)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i]["v"] <= blocks[i + 1]["v"] + 1e-15:
            i += 1
            continue
        a, b = blocks[i], blocks[i + 1]
        w = a["w"] + b["w"]
        merged = {
            "v": (a["v"] * a["w"] + b["v"] * b["w"]) / w if w else 0.0,
            "w": w,
            "n": a["n"] + b["n"],
        }
        blocks[i:i + 2] = [merged]
        i = max(0, i - 1)
    out = []
    for block in blocks:
        out.extend([block["v"]] * int(block["n"]))
    return np.asarray(out, dtype=float)


@dataclass
class MonotonicBinCalibrator:
    edges: np.ndarray
    probabilities: np.ndarray

    @classmethod
    def fit(cls, score: pd.Series, target: pd.Series, bins: int = 10) -> "MonotonicBinCalibrator":
        x = pd.to_numeric(score, errors="coerce")
        y = target.astype(float)
        mask = x.notna() & y.notna()
        x, y = x[mask], y[mask]
        if len(x) < max(30, bins * 3):
            raise ValueError("Insufficient observations for calibration")
        q = min(bins, max(2, int(x.nunique())))
        bucket = pd.qcut(x, q=q, duplicates="drop")
        stats = pd.DataFrame({"x": x, "y": y, "bucket": bucket}).groupby("bucket", observed=True).agg(
            low=("x", "min"), high=("x", "max"), rate=("y", "mean"), n=("y", "size")
        ).reset_index(drop=True)
        monotonic = _pava(stats["rate"].to_numpy(float), stats["n"].to_numpy(float))
        edges = stats["high"].to_numpy(float)
        return cls(edges=edges, probabilities=monotonic)

    def predict(self, score: pd.Series) -> pd.Series:
        x = pd.to_numeric(score, errors="coerce")
        idx = np.searchsorted(self.edges, x.fillna(self.edges[0]).to_numpy(float), side="left")
        idx = np.clip(idx, 0, len(self.probabilities) - 1)
        p = pd.Series(self.probabilities[idx], index=x.index)
        return p.where(x.notna())


def chronological_calibration_holdout(
    scored: pd.DataFrame,
    label_col: str = "controlled_explosion_hit",
    test_fraction: float = 0.30,
    bins: int = 10,
    min_positive_events: int = 75,
    k: int = 20,
) -> dict[str, object]:
    d = scored.copy()
    d["snapshot_date"] = pd.to_datetime(d["snapshot_date"], errors="coerce")
    d["score"] = pd.to_numeric(d["score"], errors="coerce")
    d = d.dropna(subset=["snapshot_date", "score", label_col])
    dates = sorted(d["snapshot_date"].unique())
    if len(dates) < 10:
        return {"status": "INSUFFICIENT_HISTORY", "reason": "need >=10 distinct snapshot dates"}
    split = int(len(dates) * (1.0 - float(test_fraction)))
    split = min(max(split, 1), len(dates) - 1)
    train_dates = set(dates[:split])
    test_dates = set(dates[split:])
    train = d[d["snapshot_date"].isin(train_dates)].copy()
    test = d[d["snapshot_date"].isin(test_dates)].copy()
    positives = int(train[label_col].astype(bool).sum())
    if positives < int(min_positive_events):
        return {
            "status": "INSUFFICIENT_POSITIVE_EVENTS",
            "train_positive_events": positives,
            "required_positive_events": int(min_positive_events),
            "probability_calibrated": False,
        }
    cal = MonotonicBinCalibrator.fit(train["score"], train[label_col], bins=bins)
    test["probability"] = cal.predict(test["score"])
    metrics = evaluate_scores(test, label_col=label_col, decision_threshold=72.0, k=k, probability_col="probability")
    return {
        "status": "CALIBRATED_HOLDOUT",
        "probability_calibrated": True,
        "train_snapshot_count": len(train_dates),
        "test_snapshot_count": len(test_dates),
        "train_positive_events": positives,
        "calibration_edges": cal.edges.tolist(),
        "calibration_probabilities": cal.probabilities.tolist(),
        "metrics": metrics,
    }
