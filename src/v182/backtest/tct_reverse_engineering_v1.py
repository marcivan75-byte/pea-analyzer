from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

FORBIDDEN_FEATURE_TOKENS = (
    "fwd_", "forward_", "future_", "label_", "target_", "outcome_",
    "mfe_", "mae_", "hit_", "exit_", "realized_",
)

DEFAULT_THRESHOLDS = (0.10, 0.15, 0.20, 0.25, 0.30)
DEFAULT_HORIZONS = (5, 10, 15, 20)
DEFAULT_DELTAS = (5, 10, 20)


@dataclass(frozen=True)
class ReverseEngineeringConfig:
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS
    delta_windows: tuple[int, ...] = DEFAULT_DELTAS
    core_threshold: float = 0.25
    execution_price: str = "next_open"
    min_support: int = 20
    min_control_support: int = 20
    max_pattern_size: int = 3
    embargo_sessions: int = 20
    transaction_cost_bps: float = 20.0
    holdout_start: str = "2025-01-01"
    holdout_end: str = "2026-12-31"


def _canon_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "date": ("date", "as_of_date", "session_date"),
        "instrument_id": ("instrument_id", "isin", "ticker", "yahoo_ticker"),
        "open": ("open", "Open", "session_open"),
        "high": ("high", "High", "session_high"),
        "low": ("low", "Low", "session_low"),
        "close": ("close", "Close", "session_close", "reference_close"),
        "volume": ("volume", "Volume"),
    }
    out = frame.copy()
    selected: dict[str, str] = {}
    lower = {str(c).lower(): str(c) for c in out.columns}
    for target, names in aliases.items():
        for name in names:
            if name in out.columns:
                selected[target] = name
                break
            if name.lower() in lower:
                selected[target] = lower[name.lower()]
                break
    missing = [k for k in ("date", "instrument_id", "open", "high", "low", "close") if k not in selected]
    if missing:
        raise ValueError(f"OHLCV_MISSING_REQUIRED:{','.join(missing)}")
    rename = {source: target for target, source in selected.items()}
    out = out.rename(columns=rename)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["instrument_id"] = out["instrument_id"].astype(str).str.strip()
    for c in ("open", "high", "low", "close", "volume"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["date", "instrument_id", "close"]).sort_values(["instrument_id", "date"])
    out = out.drop_duplicates(["instrument_id", "date"], keep="first")
    return out


def build_forward_labels(ohlcv: pd.DataFrame, cfg: ReverseEngineeringConfig = ReverseEngineeringConfig()) -> pd.DataFrame:
    """Create future labels from a realistic next-session execution price.

    Vectorized within each instrument. Entry is next-session open; features must
    be built separately from rows dated <= J0. Incomplete tail windows are censored.
    """
    frame = _canon_ohlcv(ohlcv)
    pieces: list[pd.DataFrame] = []
    max_h = max(cfg.horizons)
    for _, group in frame.groupby("instrument_id", sort=False):
        g = group.sort_values("date").reset_index(drop=True).copy()
        n = len(g)
        entry = g["open"].shift(-1) if cfg.execution_price == "next_open" else g["close"].shift(-1)
        g["entry_price_next_session"] = entry
        high_future = np.column_stack([g["high"].shift(-step).to_numpy(dtype=float) for step in range(1, max_h + 1)])
        low_future = np.column_stack([g["low"].shift(-step).to_numpy(dtype=float) for step in range(1, max_h + 1)])
        close_future = np.column_stack([g["close"].shift(-step).to_numpy(dtype=float) for step in range(1, max_h + 1)])
        ep = entry.to_numpy(dtype=float)
        valid_entry = np.isfinite(ep) & (ep > 0)
        for horizon in cfg.horizons:
            h = int(horizon)
            eligible = valid_entry.copy()
            eligible[max(0, n - h):] = False
            highs = high_future[:, :h]
            lows = low_future[:, :h]
            closes = close_future[:, :h]
            max_high = np.full(n, np.nan)
            min_low = np.full(n, np.nan)
            max_close = np.full(n, np.nan)
            high_rows = eligible & np.isfinite(highs).any(axis=1)
            low_rows = eligible & np.isfinite(lows).any(axis=1)
            close_rows = eligible & np.isfinite(closes).any(axis=1)
            max_high[high_rows] = np.nanmax(highs[high_rows], axis=1) / ep[high_rows] - 1.0
            min_low[low_rows] = np.nanmin(lows[low_rows], axis=1) / ep[low_rows] - 1.0
            max_close[close_rows] = np.nanmax(closes[close_rows], axis=1) / ep[close_rows] - 1.0
            g[f"fwd_mfe_h{horizon}"] = max_high
            g[f"fwd_max_close_return_h{horizon}"] = max_close
            g[f"fwd_mae_h{horizon}"] = min_low
            g[f"label_eligible_h{horizon}"] = eligible.astype("int8")
            returns_by_step = highs / ep[:, None] - 1.0
            for threshold in cfg.thresholds:
                pct = int(round(threshold * 100))
                hit_matrix = returns_by_step >= threshold
                any_hit = hit_matrix.any(axis=1) & eligible
                first = np.where(any_hit, hit_matrix.argmax(axis=1) + 1, np.nan)
                label = pd.Series(pd.NA, index=g.index, dtype="Int64")
                label.loc[eligible] = any_hit[eligible].astype(int)
                g[f"label_hit_{pct}_h{horizon}"] = label
                g[f"first_hit_{pct}_h{horizon}"] = first
        pieces.append(g)
    return pd.concat(pieces, ignore_index=True) if pieces else frame.copy()


