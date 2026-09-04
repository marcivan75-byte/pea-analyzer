from __future__ import annotations

import numpy as np
import pandas as pd

from research import retro_5d_patterns as r
from v182.hebdo.meta_price_history import load_2010_2026

_FULL = None
_orig_retained = r.retained_universe
_orig_matched = r.matched_controls


def retained_capture(df: pd.DataFrame) -> pd.DataFrame:
    global _FULL
    _FULL = df
    return _orig_retained(df)


def exact_winner_episodes(_univ: pd.DataFrame) -> pd.DataFrame:
    if _FULL is None:
        raise RuntimeError("FULL_FEATURE_FRAME_NOT_CAPTURED")
    f = _FULL.copy()
    f["ord_full"] = f.groupby("ticker").cumcount()
    hits = f.loc[f["ret_fwd5_pct"] > 20.0].copy()
    hits["prev_ord"] = hits.groupby("ticker")["ord_full"].shift(1)
    hits["new_episode"] = (hits["prev_ord"].isna() | ((hits["ord_full"] - hits["prev_ord"]) > 5)).astype(int)
    hits["episode_id"] = hits.groupby("ticker")["new_episode"].cumsum()
    idx = hits.groupby(["ticker", "episode_id"])["ret_fwd5_pct"].idxmax()
    ep = hits.loc[idx].copy()
    qa = (
        np.isfinite(ep["close"].astype(float))
        & np.isfinite(ep["close_t5"].astype(float))
        & (ep["close"] >= 1.0)
        & (ep["volume"] >= 5000)
        & (ep["ret_fwd5_pct"] <= 50.0)
        & (~r._round_ratio_suspect(ep["future_ratio"]))
    )
    ep = ep.loc[qa].sort_values(["date", "ticker"]).reset_index(drop=True)
    if len(ep) != 5859:
        raise SystemExit(f"BLOCK_EXACT_5D_QA_POPULATION_EXPECTED_5859_GOT_{len(ep)}")
    return ep


def matched_controls_safe(univ: pd.DataFrame, episodes: pd.DataFrame, n_controls: int = 3) -> pd.DataFrame:
    needed = list(dict.fromkeys(r.FEATURES + r.MATCH_FEATURES))
    ok = np.isfinite(episodes[needed].to_numpy(float)).all(axis=1)
    return _orig_matched(univ, episodes.loc[ok].copy(), n_controls=n_controls)


r.retained_universe = retained_capture
r.winner_episodes = exact_winner_episodes
r.matched_controls = matched_controls_safe

if __name__ == "__main__":
    df = load_2010_2026(
        "inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet",
        "inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json",
        "data/cache/actions",
    )
    r.main(df, "outputs/retro_5d_patterns")
