from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "data/reference/V20.4.2_SCORING_CONFIG.json"
A_IN_CANDIDATES = [ROOT / "outputs/V20.4.1_GITOK_ACTIONS_1829_DECISIONS.csv", ROOT / "outputs/V20.4_GITOK_ACTIONS_1829_DECISIONS.csv"]
E_IN = ROOT / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv"
A_OUT = ROOT / "outputs/V20.4.2_ACTIONS_SPECIALIZED_DECISIONS.csv"
E_OUT = ROOT / "outputs/V20.4.2_ETF_SPECIALIZED_DECISIONS.csv"
SUMMARY = ROOT / "outputs/V20.4.2_COMMITTEE_SUMMARY.md"
AUDIT = ROOT / "outputs/audit/V20.4.2_SPECIALIZED_AUDIT.json"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _pct(s: pd.Series, higher: bool = True) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    p = s.rank(pct=True, method="average") * 100.0
    return (p if higher else 100.0 - p).fillna(50.0).clip(0, 100)


def _weighted(parts: list[tuple[pd.Series, float]]) -> pd.Series:
    total = sum(w for _, w in parts)
    if total <= 0:
        raise RuntimeError("Invalid zero weight sum")
    return sum(v * w for v, w in parts) / total


def _rsi_zone(s: pd.Series, center: float = 60.0, slope: float = 3.0) -> pd.Series:
    return (100.0 - (pd.to_numeric(s, errors="coerce") - center).abs() * slope).clip(0, 100).fillna(50)


def _family_actions(df: pd.DataFrame) -> dict[str, pd.Series]:
    names = ["quality", "value", "momentum", "analyst", "risk", "structure"]
    existing = {n: pd.to_numeric(df.get(f"v2041_score_{n}_100"), errors="coerce") for n in names}
    if all(s.notna().sum() >= len(df) * 0.5 for s in existing.values()):
        return {k: v.fillna(50) for k, v in existing.items()}
    quality = _weighted([(_pct(_num(df, "roe")), 1.0), (_pct(_num(df, "roa")), .7), (_pct(_num(df, "marge_ebit")), .8), (_pct(_num(df, "marge_nette")), .7), (_pct(_num(df, "croiss_ca_3y")), .7), (_pct(_num(df, "croiss_eps_3y")), .8), (_pct(_num(df, "dette_ebitda"), False), .8), (_pct(_num(df, "debt_to_equity"), False), .5)])
    value = _weighted([(_pct(_num(df, "per_forward"), False), 1.0), (_pct(_num(df, "per_ttm"), False), .8), (_pct(_num(df, "pb"), False), .5), (_pct(_num(df, "ev_ebit"), False), .8), (_pct(_num(df, "fcf_yield")), 1.0), (_pct(_num(df, "per_vs_sector_pct"), False), .6)])
    momentum = _weighted([(_pct(_num(df, "perf_1m_pct")), 1.0), (_pct(_num(df, "perf_3m_pct")), 1.15), (_pct(_num(df, "perf_6m_pct")), 1.1), (_pct(_num(df, "relative_strength")), 1.0), (_pct(_num(df, "macd_hist")), .8), (_rsi_zone(_num(df, "rsi14")), .45), (_pct(_num(df, "rvol20")), .6)])
    analyst = _weighted([(_pct(_num(df, "analyst_momentum_score")), 1.2), (_pct(_num(df, "consensus_score_100")), 1.0), (_pct(_num(df, "target_upside_pct")), .9), (_pct(_num(df, "weighted_target_revision_30d_pct")), .9), (_pct(_num(df, "weighted_consensus_delta_30d")), .9), (_pct(_num(df, "revision_breadth_30d")), .8), (_pct(_num(df, "net_upgrades_30d")), .7), (_pct(_num(df, "consensus_confidence")), .5)])
    risk = _weighted([(_pct(_num(df, "volatility_20d"), False), 1.0), (_pct(_num(df, "volatility_60d"), False), .8), (_pct(_num(df, "max_drawdown_1y")), 1.1), (_pct(_num(df, "beta"), False), .5), (_pct(_num(df, "asymmetry")), 1.0)])
    structure = _weighted([(_pct(_num(df, "market_cap")), .9), (_pct(_num(df, "volume")), .8)])
    return {"quality": quality, "value": value, "momentum": momentum, "analyst": analyst, "risk": risk, "structure": structure}


