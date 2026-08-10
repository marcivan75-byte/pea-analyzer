from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "data/reference/V21.1_TCT_EXPLOSIF_CONFIG.json"
OUT = ROOT / "outputs/V21.1_TCT_TECHNICAL_ENRICHED.csv"
AUDIT = ROOT / "outputs/audit/V21.1_TCT_TECHNICAL_ENRICHMENT.json"


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def technical_features(h: pd.DataFrame) -> dict[str, object]:
    if h is None or h.empty:
        return {}
    close = pd.to_numeric(h.get("Close"), errors="coerce")
    high = pd.to_numeric(h.get("High"), errors="coerce")
    low = pd.to_numeric(h.get("Low"), errors="coerce")
    open_ = pd.to_numeric(h.get("Open"), errors="coerce")
    volume = pd.to_numeric(h.get("Volume"), errors="coerce")
    frame = pd.DataFrame({"close": close, "high": high, "low": low, "open": open_, "volume": volume}).dropna(subset=["close"])
    if len(frame) < 55:
        return {}

    c = frame["close"]
    ma20 = c.rolling(20).mean()
    ma50 = c.rolling(50).mean()
    ma200 = c.rolling(200).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    macd_hist = macd - macd_signal

    prev_close = c.shift(1)
    tr = pd.concat([
        (frame["high"] - frame["low"]).abs(),
        (frame["high"] - prev_close).abs(),
        (frame["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

    std20 = c.rolling(20).std(ddof=0)
    bb_upper = ma20 + 2 * std20
    bb_lower = ma20 - 2 * std20
    bb_width = ((bb_upper - bb_lower) / ma20.replace(0, np.nan)) * 100.0

    vol20 = frame["volume"].rolling(20).mean()
    rvol = frame["volume"] / vol20.replace(0, np.nan)
    prior20_high = c.shift(1).rolling(20).max()
    breakout = c > prior20_high

    gap = ((frame["open"] / prev_close) - 1.0) * 100.0
    ret1 = c.pct_change(1) * 100.0
    ret5 = c.pct_change(5) * 100.0
    ret20 = c.pct_change(20) * 100.0

    ll = frame["low"].rolling(14).min()
    hh = frame["high"].rolling(14).max()
    stoch_k = (c - ll) / (hh - ll).replace(0, np.nan) * 100.0
    stoch_d = stoch_k.rolling(3).mean()
    stoch_bull = False
    if len(stoch_k) >= 2 and pd.notna(stoch_k.iloc[-2]) and pd.notna(stoch_d.iloc[-2]) and pd.notna(stoch_k.iloc[-1]) and pd.notna(stoch_d.iloc[-1]):
        stoch_bull = bool(stoch_k.iloc[-2] <= stoch_d.iloc[-2] and stoch_k.iloc[-1] > stoch_d.iloc[-1])

    now = float(c.iloc[-1])
    ma20_now = _f(ma20.iloc[-1])
    ma50_now = _f(ma50.iloc[-1])
    ma200_now = _f(ma200.iloc[-1]) if len(c) >= 200 else None
    ma_align = None
    if ma20_now is not None and ma50_now is not None and ma200_now is not None:
        ma_align = bool(now > ma20_now > ma50_now > ma200_now)

    return {
        "ma20": ma20_now,
        "ma50": ma50_now,
        "ma200": ma200_now,
        "ma_alignment_flag_v211": ma_align,
        "rsi14": _f(rsi.iloc[-1]),
        "macd": _f(macd.iloc[-1]),
        "macd_signal": _f(macd_signal.iloc[-1]),
        "macd_hist": _f(macd_hist.iloc[-1]),
        "atr14": _f(atr14.iloc[-1]),
        "bb_width_pct_v211": _f(bb_width.iloc[-1]),
        "volume_avg_20d": _f(vol20.iloc[-1]),
        "rvol20": _f(rvol.iloc[-1]),
        "volume_acceleration_20d": _f(rvol.iloc[-1]),
        "breakout_20d_flag": bool(breakout.iloc[-1]) if pd.notna(breakout.iloc[-1]) else None,
        "gap_up_pct_v211": _f(gap.iloc[-1]),
        "perf_1d_pct_v211": _f(ret1.iloc[-1]),
        "perf_5d_pct_v211": _f(ret5.iloc[-1]),
        "perf_20d_pct_v211": _f(ret20.iloc[-1]),
        "stoch_k": _f(stoch_k.iloc[-1]),
        "stoch_d": _f(stoch_d.iloc[-1]),
        "stoch_bull_cross_flag": stoch_bull,
    }


def _resolve_input(root: Path, cfg: dict) -> Path:
    for rel in cfg["input_candidates"]:
        if rel == "outputs/V21.1_TCT_TECHNICAL_ENRICHED.csv":
            continue
        p = root / rel
        if p.exists():
            return p
    raise FileNotFoundError("No V21.0 input for TCT technical enrichment")


def apply(root: Path | None = None) -> dict:
    root = root or ROOT
    cfg = json.loads((root / CONFIG.relative_to(ROOT)).read_text(encoding="utf-8"))
    source = _resolve_input(root, cfg)
    df = pd.read_csv(source, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != int(cfg["canonical_universe_size"]):
        raise RuntimeError("TCT technical enrichment universe gate")

    tickers = [
        x for x in df.get("yahoo_ticker", pd.Series(dtype=object)).dropna().astype(str).str.strip().unique().tolist()
        if x and x.lower() != "nan"
    ]
    success = 0
    failures: list[dict[str, str]] = []
    try:
        import yfinance as yf
        for start in range(0, len(tickers), 80):
            batch = tickers[start:start + 80]
            try:
                hist = yf.download(
                    batch, period="1y", interval="1d", group_by="ticker",
                    auto_adjust=True, actions=False, threads=True, progress=False, timeout=30,
                )
            except Exception as exc:
                failures.append({"ticker": "__BATCH__", "error": f"{type(exc).__name__}:{str(exc)[:140]}"})
                continue
            for ticker in batch:
                try:
                    h = hist[ticker] if isinstance(hist.columns, pd.MultiIndex) and ticker in hist.columns.get_level_values(0) else hist
                    feat = technical_features(h)
                    if not feat:
                        continue
                    idx = df.index[df["yahoo_ticker"].astype(str).eq(ticker)]
                    for field, value in feat.items():
                        df.loc[idx, field] = value
                    success += 1
                except Exception as exc:
                    failures.append({"ticker": ticker, "error": f"{type(exc).__name__}:{str(exc)[:140]}"})
    except Exception as exc:
        failures.append({"ticker": "__SETUP__", "error": f"{type(exc).__name__}:{str(exc)[:140]}"})

    path = root / OUT.relative_to(ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    fields = ["ma20","ma50","ma200","rvol20","gap_up_pct_v211","rsi14","macd_hist","breakout_20d_flag"]
    coverage = {f: round(float(df[f].notna().mean()), 4) for f in fields if f in df.columns}
    audit = {
        "passed": True,
        "rows": int(len(df)),
        "tickers_requested": int(len(tickers)),
        "tickers_enriched": int(success),
        "coverage": coverage,
        "failures": int(len(failures)),
        "failure_sample": failures[:20],
        "source": str(source.relative_to(root)),
        "output": str(path.relative_to(root)),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    ap = root / AUDIT.relative_to(ROOT)
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("V21_1_TCT_TECHNICAL_ENRICHMENT_OK", audit)
    return audit


if __name__ == "__main__":
    apply()
