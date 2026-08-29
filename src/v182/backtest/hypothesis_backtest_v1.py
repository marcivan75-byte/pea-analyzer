"""Diagnostic backtest of entry hypotheses on reconstructed PIT prices."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
EXTRA_ROOTS = (Path("/home/workdir/artifacts"),)
SOURCES = {
    "ACTION_MT": (
        Path("artifacts/backtest/ACTION_MT_PIT_OBSERVATIONS.csv"),
        Path("state/backtest/ACTION_MT_PIT_OBSERVATIONS.csv"),
        Path("outputs/backtest/ACTION_MT_PIT_OBSERVATIONS.csv"),
        Path("outputs/backtest/ACTION_MT_WEEKLY_SNAPSHOTS.csv"),
    ),
    "ETF_MT": (
        Path("artifacts/backtest/ETF_MT_PIT_OBSERVATIONS.csv"),
        Path("state/backtest/ETF_MT_PIT_OBSERVATIONS.csv"),
        Path("outputs/backtest/ETF_MT_PIT_OBSERVATIONS.csv"),
        Path("outputs/backtest/ETF_MT_WEEKLY_SNAPSHOTS.csv"),
    ),
}
SIGNAL = {"ACTION_MT": "ACTION_MT_SCORE", "ETF_MT": "ETF_MT_SCORE"}
OUT_JSON = Path("outputs/audit/HYPOTHESIS_BACKTEST_SHADOW.json")
OUT_MD = Path("outputs/audit/HYPOTHESIS_BACKTEST_SHADOW.md")


def _candidates(root: Path, relative: Path) -> list[Path]:
    paths = [root / relative]
    for extra in EXTRA_ROOTS:
        paths.append(extra / relative.name)
        paths.append(extra / "backtest" / relative.name)
    return paths


def _read(root: Path, scope: str) -> pd.DataFrame:
    for relative in SOURCES[scope]:
        for path in _candidates(root, relative):
            if path.exists() and path.stat().st_size:
                frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
                frame["source_file"] = str(path)
                return frame
    return pd.DataFrame()


def _spaced(dates, days: int = 21):
    out = []
    for value in sorted(dates):
        if not out or (value - out[-1]).days >= days:
            out.append(value)
    return out


def _metrics(values: list[float]) -> dict:
    series = pd.Series(values, dtype="float64").dropna()
    if series.empty:
        return {"n_snapshots": 0}
    wins = series.clip(lower=series.quantile(0.05), upper=series.quantile(0.95))
    return {
        "n_snapshots": int(len(series)),
        "mean_fwd_60d_pct": round(float(series.mean()), 3),
        "mean_winsor_5_95_pct": round(float(wins.mean()), 3),
        "median_fwd_60d_pct": round(float(series.median()), 3),
        "p10_fwd_60d_pct": round(float(series.quantile(0.10)), 3),
        "hit_rate_pct": round(float((series > 0).mean() * 100.0), 1),
        "worst_pct": round(float(series.min()), 3),
    }


def _evaluate(frame: pd.DataFrame, signal_col: str) -> dict:
    frame = frame.copy()
    frame["as_of"] = pd.to_datetime(frame["as_of"], errors="coerce")
    signal = signal_col if signal_col in frame.columns else (
        "OR_COMPOSITE_SHADOW" if "OR_COMPOSITE_SHADOW" in frame.columns else None
    )
    if signal is None:
        return {"status": "MISSING_SIGNAL"}
    frame["_signal"] = pd.to_numeric(frame[signal], errors="coerce")
    frame["fwd"] = pd.to_numeric(frame.get("forward_return_pct_60d"), errors="coerce")
    frame = frame.dropna(subset=["as_of", "_signal"])
    if frame["fwd"].notna().sum() == 0:
        return {
            "status": "FORWARDS_PENDING",
            "rows": int(len(frame)),
            "isins": int(frame["isin"].nunique()) if "isin" in frame else 0,
            "signal_column": signal,
        }
    frame = frame.dropna(subset=["fwd"])
    dates = _spaced(frame["as_of"].unique(), 21)
    buckets = {"equal_weight": [], "static_top_10pct": [], "dynamic_p70": []}
    for ts in dates:
        snap = frame[frame["as_of"].eq(ts)]
        if len(snap) < 8:
            continue
        buckets["equal_weight"].append(float(snap["fwd"].mean()))
        top_n = max(1, int(round(len(snap) * 0.10)))
        buckets["static_top_10pct"].append(float(snap.nlargest(top_n, "_signal")["fwd"].mean()))
        threshold = float(snap["_signal"].quantile(0.70))
        chosen = snap[snap["_signal"] >= threshold]
        buckets["dynamic_p70"].append(float(chosen["fwd"].mean()))
    return {
        "status": "OK",
        "rows": int(len(frame)),
        "isins": int(frame["isin"].nunique()) if "isin" in frame else 0,
        "first": str(frame["as_of"].min().date()) if not frame.empty else None,
        "last": str(frame["as_of"].max().date()) if not frame.empty else None,
        "source_file": str(frame["source_file"].iloc[0]) if "source_file" in frame and not frame.empty else None,
        "signal_column": signal,
        "equal_weight": _metrics(buckets["equal_weight"]),
        "static_top_10pct": _metrics(buckets["static_top_10pct"]),
        "dynamic_p70": _metrics(buckets["dynamic_p70"]),
    }


def _markdown(payload: dict) -> str:
    lines = [
        "# Backtest diagnostic des hypothèses entrée (proxy PIT)",
        "",
        "Pas une promotion. Signal = momentum 126j ou snapshot Friday, pas la confiance CI.",
        "Moyenne winsorisée 5/95. R:R book historique absent.",
        "",
    ]
    for scope in ("ACTION_MT", "ETF_MT"):
        block = payload["scopes"].get(scope) or {}
        lines += [f"## {scope}", ""]
        if block.get("status") in {None, "NO_OBSERVATIONS", "MISSING_SIGNAL", "FORWARDS_PENDING"} and "equal_weight" not in block:
            lines += [f"Statut: {block.get('status', 'NO_OBSERVATIONS')}", ""]
            continue
        lines += [
            f"{block.get('isins')} titres, {block.get('rows')} lignes, {block.get('first')} → {block.get('last')}.",
            "",
            "| politique | n | mean | mean winsor | médiane | P10 | hit-rate % |",
            "|---|---|---|---|---|---|---|",
        ]
        for key, label in (
            ("equal_weight", "Equal weight"),
            ("static_top_10pct", "Top 10% statique"),
            ("dynamic_p70", "Dynamique P70"),
        ):
            item = block.get(key) or {}
            lines.append(
                f"| {label} | {item.get('n_snapshots', '')} | {item.get('mean_fwd_60d_pct', '')} | {item.get('mean_winsor_5_95_pct', '')} | {item.get('median_fwd_60d_pct', '')} | {item.get('p10_fwd_60d_pct', '')} | {item.get('hit_rate_pct', '')} |"
            )
        lines.append("")
    lines.append("Aucune règle n'est promue.")
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    scopes = {}
    for scope, column in SIGNAL.items():
        frame = _read(root, scope)
        scopes[scope] = _evaluate(frame, column) if not frame.empty else {"status": "NO_OBSERVATIONS"}
    payload = {
        "status": "DIAGNOSTIC_PROXY_ONLY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "horizon_days": 60,
        "spacing_days": 21,
        "oos_official_start": "2026-09-01",
        "signal_source": "PIT_126D_PRICE_MOMENTUM_OR_WEEKLY_SNAPSHOT",
        "promotion_ready": False,
        "decision_influence": 0.0,
        "real_orders_enabled": False,
        "scopes": scopes,
    }
    (root / OUT_JSON).parent.mkdir(parents=True, exist_ok=True)
    (root / OUT_JSON).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / OUT_MD).write_text(_markdown(payload), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