def _action_t1(df: pd.DataFrame, config: dict) -> tuple[pd.Series, dict[str, pd.Series]]:
    w = config["actions"]["t1_weights"]
    close, bb_u, bb_l = _num(df, "last_close"), _num(df, "bb_upper"), _num(df, "bb_lower")
    width = (bb_u - bb_l).replace(0, np.nan)
    pct_b = ((close - bb_l) / width).replace([np.inf, -np.inf], np.nan)
    bb = pd.Series(np.select([pct_b >= 1.02, (pct_b >= .85) & (pct_b < 1.02), (pct_b <= .20) & (_num(df, "positive_reversal_flag").fillna(0) > 0), pct_b <= .20], [100, 82, 92, 68], default=55), index=df.index, dtype=float)
    k, d = _num(df, "stoch_k"), _num(df, "stoch_d")
    if k.notna().any() and d.notna().any():
        cross = k - d
        st = pd.Series(np.select([(k < 35) & (cross > 0), (k < 55) & (cross > 0), (k <= 80) & (cross > 0), k > 90], [95, 82, 70, 25], default=50), index=df.index, dtype=float)
    else:
        st = pd.Series(50.0, index=df.index)
    macd, sig, hist = _num(df, "macd"), _num(df, "macd_signal"), _num(df, "macd_hist")
    macd_score = (50 + _pct(hist) * .30 + np.where((macd > sig) & (hist > 0), 20, np.where((macd < sig) & (hist < 0), -20, 0))).clip(0, 100)
    rvol = _num(df, "rvol20")
    volume = pd.Series(np.select([rvol >= 2.0, rvol >= 1.8, rvol >= 1.4, rvol >= 1.2], [100, 92, 78, 65], default=45), index=df.index, dtype=float)
    rev, ma20, ma50 = _num(df, "positive_reversal_flag").fillna(0), _num(df, "mm20"), _num(df, "mm50")
    breakout = pd.Series(np.where((close > bb_u) & (close > ma20), 100, np.where((rev > 0) & (close > ma20), 90, np.where((close > ma20) & (ma20 > ma50), 75, 45))), index=df.index, dtype=float)
    rel = _pct(_num(df, "relative_strength"))
    p1 = _pct(_num(df, "perf_1m_pct")); p5 = _pct(_num(df, "perf_5d_pct")) if "perf_5d_pct" in df.columns else p1; p1d = _pct(_num(df, "perf_1d_pct")) if "perf_1d_pct" in df.columns else p1
    accel = (.45 * p1d + .35 * p5 + .20 * p1).clip(0, 100)
    t1 = w["bollinger"] * bb + w["stochastic"] * st + w["macd"] * macd_score + w["volume"] * volume + w["reversal_breakout"] * breakout + w["relative_strength"] * rel + w["short_acceleration"] * accel
    boost = np.where(rvol >= 2.0, 1.08, np.where(rvol >= 1.8, 1.05, 1.0))
    return (t1 * boost).clip(0, 100), {"t1_bollinger": bb, "t1_stochastic": st, "t1_macd": macd_score, "t1_volume": volume, "t1_reversal_breakout": breakout, "t1_relative_strength": rel, "t1_acceleration": accel}


def _macro_multiplier_actions(df: pd.DataFrame, config: dict) -> pd.Series:
    mult = pd.Series(1.0, index=df.index)
    vix = _num(df, "macro_vix")
    if vix.notna().any():
        mult = pd.Series(np.where(vix >= 35, .86, np.where(vix >= 28, .91, np.where(vix <= 15, 1.03, 1.0))), index=df.index, dtype=float)
    fg = _num(df, "fear_greed_index")
    if fg.notna().any():
        mult *= np.where(fg < 25, .88, np.where(fg > 75, .94, np.where((fg >= 45) & (fg <= 65), 1.02, 1.0)))
    aaii_bear, spread = _num(df, "aaii_bearish_pct"), _num(df, "aaii_bull_bear_spread")
    contrarian, greed = (aaii_bear > 50) | (spread < -20), (_num(df, "aaii_bullish_pct") > 55) | (spread > 30)
    mult *= np.where(contrarian, 1.05, np.where(greed, .93, 1.0))
    return mult.clip(config["actions"]["macro"]["min_multiplier"], config["actions"]["macro"]["max_multiplier"])


