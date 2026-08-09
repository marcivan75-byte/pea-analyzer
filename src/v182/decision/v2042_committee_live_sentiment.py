from __future__ import annotations

import json
import numpy as np
import pandas as pd

from v182.decision import v2042_specialized_committee as base


def _action_components(source: pd.DataFrame, scored: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = scored.copy()
    fam = base._family_actions(source)
    for name, series in fam.items():
        out[f"component_{name}"] = series.round(2)
    t1 = pd.to_numeric(out["score_t1"], errors="coerce").fillna(50)
    hw = cfg["actions"]["horizon_family_weights"]
    for hz in ("CT", "MT", "LT"):
        for name, weight in hw[hz].items():
            component = t1 if name == "t1" else fam[name]
            out[f"contrib_{hz.lower()}_{name}"] = (component * weight).round(2)
    pre_macro = sum(pd.to_numeric(out[f"contrib_ct_{name}"], errors="coerce") for name in hw["CT"])
    out["contrib_ct_macro_effect"] = (pd.to_numeric(out["score_ct_raw"], errors="coerce") - pre_macro).round(2)

    sw = cfg["actions"]["short_weights"]
    execution_risk = (.55 * base._pct(base._num(source, "volatility_20d")) + .45 * (100 - fam["structure"])).clip(0, 100)
    short_parts = {
        "bearish_t1": 100 - t1,
        "overvaluation": 100 - fam["value"],
        "quality_weakness": 100 - fam["quality"],
        "analyst_negative": 100 - fam["analyst"],
        "fundamental_fragility": 100 - fam["risk"],
        "execution_risk_penalty": execution_risk,
    }
    for name, component in short_parts.items():
        sign = -1.0 if name == "execution_risk_penalty" else 1.0
        out[f"contrib_short_{name}"] = (sign * sw[name] * component).round(2)
    return out


def _score_etf(source: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = source.copy()
    comp = base._etf_components(out)
    for name, series in comp.items():
        out[f"component_{name}"] = pd.to_numeric(series, errors="coerce").round(2)

    hw = cfg["etf"]["horizon_weights"]
    for hz, weights in hw.items():
        pre = pd.Series(0.0, index=out.index)
        for name, weight in weights.items():
            contrib = pd.to_numeric(comp[name], errors="coerce").fillna(50) * weight
            out[f"contrib_{hz.lower()}_{name}"] = contrib.round(2)
            pre = pre + contrib
        out[f"score_{hz.lower()}_raw_pre_macro"] = pre.clip(0, 100).round(2)
        out[f"score_{hz.lower()}_raw"] = out[f"score_{hz.lower()}_raw_pre_macro"]

    macro = base._macro_multiplier_actions(out, cfg)
    out["macro_multiplier_ct"] = macro.round(3)
    out["score_ct_raw"] = (pd.to_numeric(out["score_ct_raw_pre_macro"], errors="coerce") * macro).clip(0, 100).round(2)
    out["contrib_ct_macro_effect"] = (out["score_ct_raw"] - out["score_ct_raw_pre_macro"]).round(2)

    lo = float(cfg["actions"]["macro"]["min_multiplier"])
    hi = float(cfg["actions"]["macro"]["max_multiplier"])
    regime = ((hi - macro) / max(hi - lo, 1e-9) * 100.0).clip(0, 100)
    out["sentiment_regime_score"] = regime.round(2)

    sw = cfg["etf"]["short_weights"]
    short_components = {
        "bearish_momentum": 100 - comp["momentum"],
        "risk_fragility": 100 - comp["risk_efficiency"],
        "structural_weakness": 100 - comp["v9"],
        "liquidity_weakness": 100 - comp["liquidity"],
        "tracking_cost_weakness": 100 - (.60 * comp["tracking"] + .40 * comp["ter"]),
        "regime": regime,
    }
    short = pd.Series(0.0, index=out.index)
    for name, component in short_components.items():
        contrib = pd.to_numeric(component, errors="coerce").fillna(50) * sw[name]
        out[f"contrib_short_{name}"] = contrib.round(2)
        short = short + contrib
    short = short.clip(0, 100)
    out["score_short_raw"] = short.round(2)

    cal = cfg["etf"]["score_calibration"]
    rw, pw = cal["raw_weight"], cal["percentile_weight"]
    for hz in ["ct", "mt", "lt"]:
        raw = pd.to_numeric(out[f"score_{hz}_raw"], errors="coerce")
        out[f"score_{hz}"] = (rw * raw + pw * base._pct(raw)).clip(0, 100).round(2)
    srw = cal["short_raw_weight"]
    out["score_short"] = (srw * short + (1 - srw) * base._pct(short)).clip(0, 100).round(2)

    conf = (base._num(out, "ticker_confidence_pct").fillna(0) / 100).clip(0, 1)
    aum = base._num(out, "fund_total_assets_eur_m").fillna(base._num(out, "aum_m"))
    spread = base._num(out, "spread_pct") if "spread_pct" in out.columns else pd.Series(np.nan, index=out.index)
    liquid_ok = (aum.isna() | (aum >= cfg["etf"]["hard_gates"]["min_aum_eur_m"])) & (spread.isna() | (spread <= cfg["etf"]["hard_gates"]["max_spread_pct"]))
    for hz in ["CT", "MT", "LT"]:
        col = f"score_{hz.lower()}"
        out[f"decision_{hz.lower()}"] = base._decision(out[col], cfg["etf"]["thresholds"][hz], conf, liquid_ok)
        low_conf = conf < cfg["etf"]["identity_min_buy"]
        out.loc[low_conf, f"decision_{hz.lower()}"] = np.where(out.loc[low_conf, col] >= cfg["etf"]["thresholds"][hz]["WATCH"], "REVIEW", "REJECT")
        out[f"rank_{hz.lower()}"] = out[col].rank(method="min", ascending=False).astype(int)
    st = cfg["etf"]["thresholds"]["SHORT"]
    out["decision_short"] = np.select(
        [out["score_short"] >= st["SHORT_CANDIDATE"], out["score_short"] >= st["WATCH_SHORT"]],
        ["SHORT_CANDIDATE", "WATCH_SHORT"],
        default="NO_SHORT",
    )
    out["rank_short"] = out["score_short"].rank(method="min", ascending=False).astype(int)
    lim = cfg["etf"]["selection_limits"]
    for hz in ["ct", "mt", "lt"]:
        out[f"selection_{hz}"] = (out[f"rank_{hz}"] <= lim[hz.upper()]) & out[f"decision_{hz}"].isin(["BUY_CANDIDATE", "WATCH"])
    out["selection_short"] = (out["rank_short"] <= lim["SHORT"]) & out["decision_short"].isin(["SHORT_CANDIDATE", "WATCH_SHORT"])
    out["execution"] = "RESEARCH_ONLY"
    out["v2042_version"] = "V20.4.2_SPECIALIZED_SENTIMENT"
    return out


def _sentiment_snapshot(actions: pd.DataFrame) -> dict:
    row = actions.iloc[0]
    def val(name):
        v = row.get(name)
        return None if pd.isna(v) else v
    return {
        "status": val("sentiment_data_status"),
        "fear_greed_index": val("fear_greed_index"),
        "fear_greed_rating": val("fear_greed_rating"),
        "fear_greed_asof": val("fear_greed_asof"),
        "fear_greed_source": val("fear_greed_source"),
        "aaii_bullish_pct": val("aaii_bullish_pct"),
        "aaii_neutral_pct": val("aaii_neutral_pct"),
        "aaii_bearish_pct": val("aaii_bearish_pct"),
        "aaii_bull_bear_spread": val("aaii_bull_bear_spread"),
        "aaii_asof": val("aaii_asof"),
        "aaii_source": val("aaii_source"),
    }


def main() -> None:
    cfg = json.loads(base.CONFIG_PATH.read_text(encoding="utf-8"))
    if cfg.get("smart_money_enabled"):
        raise RuntimeError("V20.4.2 must run without Smart Money")
    a_path = next((p for p in base.A_IN_CANDIDATES if p.exists()), None)
    if a_path is None:
        raise RuntimeError("Missing Actions input")
    if not base.E_IN.exists():
        raise RuntimeError(f"Missing validated ETF input: {base.E_IN}")

    actions = pd.read_csv(a_path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    etfs = pd.read_csv(base.E_IN, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(actions) != 1829 or actions["isin"].nunique() != 1829:
        raise RuntimeError("Actions canonical gate failed")
    if len(etfs) != 102 or etfs["isin"].nunique() != 102:
        raise RuntimeError("ETF validated canonical gate failed")
    required_sentiment = {"fear_greed_index", "aaii_bullish_pct", "aaii_bearish_pct", "aaii_bull_bear_spread", "sentiment_data_status"}
    if not required_sentiment.issubset(actions.columns) or actions[list(required_sentiment)].isna().any().any():
        raise RuntimeError("V20.4.2 sentiment inputs missing")
    if not required_sentiment.issubset(etfs.columns) or etfs[list(required_sentiment)].isna().any().any():
        raise RuntimeError("V20.4.2 ETF sentiment inputs missing")

    a = _action_components(actions, base.score_actions(actions, cfg), cfg)
    e = _score_etf(etfs, cfg)
    base.A_OUT.parent.mkdir(parents=True, exist_ok=True)
    base.AUDIT.parent.mkdir(parents=True, exist_ok=True)
    a.to_csv(base.A_OUT, sep=";", index=False, encoding="utf-8-sig")
    e.to_csv(base.E_OUT, sep=";", index=False, encoding="utf-8-sig")

    sentiment = _sentiment_snapshot(actions)
    lines = [
        "# V20.4.2 Committee — independent strategy selectors + live sentiment",
        "",
        "Smart Money: **OFF**  ",
        "Execution: **RESEARCH_ONLY**  ",
        f"Fear & Greed: **{sentiment['fear_greed_index']}** ({sentiment['fear_greed_rating']}) — {sentiment['fear_greed_asof']}  ",
        f"AAII: Bull {sentiment['aaii_bullish_pct']}% / Bear {sentiment['aaii_bearish_pct']}% / Spread {sentiment['aaii_bull_bear_spread']} — {sentiment['aaii_asof']}",
        "",
        "## Actions PEA",
    ]
    for hz in ["ct", "mt", "lt", "short"]:
        lines += [f"### {hz.upper()}", str(a[f"decision_{hz}"].value_counts().to_dict()), f"Selection count: {int(a[f'selection_{hz}'].sum())}", ""]
    lines += ["## ETF PEA"]
    for hz in ["ct", "mt", "lt", "short"]:
        lines += [f"### {hz.upper()}", str(e[f"decision_{hz}"].value_counts().to_dict()), f"Selection count: {int(e[f'selection_{hz}'].sum())}", ""]
    base.SUMMARY.write_text("\n".join(lines), encoding="utf-8")

    audit = {
        "passed": True,
        "version": "V20.4.2_SPECIALIZED_SENTIMENT",
        "actions_rows": len(a),
        "etf_rows": len(e),
        "actions_unique_isin": int(a["isin"].nunique()),
        "etf_unique_isin": int(e["isin"].nunique()),
        "smart_money_enabled": False,
        "live_order_execution_enabled": False,
        "market_sentiment": sentiment,
        "actions_decisions": {hz: a[f"decision_{hz}"].value_counts().to_dict() for hz in ["ct", "mt", "lt", "short"]},
        "etf_decisions": {hz: e[f"decision_{hz}"].value_counts().to_dict() for hz in ["ct", "mt", "lt", "short"]},
        "actions_selection_counts": {hz: int(a[f"selection_{hz}"].sum()) for hz in ["ct", "mt", "lt", "short"]},
        "etf_selection_counts": {hz: int(e[f"selection_{hz}"].sum()) for hz in ["ct", "mt", "lt", "short"]},
        "actions_macro_multiplier": {"min": float(a["macro_multiplier_ct"].min()), "max": float(a["macro_multiplier_ct"].max())},
        "etf_macro_multiplier": {"min": float(e["macro_multiplier_ct"].min()), "max": float(e["macro_multiplier_ct"].max())},
    }
    base.AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print("V20.4.2_SPECIALIZED_SENTIMENT_OK", json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
