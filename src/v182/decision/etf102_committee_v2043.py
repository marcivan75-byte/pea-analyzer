from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "data/reference/V20.4.3_ETF102_CONFIG.json"
IN = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
SMART = ROOT / "outputs/V18.3_PEA_ETF_SMART_MONEY_SHADOW.csv"
OUT = ROOT / "outputs/V20.4.3_ETF102_COMMITTEE.csv"
AUDIT = ROOT / "outputs/audit/V20.4.3_ETF102_COMMITTEE_AUDIT.json"
SUMMARY = ROOT / "outputs/V20.4.3_ETF102_COMMITTEE_SUMMARY.md"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _rank(s: pd.Series, higher: bool = True) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    p = s.rank(pct=True, method="average") * 100.0
    return p if higher else 100.0 - p


def _rsi_zone(s: pd.Series, target: float, slope: float) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    out = (100.0 - (x - target).abs() * slope).clip(0, 100)
    return out.where(x.notna())


def _weighted_available(parts: dict[str, tuple[pd.Series, float]]) -> tuple[pd.Series, pd.Series]:
    if not parts:
        raise RuntimeError("No weighted parts")
    index = next(iter(parts.values()))[0].index
    numerator = pd.Series(0.0, index=index)
    denominator = pd.Series(0.0, index=index)
    total = sum(float(w) for _, w in parts.values())
    for series, weight in parts.values():
        s = pd.to_numeric(series, errors="coerce")
        w = float(weight)
        numerator += s.fillna(0.0) * w
        denominator += s.notna().astype(float) * w
    score = numerator / denominator.replace(0.0, np.nan)
    coverage = denominator / total if total > 0 else pd.Series(0.0, index=index)
    return score.clip(0, 100), coverage.clip(0, 1)


def _component(df: pd.DataFrame, name: str, cfg: dict) -> tuple[pd.Series, pd.Series]:
    rules = cfg["component_rules"][name]
    inverse = set(cfg.get("inverse_metrics", []))
    parts: dict[str, tuple[pd.Series, float]] = {}
    for field, weight in rules.items():
        raw = _num(df, field)
        if field == "rsi14":
            scored = _rsi_zone(raw, float(cfg.get("rsi_target", 60)), float(cfg.get("rsi_slope", 3)))
        elif field == "diversification_direct_score":
            scored = raw.clip(0, 100)
        else:
            scored = _rank(raw, higher=field not in inverse)
        parts[field] = (scored, float(weight))
    return _weighted_available(parts)


def _macro_multiplier(df: pd.DataFrame, cfg: dict) -> pd.Series:
    mult = pd.Series(1.0, index=df.index)
    vix = _num(df, "macro_vix")
    mult *= np.where(vix >= 35, .86, np.where(vix >= 28, .91, np.where(vix <= 15, 1.03, 1.0)))
    fg = _num(df, "fear_greed_index")
    mult *= np.where(fg < 25, .88, np.where(fg > 75, .94, np.where((fg >= 45) & (fg <= 65), 1.02, 1.0)))
    bear = _num(df, "aaii_bearish_pct")
    bull = _num(df, "aaii_bullish_pct")
    spread = _num(df, "aaii_bull_bear_spread")
    contrarian = (bear > 50) | (spread < -20)
    greed = (bull > 55) | (spread > 30)
    mult *= np.where(contrarian, 1.05, np.where(greed, .93, 1.0))
    if "funnel_macro_multiplier" in df.columns:
        funnel = _num(df, "funnel_macro_multiplier").fillna(1.0).clip(.90, 1.10)
        mult *= funnel
    return mult.clip(float(cfg["macro"]["min_multiplier"]), float(cfg["macro"]["max_multiplier"]))