def _decision(score: pd.Series, thresholds: dict, identity: pd.Series | None = None, liquidity_ok: pd.Series | None = None) -> np.ndarray:
    out = np.select([score >= thresholds["BUY_CANDIDATE"], score >= thresholds["WATCH"], score >= thresholds["REVIEW"]], ["BUY_CANDIDATE", "WATCH", "REVIEW"], default="REJECT").astype(object)
    if identity is not None:
        low = identity < .92
        out = np.where(low & (score >= thresholds["WATCH"]), "REVIEW", np.where(low, "REJECT", out))
    if liquidity_ok is not None:
        out = np.where(~liquidity_ok, "REJECT", out)
    return out


def score_actions(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    out = df.copy(); fam = _family_actions(out); t1, t1parts = _action_t1(out, config)
    for name, s in t1parts.items(): out[name] = s.round(2)
    out["score_t1"] = t1.round(2)
    hw = config["actions"]["horizon_family_weights"]
    ct = hw["CT"]["quality"] * fam["quality"] + hw["CT"]["value"] * fam["value"] + hw["CT"]["t1"] * t1 + hw["CT"]["analyst"] * fam["analyst"] + hw["CT"]["risk"] * fam["risk"] + hw["CT"]["structure"] * fam["structure"]
    macro = _macro_multiplier_actions(out, config); ct_post = (ct * macro).clip(0, 100)
    mt = sum(hw["MT"][k] * fam[k] for k in hw["MT"]).clip(0, 100); lt = sum(hw["LT"][k] * fam[k] for k in hw["LT"]).clip(0, 100)
    sw = config["actions"]["short_weights"]; execution_risk = (.55 * _pct(_num(out, "volatility_20d")) + .45 * (100 - fam["structure"])).clip(0, 100)
    short = (sw["bearish_t1"] * (100 - t1) + sw["overvaluation"] * (100 - fam["value"]) + sw["quality_weakness"] * (100 - fam["quality"]) + sw["analyst_negative"] * (100 - fam["analyst"]) + sw["fundamental_fragility"] * (100 - fam["risk"]) - sw["execution_risk_penalty"] * execution_risk + 10).clip(0, 100)
    out["score_ct_raw"], out["score_mt_raw"], out["score_lt_raw"], out["score_short_raw"] = ct_post.round(2), mt.round(2), lt.round(2), short.round(2)
    cal = config["actions"]["score_calibration"]; rw, pw = cal["raw_weight"], cal["percentile_weight"]
    out["score_ct"] = (rw * ct_post + pw * _pct(ct_post)).clip(0, 100).round(2); out["score_mt"] = (rw * mt + pw * _pct(mt)).clip(0, 100).round(2); out["score_lt"] = (rw * lt + pw * _pct(lt)).clip(0, 100).round(2)
    srw = cal["short_raw_weight"]; out["score_short"] = (srw * short + (1 - srw) * _pct(short)).clip(0, 100).round(2); out["macro_multiplier_ct"] = macro.round(3)
    identity = _num(out, "identity_confidence").fillna(_num(out, "v182_ticker_validation_confidence_pct") / 100).fillna(0); liquid = fam["structure"] >= 25
    for hz in ["CT", "MT", "LT"]:
        col = f"score_{hz.lower()}"; out[f"decision_{hz.lower()}"] = _decision(out[col], config["actions"]["thresholds"][hz], identity, liquid); out[f"rank_{hz.lower()}"] = out[col].rank(method="min", ascending=False).astype(int)
    st = config["actions"]["thresholds"]["SHORT"]; out["decision_short"] = np.select([out["score_short"] >= st["SHORT_CANDIDATE"], out["score_short"] >= st["WATCH_SHORT"]], ["SHORT_CANDIDATE", "WATCH_SHORT"], default="NO_SHORT"); out["rank_short"] = out["score_short"].rank(method="min", ascending=False).astype(int)
    lim = config["actions"]["selection_limits"]
    for hz in ["ct", "mt", "lt"]: out[f"selection_{hz}"] = (out[f"rank_{hz}"] <= lim[hz.upper()]) & out[f"decision_{hz}"].isin(["BUY_CANDIDATE", "WATCH"])
    out["selection_short"] = (out["rank_short"] <= lim["SHORT"]) & out["decision_short"].isin(["SHORT_CANDIDATE", "WATCH_SHORT"]); out["execution"] = "RESEARCH_ONLY"; out["v2042_version"] = "V20.4.2_SPECIALIZED"
    return out


def _etf_components(df: pd.DataFrame) -> dict[str, pd.Series]:
    mom = .12 * _pct(_num(df, "perf_1m_pct")) + .24 * _pct(_num(df, "perf_3m_pct")) + .20 * _pct(_num(df, "perf_6m_pct")) + .16 * _pct(_num(df, "perf_1y_pct")) + .10 * _pct(_num(df, "relative_strength")) + .08 * _pct(_num(df, "macd_hist")) + .05 * _rsi_zone(_num(df, "rsi14")) + .05 * _pct(_num(df, "rvol20"))
    risk = .25 * _pct(_num(df, "volatility_20d"), False) + .25 * _pct(_num(df, "volatility_60d"), False) + .30 * _pct(_num(df, "max_drawdown_1y")) + .20 * _pct(_num(df, "risk_indicator"), False)
    assets = _num(df, "fund_total_assets_eur_m").fillna(_num(df, "aum_m")); liquidity = .55 * _pct(assets) + .30 * _pct(_num(df, "volume")) + .15 * _pct(_num(df, "holdings"))
    tracking = .50 * _pct(_num(df, "tracking_error_1y_pct"), False) + .30 * _pct(_num(df, "tracking_error_3y_pct"), False) + .20 * _pct(_num(df, "tracking_error_5y_pct"), False)
    ter = _pct(_num(df, "ter_pct"), False); diversification = .55 * _pct(_num(df, "holdings")) + .45 * _pct(assets)
    v9 = pd.to_numeric(df["score_v9"], errors="coerce") if "score_v9" in df.columns else (pd.to_numeric(df["Score V9"], errors="coerce") if "Score V9" in df.columns else pd.Series(50.0, index=df.index)); v9 = v9.fillna(50).clip(0, 100)
    stars = _num(df, "morningstar_rating"); ms = pd.Series(50.0, index=df.index); ms.loc[stars.eq(3)] = 60; ms.loc[stars.eq(4)] = 75; ms.loc[stars.ge(5)] = 90
    esg = _pct(_num(df, "esg_score")) if "esg_score" in df.columns else pd.Series(50.0, index=df.index)
    return {"momentum": mom, "risk_efficiency": risk, "liquidity": liquidity, "tracking": tracking, "ter": ter, "diversification": diversification, "v9": v9, "morningstar": ms, "esg": esg}


def score_etf(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    out = df.copy(); comp = _etf_components(out); hw = config["etf"]["horizon_weights"]
    for hz, w in hw.items(): out[f"score_{hz.lower()}_raw"] = sum(comp[k] * wt for k, wt in w.items()).clip(0, 100)
    sw = config["etf"]["short_weights"]; short = (sw["bearish_momentum"] * (100 - comp["momentum"]) + sw["risk_fragility"] * (100 - comp["risk_efficiency"]) + sw["structural_weakness"] * (100 - comp["v9"]) + sw["liquidity_weakness"] * (100 - comp["liquidity"]) + sw["tracking_cost_weakness"] * (100 - (.60 * comp["tracking"] + .40 * comp["ter"])) + sw["regime"] * 50).clip(0, 100); out["score_short_raw"] = short.round(2)
    cal = config["etf"]["score_calibration"]; rw, pw = cal["raw_weight"], cal["percentile_weight"]
    for hz in ["ct", "mt", "lt"]: out[f"score_{hz}"] = (rw * out[f"score_{hz}_raw"] + pw * _pct(out[f"score_{hz}_raw"])).clip(0, 100).round(2)
    srw = cal["short_raw_weight"]; out["score_short"] = (srw * short + (1 - srw) * _pct(short)).clip(0, 100).round(2)
    conf = (_num(out, "ticker_confidence_pct").fillna(0) / 100).clip(0, 1); aum = _num(out, "fund_total_assets_eur_m").fillna(_num(out, "aum_m")); spread = _num(out, "spread_pct") if "spread_pct" in out.columns else pd.Series(np.nan, index=out.index); liquid_ok = (aum.isna() | (aum >= config["etf"]["hard_gates"]["min_aum_eur_m"])) & (spread.isna() | (spread <= config["etf"]["hard_gates"]["max_spread_pct"]))
    for hz in ["CT", "MT", "LT"]:
        col = f"score_{hz.lower()}"; out[f"decision_{hz.lower()}"] = _decision(out[col], config["etf"]["thresholds"][hz], conf, liquid_ok); low = conf < config["etf"]["identity_min_buy"]; out.loc[low, f"decision_{hz.lower()}"] = np.where(out.loc[low, col] >= config["etf"]["thresholds"][hz]["WATCH"], "REVIEW", "REJECT"); out[f"rank_{hz.lower()}"] = out[col].rank(method="min", ascending=False).astype(int)
    st = config["etf"]["thresholds"]["SHORT"]; out["decision_short"] = np.select([out["score_short"] >= st["SHORT_CANDIDATE"], out["score_short"] >= st["WATCH_SHORT"]], ["SHORT_CANDIDATE", "WATCH_SHORT"], default="NO_SHORT"); out["rank_short"] = out["score_short"].rank(method="min", ascending=False).astype(int)
    lim = config["etf"]["selection_limits"]
    for hz in ["ct", "mt", "lt"]: out[f"selection_{hz}"] = (out[f"rank_{hz}"] <= lim[hz.upper()]) & out[f"decision_{hz}"].isin(["BUY_CANDIDATE", "WATCH"])
    out["selection_short"] = (out["rank_short"] <= lim["SHORT"]) & out["decision_short"].isin(["SHORT_CANDIDATE", "WATCH_SHORT"]); out["execution"] = "RESEARCH_ONLY"; out["v2042_version"] = "V20.4.2_SPECIALIZED"
    return out


def main() -> None:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if cfg.get("smart_money_enabled"): raise RuntimeError("V20.4.2 must run without Smart Money")
    a_path = next((p for p in A_IN_CANDIDATES if p.exists()), None)
    if a_path is None: raise RuntimeError("Missing Actions input")
    if not E_IN.exists(): raise RuntimeError(f"Missing validated ETF input: {E_IN}")
    actions = pd.read_csv(a_path, sep=";", dtype=object, encoding="utf-8-sig"); etfs = pd.read_csv(E_IN, sep=";", dtype=object, encoding="utf-8-sig")
    if len(actions) != 1829 or actions["isin"].nunique() != 1829: raise RuntimeError("Actions canonical gate failed")
    if len(etfs) != 102 or etfs["isin"].nunique() != 102: raise RuntimeError("ETF validated canonical gate failed")
    a, e = score_actions(actions, cfg), score_etf(etfs, cfg)
    A_OUT.parent.mkdir(parents=True, exist_ok=True); AUDIT.parent.mkdir(parents=True, exist_ok=True)
    a.to_csv(A_OUT, sep=";", index=False, encoding="utf-8-sig"); e.to_csv(E_OUT, sep=";", index=False, encoding="utf-8-sig")
    lines = ["# V20.4.2 Committee — independent strategy selectors", "", "Smart Money: **OFF**  ", "Execution: **RESEARCH_ONLY**", "", "## Actions PEA"]
    for hz in ["ct", "mt", "lt", "short"]: lines += [f"### {hz.upper()}", str(a[f"decision_{hz}"].value_counts().to_dict()), f"Selection count: {int(a[f'selection_{hz}'].sum())}", ""]
    lines += ["## ETF PEA"]
    for hz in ["ct", "mt", "lt", "short"]: lines += [f"### {hz.upper()}", str(e[f"decision_{hz}"].value_counts().to_dict()), f"Selection count: {int(e[f'selection_{hz}'].sum())}", ""]
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    audit = {"passed": True, "version": "V20.4.2_SPECIALIZED", "actions_rows": len(a), "etf_rows": len(e), "actions_unique_isin": int(a["isin"].nunique()), "etf_unique_isin": int(e["isin"].nunique()), "smart_money_enabled": False, "live_order_execution_enabled": False, "actions_decisions": {hz: a[f"decision_{hz}"].value_counts().to_dict() for hz in ["ct", "mt", "lt", "short"]}, "etf_decisions": {hz: e[f"decision_{hz}"].value_counts().to_dict() for hz in ["ct", "mt", "lt", "short"]}, "actions_selection_counts": {hz: int(a[f"selection_{hz}"].sum()) for hz in ["ct", "mt", "lt", "short"]}, "etf_selection_counts": {hz: int(e[f"selection_{hz}"].sum()) for hz in ["ct", "mt", "lt", "short"]}}
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print("V20.4.2_SPECIALIZED_OK", json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