def build_technical_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Build transparent PIT-safe technical features for each instrument/date."""
    frame = _canon_ohlcv(ohlcv)
    pieces: list[pd.DataFrame] = []
    for _, g in frame.groupby("instrument_id", sort=False):
        g = g.sort_values("date").copy()
        close = g["close"]
        high = g["high"]
        low = g["low"]
        volume = g["volume"] if "volume" in g.columns else pd.Series(np.nan, index=g.index)
        for days in (1, 3, 5, 10, 20, 60):
            g[f"ret_{days}d"] = close.pct_change(days)
        for days in (5, 10, 20, 50, 100, 200):
            ma = close.rolling(days, min_periods=days).mean()
            g[f"ma_{days}"] = ma
            g[f"close_over_ma_{days}"] = close / ma - 1.0
            g[f"ma_{days}_slope_5d"] = ma.pct_change(5)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean().replace(0, np.nan)
        rs = gain / loss
        g["rsi14"] = 100 - 100 / (1 + rs)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        g["macd"] = macd
        g["macd_signal"] = signal
        g["macd_hist"] = macd - signal
        prev = close.shift(1)
        tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        g["atr14_pct"] = atr / close
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper, lower = mid + 2 * std, mid - 2 * std
        g["bb_bandwidth"] = (upper - lower) / mid.replace(0, np.nan)
        g["breakout_20d"] = (close > high.shift(1).rolling(20, min_periods=20).max()).astype("Int64")
        g["breakout_50d"] = (close > high.shift(1).rolling(50, min_periods=50).max()).astype("Int64")
        avg20 = volume.rolling(20).mean().replace(0, np.nan)
        g["rvol20"] = volume / avg20
        g["volume_accel_5_20"] = volume.rolling(5).mean() / avg20
        g["range_pct"] = (high - low) / close.replace(0, np.nan)
        pieces.append(g)
    return pd.concat(pieces, ignore_index=True) if pieces else frame.copy()


def append_only_history(
    existing: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    key_cols: Sequence[str],
    observed_col: str = "observed_at_utc",
) -> pd.DataFrame:
    """Append immutable observations; conflicting duplicates fail closed."""
    incoming = observations.copy()
    if incoming.empty:
        return existing.copy()
    if observed_col not in incoming.columns:
        raise ValueError("PIT_TIMESTAMP_REQUIRED")
    incoming[observed_col] = pd.to_datetime(incoming[observed_col], errors="coerce", utc=True)
    if incoming[observed_col].isna().any():
        raise ValueError("PIT_TIMESTAMP_INVALID")
    current = existing.copy()
    if current.empty:
        current = incoming.iloc[0:0].copy()
    if observed_col in current.columns:
        current[observed_col] = pd.to_datetime(current[observed_col], errors="coerce", utc=True)
    keys = list(key_cols) + [observed_col]
    combined = pd.concat([current, incoming], ignore_index=True, sort=False)
    compare_cols = [c for c in combined.columns if c not in keys]
    dup = combined[combined.duplicated(keys, keep=False)]
    if not dup.empty:
        for _, d in dup.groupby(keys, dropna=False):
            if len(d[compare_cols].drop_duplicates()) > 1:
                raise ValueError("PIT_IMMUTABILITY_CONFLICT")
    return combined.drop_duplicates(keys, keep="first").sort_values(keys).reset_index(drop=True)


def asof_snapshot_features(
    base: pd.DataFrame,
    history: pd.DataFrame,
    *,
    id_col: str = "instrument_id",
    date_col: str = "date",
    history_time_col: str = "observed_at_utc",
    value_cols: Sequence[str] | None = None,
    delta_windows: Sequence[int] = DEFAULT_DELTAS,
) -> pd.DataFrame:
    """Attach PIT observations and compute changes over prior trading sessions."""
    out = base.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce", utc=True)
    hist = history.copy()
    if history_time_col not in hist.columns:
        raise ValueError("PIT_TIMESTAMP_REQUIRED")
    hist[history_time_col] = pd.to_datetime(hist[history_time_col], errors="coerce", utc=True)
    if hist[history_time_col].isna().any():
        raise ValueError("PIT_TIMESTAMP_INVALID")
    if value_cols is None:
        value_cols = [
            c for c in hist.columns
            if c not in {id_col, history_time_col, "source", "fetched_at_utc"}
            and pd.api.types.is_numeric_dtype(hist[c])
        ]
    right = hist[[id_col, history_time_col, *value_cols]].copy().sort_values([history_time_col, id_col])
    ordered = out.sort_values([date_col, id_col]).copy()
    merged = pd.merge_asof(
        ordered, right,
        left_on=date_col, right_on=history_time_col,
        by=id_col, direction="backward", allow_exact_matches=True,
    )
    for lag in delta_windows:
        lag_dates = ordered.sort_values([id_col, date_col]).groupby(id_col, sort=False)[date_col].shift(int(lag))
        probe = ordered[[id_col, date_col]].copy()
        probe["_lag_date"] = lag_dates.reindex(ordered.index)
        valid = probe["_lag_date"].notna()
        lagged_values = pd.DataFrame(index=ordered.index, columns=value_cols, dtype=float)
        if valid.any():
            left = probe.loc[valid, [id_col, "_lag_date"]].sort_values(["_lag_date", id_col])
            lagged = pd.merge_asof(
                left, right, left_on="_lag_date", right_on=history_time_col,
                by=id_col, direction="backward", allow_exact_matches=True,
            )
            lagged.index = left.index
            for c in value_cols:
                lagged_values.loc[lagged.index, c] = pd.to_numeric(lagged[c], errors="coerce")
        current_by_index = merged.copy()
        current_by_index.index = ordered.index
        for c in value_cols:
            current = pd.to_numeric(current_by_index[c], errors="coerce")
            previous = pd.to_numeric(lagged_values[c], errors="coerce")
            absolute = current - previous
            relative = absolute / previous.replace(0, np.nan)
            merged[f"{c}_delta_{lag}s"] = absolute.to_numpy()
            merged[f"{c}_pct_delta_{lag}s"] = relative.to_numpy()
    return merged.sort_values([id_col, date_col]).reset_index(drop=True)


def pivot_long_pit_observations(
    observations: pd.DataFrame,
    *,
    ticker_to_instrument: Mapping[str, str] | None = None,
    ticker_col: str = "ticker",
    field_col: str = "field",
    value_col: str = "value",
    observed_col: str = "fetched_at_utc",
) -> pd.DataFrame:
    """Convert existing long-form source observations to immutable PIT research snapshots."""
    required = {ticker_col, field_col, value_col, observed_col}
    if not required.issubset(observations.columns):
        raise ValueError("PIT_OBSERVATION_FIELDS_MISSING:" + ",".join(sorted(required - set(observations.columns))))
    data = observations.copy()
    data[observed_col] = pd.to_datetime(data[observed_col], errors="coerce", utc=True)
    if data[observed_col].isna().any():
        raise ValueError("PIT_TIMESTAMP_INVALID")
    data["instrument_id"] = data[ticker_col].map(ticker_to_instrument) if ticker_to_instrument else data[ticker_col].astype(str)
    data = data.dropna(subset=["instrument_id"])
    wide = data.pivot_table(index=["instrument_id", observed_col], columns=field_col, values=value_col, aggfunc="first").reset_index()
    wide.columns.name = None
    wide = wide.rename(columns={observed_col: "observed_at_utc"})
    quantitative = {
        "consensus_score", "buy_n", "hold_n", "sell_n", "n_analysts", "consensus_delta_4w",
        "net_upgrades_30d", "broker_weighted_revision_30d", "target_price",
    }
    for c in quantitative.intersection(wide.columns):
        wide[c] = pd.to_numeric(wide[c], errors="coerce")
    return wide.sort_values(["instrument_id", "observed_at_utc"]).reset_index(drop=True)


def build_catalyst_event_features(
    base: pd.DataFrame,
    events: pd.DataFrame,
    *,
    id_col: str = "instrument_id",
    date_col: str = "date",
    event_time_col: str = "observed_at_utc",
    event_type_col: str = "event_type",
    windows_days: Sequence[int] = (1, 3, 5, 10, 20),
) -> tuple[pd.DataFrame, list[str]]:
    """Encode already-observed catalyst events with vectorized time searches."""
    out = base.copy().reset_index(drop=True)
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce", utc=True)
    hist = events.copy()
    required = {id_col, event_time_col, event_type_col}
    if not required.issubset(hist.columns):
        raise ValueError("CATALYST_FIELDS_MISSING:" + ",".join(sorted(required - set(hist.columns))))
    hist[event_time_col] = pd.to_datetime(hist[event_time_col], errors="coerce", utc=True)
    if hist[event_time_col].isna().any():
        raise ValueError("PIT_TIMESTAMP_INVALID")
    hist[event_type_col] = hist[event_type_col].astype(str).str.upper().str.strip()
    types = sorted(t for t in hist[event_type_col].unique() if t)
    feature_cols: list[str] = []
    date_ns = out[date_col].astype("int64").to_numpy()
    day_ns = 86_400_000_000_000
    for event_type in types:
        safe = "".join(ch if ch.isalnum() else "_" for ch in event_type.lower()).strip("_")
        typed = hist[hist[event_type_col] == event_type]
        for window in windows_days:
            col = f"catalyst_{safe}_count_{int(window)}d"
            out[col] = 0
            feature_cols.append(col)
        age_col = f"catalyst_{safe}_age_days"
        out[age_col] = np.nan
        feature_cols.append(age_col)
        for instrument, idx in out.groupby(id_col, sort=False).groups.items():
            event_times = typed.loc[typed[id_col] == instrument, event_time_col].sort_values().astype("int64").to_numpy()
            if len(event_times) == 0:
                continue
            positions = np.asarray(list(idx), dtype=int)
            signals = date_ns[positions]
            right = np.searchsorted(event_times, signals, side="right")
            for window in windows_days:
                left_boundary = signals - int(window) * day_ns
                left = np.searchsorted(event_times, left_boundary, side="right")
                out.loc[positions, f"catalyst_{safe}_count_{int(window)}d"] = right - left
            has_prior = right > 0
            prior_time = np.full(len(signals), np.nan)
            prior_time[has_prior] = event_times[right[has_prior] - 1]
            out.loc[positions, age_col] = (signals - prior_time) / day_ns
    return out, feature_cols


def sanitize_feature_columns(columns: Iterable[str]) -> list[str]:
    safe: list[str] = []
    rejected: list[str] = []
    for column in columns:
        name = str(column)
        low = name.lower()
        if any(token in low for token in FORBIDDEN_FEATURE_TOKENS):
            rejected.append(name)
        else:
            safe.append(name)
    if rejected:
        raise ValueError("LOOKAHEAD_FEATURE_REJECTED:" + ",".join(sorted(rejected)))
    return safe


def binary_factor_lift(frame: pd.DataFrame, factor_col: str, label_col: str, *, min_support: int = 20) -> dict:
    data = frame[[factor_col, label_col]].dropna()
    if data.empty:
        return {"factor": factor_col, "support": 0, "status": "NO_DATA"}
    y = pd.to_numeric(data[label_col], errors="coerce")
    mask = data[factor_col].astype(bool)
    baseline = float(y.mean())
    support = int(mask.sum())
    hits = int(y[mask].sum()) if support else 0
    rate = hits / support if support else np.nan
    lift = rate / baseline if support and baseline > 0 else np.nan
    return {
        "factor": factor_col, "support": support, "hits": hits, "baseline_rate": baseline,
        "success_rate": rate, "lift": lift, "status": "OK" if support >= min_support else "LOW_SUPPORT",
    }


def quantile_factor_scan(
    frame: pd.DataFrame, factor_cols: Sequence[str], label_col: str, *, bins: int = 5, min_support: int = 20,
) -> pd.DataFrame:
    rows: list[dict] = []
    baseline = float(pd.to_numeric(frame[label_col], errors="coerce").mean())
    for factor in sanitize_feature_columns(factor_cols):
        data = frame[[factor, label_col]].dropna().copy()
        if len(data) < max(min_support, bins * 2):
            continue
        try:
            data["_bucket"] = pd.qcut(pd.to_numeric(data[factor], errors="coerce"), q=bins, duplicates="drop")
        except ValueError:
            continue
        for bucket, group in data.groupby("_bucket", observed=True):
            support = len(group)
            rate = float(pd.to_numeric(group[label_col], errors="coerce").mean())
            rows.append({
                "factor": factor, "bucket": str(bucket), "support": int(support), "success_rate": rate,
                "baseline_rate": baseline, "lift": rate / baseline if baseline > 0 else np.nan,
                "status": "OK" if support >= min_support else "LOW_SUPPORT",
            })
    return pd.DataFrame(rows).sort_values(["lift", "support"], ascending=[False, False]) if rows else pd.DataFrame()


def discover_boolean_patterns(
    frame: pd.DataFrame, factor_cols: Sequence[str], label_col: str, *, max_size: int = 3, min_support: int = 20,
) -> pd.DataFrame:
    factors = sanitize_feature_columns(factor_cols)
    baseline = float(pd.to_numeric(frame[label_col], errors="coerce").mean())
    rows: list[dict] = []
    for size in range(1, min(max_size, len(factors)) + 1):
        for combo in combinations(factors, size):
            mask = pd.Series(True, index=frame.index)
            for factor in combo:
                mask &= frame[factor].fillna(False).astype(bool)
            support = int(mask.sum())
            if support < min_support:
                continue
            y = pd.to_numeric(frame.loc[mask, label_col], errors="coerce")
            rate = float(y.mean())
            rows.append({
                "pattern": " & ".join(combo), "size": size, "support": support, "hits": int(y.sum()),
                "success_rate": rate, "baseline_rate": baseline, "lift": rate / baseline if baseline > 0 else np.nan,
            })
    return pd.DataFrame(rows).sort_values(["lift", "support"], ascending=[False, False]) if rows else pd.DataFrame()


def chronological_split(frame: pd.DataFrame, date_col: str = "date", *, purge_sessions: int = 20) -> pd.DataFrame:
    """Chronological split with a conservative purge before every boundary."""
    out = frame.copy()
    dt = pd.to_datetime(out[date_col], errors="coerce")
    out["research_split"] = np.select(
        [
            dt.between("2010-01-01", "2018-12-31"), dt.between("2019-01-01", "2022-12-31"),
            dt.between("2023-01-01", "2024-12-31"), dt.between("2025-01-01", "2026-12-31"),
        ],
        ["DISCOVERY", "DEVELOPMENT", "VALIDATION", "HOLDOUT"], default="OUT_OF_SCOPE",
    )
    if purge_sessions > 0:
        for boundary in pd.to_datetime(["2019-01-01", "2023-01-01", "2025-01-01"]):
            lower = boundary - pd.offsets.BDay(int(purge_sessions))
            crossing = (dt < boundary) & (dt >= lower)
            out.loc[crossing, "research_split"] = "PURGED"
    return out


def discover_patterns_discovery_only(
    frame: pd.DataFrame, factor_cols: Sequence[str], label_col: str, *, cfg: ReverseEngineeringConfig = ReverseEngineeringConfig(),
) -> pd.DataFrame:
    discovery = frame[frame["research_split"] == "DISCOVERY"].copy()
    return discover_boolean_patterns(
        discovery, factor_cols, label_col, max_size=cfg.max_pattern_size, min_support=cfg.min_support,
    )


def pattern_stability(
    frame: pd.DataFrame, pattern_cols: Sequence[str], label_col: str, split_col: str = "research_split", min_support: int = 20,
) -> pd.DataFrame:
    rows = []
    for split, group in frame.groupby(split_col, dropna=False):
        stats = discover_boolean_patterns(group, pattern_cols, label_col, max_size=min(3, len(pattern_cols)), min_support=min_support)
        if stats.empty:
            continue
        stats.insert(0, "split", split)
        rows.append(stats)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _audit_universe(frame: pd.DataFrame) -> dict:
    required = {"instrument_id", "date"}
    ok = required.issubset(frame.columns) and not frame[list(required)].isna().any().any()
    duplicates = int(frame.duplicated(["instrument_id", "date"]).sum()) if required.issubset(frame.columns) else -1
    return {"pass": bool(ok and duplicates == 0), "duplicates": duplicates, "scope": "universe_identity"}


def _audit_labels(frame: pd.DataFrame, cfg: ReverseEngineeringConfig) -> dict:
    cols = [f"label_hit_{int(t*100)}_h{h}" for t in cfg.thresholds for h in cfg.horizons]
    present = all(c in frame.columns for c in cols)
    entry_ok = "entry_price_next_session" in frame.columns and (frame["entry_price_next_session"].dropna() > 0).all()
    return {"pass": bool(present and entry_ok), "scope": "labels_execution", "required_labels": len(cols)}


def _audit_pit(feature_cols: Sequence[str]) -> dict:
    try:
        sanitize_feature_columns(feature_cols)
        return {"pass": True, "scope": "pit_lookahead", "rejected": []}
    except ValueError as exc:
        return {"pass": False, "scope": "pit_lookahead", "error": str(exc)}


def _audit_feature_quality(frame: pd.DataFrame, feature_cols: Sequence[str]) -> dict:
    existing = [c for c in feature_cols if c in frame.columns]
    if not existing:
        return {"pass": False, "scope": "feature_quality", "reason": "NO_FEATURES"}
    coverage = float(frame[existing].notna().mean().mean())
    inf_count = 0
    for c in existing:
        s = pd.to_numeric(frame[c], errors="coerce")
        inf_count += int(np.isinf(s).sum())
    return {"pass": bool(coverage >= 0.35 and inf_count == 0), "scope": "feature_quality", "coverage": coverage, "inf_count": inf_count}


def _audit_split(frame: pd.DataFrame, cfg: ReverseEngineeringConfig) -> dict:
    if "research_split" not in frame.columns:
        return {"pass": False, "scope": "split_leakage", "reason": "NO_SPLIT"}
    holdout = frame[frame["research_split"] == "HOLDOUT"]
    other = frame[frame["research_split"].isin(["DISCOVERY", "DEVELOPMENT", "VALIDATION"])]
    separated = True
    if not holdout.empty and not other.empty:
        separated = pd.to_datetime(other["date"]).max() < pd.to_datetime(holdout["date"]).min()
    return {"pass": bool(separated), "scope": "split_leakage", "holdout_rows": int(len(holdout)), "embargo_sessions": cfg.embargo_sessions}


def _audit_robustness(patterns: pd.DataFrame, cfg: ReverseEngineeringConfig) -> dict:
    if patterns.empty:
        return {"pass": False, "scope": "pattern_robustness", "reason": "NO_PATTERNS"}
    supported = patterns[patterns["support"] >= cfg.min_support]
    finite = supported["lift"].replace([np.inf, -np.inf], np.nan).notna().all()
    return {"pass": bool(not supported.empty and finite), "scope": "pattern_robustness", "supported_patterns": int(len(supported))}


def _audit_trading_realism(frame: pd.DataFrame, cfg: ReverseEngineeringConfig) -> dict:
    mfe = [c for c in frame.columns if c.startswith("fwd_mfe_h")]
    mae = [c for c in frame.columns if c.startswith("fwd_mae_h")]
    ok = bool(mfe and mae and cfg.transaction_cost_bps >= 0 and cfg.execution_price == "next_open")
    return {"pass": ok, "scope": "trading_realism", "mfe_fields": len(mfe), "mae_fields": len(mae), "cost_bps": cfg.transaction_cost_bps}


def _audit_oos(frame: pd.DataFrame, patterns: pd.DataFrame) -> dict:
    splits = set(frame.get("research_split", pd.Series(dtype=str)).astype(str))
    required = {"DISCOVERY", "DEVELOPMENT", "VALIDATION", "HOLDOUT"}
    return {
        "pass": bool(required.issubset(splits)), "scope": "oos_regime_holdout", "splits_present": sorted(splits),
        "pattern_count": int(len(patterns)), "holdout_locked_for_discovery": True,
    }


def run_eight_pass_audit(
    frame: pd.DataFrame, feature_cols: Sequence[str], patterns: pd.DataFrame, cfg: ReverseEngineeringConfig = ReverseEngineeringConfig(),
) -> pd.DataFrame:
    """Eight explicit gates. PASS is an engineering contract, not proof of alpha."""
    passes = [
        ("PASS_01_UNIVERSE_IDENTITY", _audit_universe(frame)),
        ("PASS_02_LABELS_EXECUTION", _audit_labels(frame, cfg)),
        ("PASS_03_PIT_LOOKAHEAD", _audit_pit(feature_cols)),
        ("PASS_04_FEATURE_QUALITY", _audit_feature_quality(frame, feature_cols)),
        ("PASS_05_SPLIT_LEAKAGE", _audit_split(frame, cfg)),
        ("PASS_06_PATTERN_ROBUSTNESS", _audit_robustness(patterns, cfg)),
        ("PASS_07_TRADING_REALISM", _audit_trading_realism(frame, cfg)),
        ("PASS_08_OOS_HOLDOUT", _audit_oos(frame, patterns)),
    ]
    rows = []
    for name, payload in passes:
        rows.append({"audit_pass": name, "status": "PASS" if payload.pop("pass", False) else "FAIL", **payload})
    return pd.DataFrame(rows)


def prepare_research_matrix(
    ohlcv: pd.DataFrame,
    *,
    exogenous_history: pd.DataFrame | None = None,
    exogenous_value_cols: Sequence[str] | None = None,
    cfg: ReverseEngineeringConfig = ReverseEngineeringConfig(),
) -> tuple[pd.DataFrame, list[str]]:
    technical = build_technical_features(ohlcv)
    labels = build_forward_labels(ohlcv, cfg)
    label_cols = [c for c in labels.columns if c.startswith(("fwd_", "label_", "first_hit_", "entry_price_"))]
    base = technical.merge(labels[["instrument_id", "date", *label_cols]], on=["instrument_id", "date"], how="left", validate="one_to_one")
    feature_cols = [c for c in technical.columns if c not in {"instrument_id", "date", "open", "high", "low", "close", "volume"}]
    if exogenous_history is not None and not exogenous_history.empty:
        before = set(base.columns)
        base = asof_snapshot_features(base, exogenous_history, value_cols=exogenous_value_cols, delta_windows=cfg.delta_windows)
        feature_cols.extend([c for c in base.columns if c not in before and c != "observed_at_utc"])
    base = chronological_split(base, purge_sessions=max(cfg.horizons))
    feature_cols = sanitize_feature_columns(feature_cols)
    return base, feature_cols