def _bonus_malus(df: pd.DataFrame, cfg: dict) -> pd.Series:
    out = pd.Series(0.0, index=df.index)
    stars = _num(df, "morningstar_rating")
    for key, value in cfg["bonuses_maluses"]["morningstar"].items():
        out += np.where(stars.eq(float(key)), float(value), 0.0)
    risk = _num(df, "risk_indicator")
    out += np.where(risk.ge(6), float(cfg["bonuses_maluses"]["risk_indicator_ge_6"]), 0.0)
    spread = _num(df, "spread_pct")
    out += np.where(spread.le(.20), float(cfg["bonuses_maluses"]["spread_le_020"]), 0.0)
    out += np.where(spread.gt(.70), float(cfg["bonuses_maluses"]["spread_gt_070"]), 0.0)
    out += np.where(spread.gt(1.20), float(cfg["bonuses_maluses"]["spread_gt_120"]), 0.0)
    return out


def _merge_smart_money(df: pd.DataFrame) -> pd.DataFrame:
    if not SMART.exists():
        return df
    sm = pd.read_csv(SMART, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    keep = [c for c in [
        "isin", "ifs_raw", "ifs_effective", "smart_money_confidence",
        "institutional_flow_label", "flow_status", "flow_history_snapshots",
        "flow_observations", "smart_money_data_status", "smart_money_source_completeness"
    ] if c in sm.columns]
    if "isin" not in keep:
        return df
    sm = sm[keep].drop_duplicates("isin", keep="last")
    conflicts = [c for c in keep if c != "isin" and c in df.columns]
    if conflicts:
        df = df.drop(columns=conflicts)
    return df.merge(sm, on="isin", how="left")


def _decision(score: float, thresholds: dict) -> str:
    if score >= float(thresholds["BUY_CANDIDATE"]):
        return "BUY_CANDIDATE"
    if score >= float(thresholds["WATCH"]):
        return "WATCH"
    if score >= float(thresholds["REVIEW"]):
        return "REVIEW"
    return "REJECT"


def build(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if len(df) != int(cfg["universe_size"]) or df["isin"].astype(str).nunique() != int(cfg["universe_size"]):
        raise RuntimeError("V20.4.3 ETF102 canonical universe gate failed")
    out = _merge_smart_money(df.copy())

    components: dict[str, pd.Series] = {}
    component_coverage: dict[str, pd.Series] = {}
    for name in cfg["component_rules"]:
        score, cov = _component(out, name, cfg)
        components[name] = score
        component_coverage[name] = cov
        out[f"component_{name}"] = score.round(2)
        out[f"component_{name}_coverage"] = cov.round(3)

    bonus = _bonus_malus(out, cfg)
    out["etf102_bonus_malus"] = bonus.round(2)
    macro = _macro_multiplier(out, cfg)
    out["macro_multiplier_ct"] = macro.round(3)
    coverage_floor = float(cfg["coverage"]["coverage_penalty_floor"])

    for hz in ("CT", "MT", "LT"):
        weights = cfg["horizon_weights"][hz]
        parts = {k: (components[k], float(w)) for k, w in weights.items()}
        raw, coverage = _weighted_available(parts)
        adjusted = raw * (coverage_floor + (1.0 - coverage_floor) * coverage) + bonus
        if hz == "CT":
            adjusted *= macro
        adjusted = adjusted.clip(0, 100)
        out[f"score_{hz.lower()}_raw"] = adjusted.round(2)
        out[f"weight_coverage_{hz.lower()}"] = coverage.round(3)
        for key, weight in weights.items():
            denom = pd.Series(0.0, index=out.index)
            for k, w in weights.items():
                denom += components[k].notna().astype(float) * float(w)
            effective_weight = np.where(components[key].notna() & denom.gt(0), float(weight) / denom, 0.0)
            out[f"effective_weight_{hz.lower()}_{key}"] = np.round(effective_weight, 4)
            out[f"contrib_{hz.lower()}_{key}"] = (components[key].fillna(0.0) * effective_weight).round(2)

    # Short score also renormalizes only observed evidence.
    tracking_cost, tracking_cost_cov = _weighted_available({
        "tracking": (components["tracking"], .60),
        "cost": (components["cost"], .40),
    })
    regime = ((1.0 - (macro - float(cfg["macro"]["min_multiplier"])) /
              (float(cfg["macro"]["max_multiplier"]) - float(cfg["macro"]["min_multiplier"]))) * 100.0).clip(0, 100)
    short_inputs = {
        "bearish_momentum": 100.0 - components["momentum"],
        "risk_fragility": 100.0 - components["risk_efficiency"],
        "liquidity_weakness": 100.0 - components["liquidity"],
        "tracking_cost_weakness": 100.0 - tracking_cost,
        "regime": regime,
    }
    short_parts = {k: (short_inputs[k], float(w)) for k, w in cfg["short_weights"].items()}
    short_raw, short_cov = _weighted_available(short_parts)
    short_raw = (short_raw * (coverage_floor + (1.0 - coverage_floor) * short_cov)).clip(0, 100)
    out["score_short_raw"] = short_raw.round(2)
    out["weight_coverage_short"] = short_cov.round(3)
    out["sentiment_regime_score"] = regime.round(2)
    out["component_tracking_cost"] = tracking_cost.round(2)
    out["component_tracking_cost_coverage"] = tracking_cost_cov.round(3)
    for key, weight in cfg["short_weights"].items():
        denom = pd.Series(0.0, index=out.index)
        for k, w in cfg["short_weights"].items():
            denom += short_inputs[k].notna().astype(float) * float(w)
        ew = np.where(short_inputs[key].notna() & denom.gt(0), float(weight) / denom, 0.0)
        out[f"effective_weight_short_{key}"] = np.round(ew, 4)
        out[f"contrib_short_{key}"] = (short_inputs[key].fillna(0.0) * ew).round(2)

    cal = cfg["score_calibration"]
    rw, pw = float(cal["raw_weight"]), float(cal["percentile_weight"])
    for hz in ("ct", "mt", "lt"):
        raw = _num(out, f"score_{hz}_raw")
        pct = _rank(raw)
        out[f"score_{hz}"] = (rw * raw + pw * pct).clip(0, 100).round(2)
    srw = float(cal["short_raw_weight"])
    out["score_short"] = (srw * short_raw + (1.0 - srw) * _rank(short_raw)).clip(0, 100).round(2)

    identity = (_num(out, "ticker_confidence_pct") / 100.0).clip(0, 1)
    aum = _num(out, "fund_total_assets_eur_m")
    min_buy_cov = float(cfg["coverage"]["min_weight_coverage_buy"])
    min_watch_cov = float(cfg["coverage"]["min_weight_coverage_watch"])
    id_min = float(cfg["coverage"]["identity_min_buy"])
    sm_conf = _num(out, "smart_money_confidence")
    ifs = _num(out, "ifs_effective")
    sm_cfg = cfg["smart_money"]

    for hz in ("ct", "mt", "lt"):
        score = _num(out, f"score_{hz}")
        cov = _num(out, f"weight_coverage_{hz}")
        decisions: list[str] = []
        reasons: list[str] = []
        sm_gates: list[str] = []
        for i in out.index:
            s = float(score.loc[i]) if pd.notna(score.loc[i]) else 0.0
            dec = _decision(s, cfg["thresholds"][hz.upper()])
            reason = "SCORE"
            if identity.loc[i] < id_min:
                dec, reason = "REVIEW", "IDENTITY_CONFIDENCE"
            elif cov.loc[i] < min_watch_cov:
                dec, reason = "REVIEW", "DATA_COVERAGE_LOW"
            elif dec == "BUY_CANDIDATE" and cov.loc[i] < min_buy_cov:
                dec, reason = "WATCH", "DATA_COVERAGE_BUY_GATE"
            elif dec == "BUY_CANDIDATE" and cfg["coverage"].get("aum_required_for_buy", True) and pd.isna(aum.loc[i]):
                dec, reason = "WATCH", "AUM_REQUIRED_FOR_BUY"

            gate = "NONE"
            if pd.notna(sm_conf.loc[i]) and pd.notna(ifs.loc[i]) and sm_conf.loc[i] >= float(sm_cfg["min_confidence_for_gate"]):
                if ifs.loc[i] <= float(sm_cfg["block_buy_ifs_lte"]):
                    if dec == "BUY_CANDIDATE":
                        dec, reason = "REVIEW", "SMART_MONEY_NEGATIVE_BLOCK"
                    gate = "BLOCK_BUY"
                elif ifs.loc[i] <= float(sm_cfg["review_ifs_lte"]):
                    if dec == "BUY_CANDIDATE":
                        dec, reason = "WATCH", "SMART_MONEY_NEGATIVE_REVIEW"
                    gate = "REVIEW_BUY"
            decisions.append(dec)
            reasons.append(reason)
            sm_gates.append(gate)
        out[f"decision_{hz}"] = decisions
        out[f"decision_reason_{hz}"] = reasons
        out[f"smart_money_gate_{hz}"] = sm_gates
        out[f"rank_{hz}"] = score.rank(method="min", ascending=False).astype(int)

    st = cfg["thresholds"]["SHORT"]
    out["decision_short"] = np.select(
        [out["score_short"] >= float(st["SHORT_CANDIDATE"]), out["score_short"] >= float(st["WATCH_SHORT"])],
        ["SHORT_CANDIDATE", "WATCH_SHORT"], default="NO_SHORT"
    )
    out["rank_short"] = out["score_short"].rank(method="min", ascending=False).astype(int)

    limits = cfg["selection_limits"]
    for hz in ("ct", "mt", "lt"):
        out[f"selection_{hz}"] = (out[f"rank_{hz}"] <= int(limits[hz.upper()])) & out[f"decision_{hz}"].isin(["BUY_CANDIDATE", "WATCH"])
    out["selection_short"] = (out["rank_short"] <= int(limits["SHORT"])) & out["decision_short"].isin(["SHORT_CANDIDATE", "WATCH_SHORT"])
    out["execution"] = "RESEARCH_ONLY"
    out["v2043_version"] = cfg["version"]
    out["legacy_266_used"] = False
    return out


def main() -> None:
    if not IN.exists():
        raise RuntimeError(f"Missing ETF102 enriched input: {IN}")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    df = pd.read_csv(IN, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    out = build(df, cfg)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep=";", index=False, encoding="utf-8-sig")

    coverage = {}
    for field in [
        "ter_pct", "fund_total_assets_eur_m", "spread_pct", "morningstar_rating",
        "diversification_direct_score", "tracking_error_1y_pct", "tracking_error_3y_pct",
        "tracking_error_5y_pct", "weight_coverage_ct", "weight_coverage_mt", "weight_coverage_lt"
    ]:
        coverage[field] = int(pd.to_numeric(out.get(field), errors="coerce").notna().sum()) if field in out else 0
    audit = {
        "passed": True,
        "version": cfg["version"],
        "rows": len(out),
        "unique_isin": int(out["isin"].nunique()),
        "legacy_266_used": False,
        "missing_data_policy": cfg["missing_data_policy"],
        "coverage_count_of_102": coverage,
        "mean_weight_coverage": {hz: round(float(pd.to_numeric(out[f"weight_coverage_{hz}"], errors="coerce").mean()), 4) for hz in ["ct", "mt", "lt", "short"]},
        "decisions": {hz: out[f"decision_{hz}"].value_counts().to_dict() for hz in ["ct", "mt", "lt", "short"]},
        "selection_counts": {hz: int(out[f"selection_{hz}"].sum()) for hz in ["ct", "mt", "lt", "short"]},
        "smart_money_rows_present": int(pd.to_numeric(out.get("ifs_effective"), errors="coerce").notna().sum()) if "ifs_effective" in out else 0,
        "smart_money_positive_score_boost_allowed": False,
        "execution": "RESEARCH_ONLY",
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# V20.4.3 ETF102 Committee",
        "",
        "Universe: **102 validated ETF only**  ",
        "Legacy 266: **OFF / forbidden**  ",
        "Missing data: **weights renormalized on observed evidence; no neutral 50**  ",
        "Smart Money: **negative high-confidence risk gate only; positive boost disabled**  ",
        "Execution: **RESEARCH_ONLY**",
        "",
    ]
    for hz in ["ct", "mt", "lt", "short"]:
        lines += [f"## {hz.upper()}", str(out[f"decision_{hz}"].value_counts().to_dict()), f"Top selection count: {int(out[f'selection_{hz}'].sum())}", ""]
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print("V20.4.3_ETF102_COMMITTEE_OK", json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
