from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "data/reference/V21.1_TCT_EXPLOSIF_CONFIG.json"


def _num(df: pd.DataFrame, *cols: str) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for col in cols:
        if col in df.columns:
            x = pd.to_numeric(df[col], errors="coerce")
            out = out.where(out.notna(), x)
    return out


def _rank(s: pd.Series, higher: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    p = x.rank(pct=True, method="average") * 100.0
    return p if higher else 100.0 - p


def _bool_score(s: pd.Series) -> pd.Series:
    text = s.astype(str).str.strip().str.lower()
    observed = ~text.isin({"", "nan", "none", "<na>"})
    value = text.isin({"true", "1", "yes", "oui", "y"}).astype(float) * 100.0
    return value.where(observed)


def _direct100(df: pd.DataFrame, *cols: str) -> pd.Series:
    return _num(df, *cols).clip(0.0, 100.0)


def _piecewise(x: pd.Series, xp: list[float], fp: list[float]) -> pd.Series:
    raw = pd.to_numeric(x, errors="coerce")
    vals = np.interp(raw.to_numpy(float), xp, fp, left=fp[0], right=fp[-1])
    return pd.Series(vals, index=x.index).where(raw.notna()).clip(0.0, 100.0)


def _rsi_sweet(x: pd.Series) -> pd.Series:
    rsi = pd.to_numeric(x, errors="coerce")
    out = pd.Series(np.nan, index=x.index, dtype=float)
    mid = rsi.between(50, 65)
    out.loc[mid] = 100.0
    low = rsi < 50
    high = rsi > 65
    out.loc[low] = (100.0 - (50.0 - rsi.loc[low]) * 4.0).clip(0.0, 100.0)
    out.loc[high] = (100.0 - (rsi.loc[high] - 65.0) * 5.0).clip(0.0, 100.0)
    return out


def _gap_score(x: pd.Series) -> pd.Series:
    gap = pd.to_numeric(x, errors="coerce")
    out = pd.Series(np.nan, index=x.index, dtype=float)
    out.loc[gap < 0] = 0.0
    out.loc[gap.between(0, 1.0, inclusive="left")] = 25.0
    out.loc[gap.between(1.0, 2.0, inclusive="left")] = 55.0
    out.loc[gap.between(2.0, 6.0, inclusive="both")] = 100.0
    out.loc[gap.between(6.0, 10.0, inclusive="right")] = 80.0
    out.loc[gap > 10.0] = 45.0
    return out


def _fcf_yield_score(x: pd.Series) -> pd.Series:
    return _piecewise(x, [-10, 0, 2, 5, 8, 15], [0, 20, 45, 70, 100, 100])


def _ev_ebitda_score(x: pd.Series) -> pd.Series:
    raw = pd.to_numeric(x, errors="coerce")
    valid = raw.where(raw > 0)
    return _piecewise(valid, [3, 6, 10, 15, 25, 40], [100, 100, 90, 60, 20, 0])


def _peg_score(pe: pd.Series, growth_pct: pd.Series) -> pd.Series:
    p = pd.to_numeric(pe, errors="coerce")
    g = pd.to_numeric(growth_pct, errors="coerce")
    peg = (p / g).where((p > 0) & (g > 0))
    return _piecewise(peg, [0.1, 0.5, 0.8, 1.2, 2.0, 3.0], [90, 100, 100, 75, 35, 0])


def _drawdown_score(x: pd.Series) -> pd.Series:
    return _piecewise(x, [-70, -50, -40, -30, -20, -10, 0], [0, 10, 25, 45, 70, 90, 100])


def _volatility_score(x: pd.Series) -> pd.Series:
    v = pd.to_numeric(x, errors="coerce")
    out = pd.Series(np.nan, index=x.index, dtype=float)
    out.loc[v <= 10] = 25.0
    out.loc[v.between(10, 20, inclusive="right")] = 55.0
    out.loc[v.between(20, 45, inclusive="right")] = 100.0
    out.loc[v.between(45, 70, inclusive="right")] = 70.0
    out.loc[v > 70] = 35.0
    return out


def _ma_alignment(df: pd.DataFrame) -> pd.Series:
    close = _num(df, "last_close", "close")
    ma20 = _num(df, "ma20", "sma20", "mm20")
    ma50 = _num(df, "ma50", "sma50", "mm50")
    ma200 = _num(df, "ma200", "sma200", "mm200")
    observed = close.notna() & ma20.notna() & ma50.notna() & ma200.notna()
    score = ((close > ma20) & (ma20 > ma50) & (ma50 > ma200)).astype(float) * 100.0
    return score.where(observed)


def _weighted(scores: dict[str, tuple[pd.Series, float]]) -> tuple[pd.Series, pd.Series]:
    if not scores:
        raise ValueError("empty score component")
    idx = next(iter(scores.values()))[0].index
    numerator = pd.Series(0.0, index=idx)
    denominator = pd.Series(0.0, index=idx)
    total = sum(float(w) for _, w in scores.values())
    for s, w in scores.values():
        x = pd.to_numeric(s, errors="coerce").clip(0.0, 100.0)
        numerator += x.fillna(0.0) * float(w)
        denominator += x.notna().astype(float) * float(w)
    score = (numerator / denominator.replace(0.0, np.nan)).clip(0.0, 100.0)
    coverage = (denominator / total).clip(0.0, 1.0)
    return score, coverage


def _derived(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    pe = _num(out, "per_forward_v21", "per_forward")
    sector = out.get("sector_v21", pd.Series("UNCLASSIFIED", index=out.index)).fillna("UNCLASSIFIED").astype(str)
    valid_pe = pe.where(pe > 0)
    sector_median = valid_pe.groupby(sector).transform("median")
    global_median = float(valid_pe.median()) if valid_pe.notna().any() else math.nan
    sector_median = sector_median.fillna(global_median)
    out["per_vs_sector_pct_v211"] = ((valid_pe / sector_median) - 1.0) * 100.0
    out.loc[valid_pe.isna() | sector_median.le(0), "per_vs_sector_pct_v211"] = np.nan
    growth = _num(out, "earnings_growth_v21_pct")
    out["peg_v211"] = (valid_pe / growth).where((valid_pe > 0) & (growth > 0))
    ev = _num(out, "enterprise_value_v21")
    ebitda = _num(out, "ebitda_v21")
    out["ev_to_ebitda_v211"] = (ev / ebitda).where((ev > 0) & (ebitda > 0))
    vol = _num(out, "volume")
    vavg = _num(out, "volume_avg_20d")
    out["rvol_v211"] = _num(out, "rvol20", "volume_acceleration_20d")
    fallback_rvol = (vol / vavg).where((vol >= 0) & (vavg > 0))
    out["rvol_v211"] = out["rvol_v211"].where(out["rvol_v211"].notna(), fallback_rvol)
    out["gap_up_pct_v211"] = _num(out, "gap_up_pct_v211", "gap_up_pct", "open_gap_pct")
    return out


def compute_scores(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = _derived(df)
    technical, technical_cov = _weighted({
        "breakout": (_bool_score(out.get("breakout_20d_flag", pd.Series(index=out.index, dtype=object))), .20),
        "ma_alignment": (_ma_alignment(out), .15),
        "reversal": (_bool_score(out.get("positive_reversal_flag", pd.Series(index=out.index, dtype=object))), .15),
        "macd": (_rank(_num(out, "macd_hist"), True), .15),
        "rsi": (_rsi_sweet(_num(out, "rsi14")), .10),
        "stoch": (_bool_score(out.get("stoch_bull_cross_flag", pd.Series(index=out.index, dtype=object))), .08),
        "relative_strength": (_rank(_num(out, "relative_strength"), True), .10),
        "gap": (_gap_score(_num(out, "gap_up_pct_v211")), .07),
    })

    rumor_cap = float(cfg["gates"]["rumor_only_event_cap"])
    confirmed_mna = _direct100(out, "mna_event_score", "mna_confirmed_score")
    rumor = _direct100(out, "mna_rumor_score").clip(upper=rumor_cap)
    mna = confirmed_mna.where(confirmed_mna.notna(), rumor)
    catalyst, catalyst_cov = _weighted({
        "mna": (mna, .25),
        "earnings": (_direct100(out, "earnings_catalyst_score"), .20),
        "guidance": (_direct100(out, "guidance_revision_score"), .15),
        "major_contract": (_direct100(out, "major_contract_score", "contract_catalyst_score"), .20),
        "regulatory": (_direct100(out, "regulatory_catalyst_score"), .10),
        "fda": (_direct100(out, "fda_catalyst_score"), .10),
    })

    news, news_cov = _weighted({
        "instrument_news": (_direct100(out, "news_catalyst_score", "gdelt_catalyst_discovery_score"), .50),
        "sector_news": (_direct100(out, "sector_news_score"), .30),
        "sector_momentum": (_rank(_num(out, "sector_perf_5d_pct", "sector_momentum_5d_pct"), True), .20),
    })

    valuation_discount = _direct100(out, "valuation_discount_score")
    if valuation_discount.notna().sum() == 0:
        discount = -_num(out, "per_vs_sector_pct_v211")
        valuation_discount = _piecewise(discount, [-50, 0, 20, 40, 70], [0, 40, 70, 90, 100])
    valuation, valuation_cov = _weighted({
        "sector_discount": (valuation_discount, .50),
        "peg": (_peg_score(_num(out, "per_forward_v21"), _num(out, "earnings_growth_v21_pct")), .20),
        "fcf": (_fcf_yield_score(_num(out, "fcf_yield_v21")), .20),
        "ev_ebitda": (_ev_ebitda_score(_num(out, "ev_to_ebitda_v211")), .10),
    })

    rvol = _num(out, "rvol_v211")
    short_float = _num(out, "short_percent_float_pct")
    amf_short = _num(out, "amf_public_short_pct", "amf_short_pct", "public_short_pct")
    short_ratio = _num(out, "short_ratio")
    volume_squeeze, volume_cov = _weighted({
        "relative_volume": (_piecewise(rvol, [0, .75, 1, 1.5, 2, 3, 5], [0, 10, 25, 60, 85, 100, 100]), .35),
        "short_float": (_piecewise(short_float, [0, 3, 5, 10, 15, 25], [0, 20, 40, 70, 100, 100]), .25),
        "amf_public_short": (_piecewise(amf_short, [0, .5, 1, 2, 4], [0, 35, 55, 80, 100]), .15),
        "gap": (_gap_score(_num(out, "gap_up_pct_v211")), .15),
        "days_to_cover": (_piecewise(short_ratio, [0, 1, 3, 5, 8, 12], [0, 20, 50, 75, 100, 100]), .10),
    })

    revisions, revisions_cov = _weighted({
        "analyst_momentum": (_direct100(out, "analyst_momentum_score"), .35),
        "consensus_delta": (_rank(_num(out, "consensus_delta_4w"), True), .25),
        "net_upgrades": (_rank(_num(out, "net_upgrades_30d_v21"), True), .20),
        "broker_revision": (_rank(_num(out, "broker_weighted_revision_30d"), True), .20),
    })

    liq = _num(out, "liquidity_percentile", "iquidity_percentile")
    liq01 = liq.where(liq <= 1.0, liq / 100.0)
    liq_score = (liq01 * 100.0).clip(0, 100)
    risk, risk_cov = _weighted({
        "liquidity": (liq_score, .45),
        "drawdown": (_drawdown_score(_num(out, "max_drawdown_1y")), .35),
        "volatility": (_volatility_score(_num(out, "volatility_20d")), .20),
    })

    buyback = _direct100(out, "buyback_score", "corporate_event_score")
    if buyback.notna().sum() == 0 and "buyback_signal" in out.columns:
        buyback = _bool_score(out["buyback_signal"])
    insider_balance = _num(out, "insider_buyers_90d") - _num(out, "insider_sellers_90d")
    insider_shadow, insider_shadow_cov = _weighted({
        "insider_net_buy": (_rank(_num(out, "insider_net_buy_90d"), True), .45),
        "insider_balance": (_rank(insider_balance, True), .20),
        "legacy_insider_score": (_direct100(out, "insider_score"), .25),
        "insider_cluster": (_bool_score(out.get("insider_cluster_flag", pd.Series(index=out.index, dtype=object))), .10),
    })
    out["tct_insider_shadow_score"] = insider_shadow.round(2)
    out["tct_insider_shadow_coverage"] = insider_shadow_cov.round(3)
    corporate, corporate_cov = _weighted({"buyback_or_corporate_event": (buyback, 1.00)})

    macro, macro_cov = _weighted({
        "topdown": (_direct100(out, "action_topdown_score"), .70),
        "sentiment_regime": (_direct100(out, "sentiment_regime_score"), .30),
    })

    pillars = {
        "technical_impulse": (technical, technical_cov),
        "catalyst_event": (catalyst, catalyst_cov),
        "volume_squeeze": (volume_squeeze, volume_cov),
        "news_sector": (news, news_cov),
        "valuation_relative": (valuation, valuation_cov),
        "analyst_revision": (revisions, revisions_cov),
        "risk_liquidity": (risk, risk_cov),
        "corporate_insider": (corporate, corporate_cov),
        "macro_rotation": (macro, macro_cov),
    }
    for name, (score, cov) in pillars.items():
        out[f"tct_{name}_score"] = score.round(2)
        out[f"tct_{name}_coverage"] = cov.round(3)

    weights = cfg["pillar_weights"]
    numerator = pd.Series(0.0, index=out.index)
    denominator = pd.Series(0.0, index=out.index)
    coverage_numerator = pd.Series(0.0, index=out.index)
    total = float(sum(weights.values()))
    for name, w in weights.items():
        score, pillar_cov = pillars[name]
        numerator += score.fillna(0.0) * float(w)
        denominator += score.notna().astype(float) * float(w)
        coverage_numerator += pillar_cov.fillna(0.0) * float(w)
    raw = numerator / denominator.replace(0.0, np.nan)
    coverage = (coverage_numerator / total).clip(0.0, 1.0)

    syn = cfg["synergy"]
    bonus = pd.Series(0.0, index=out.index)
    bonus += np.where(
        (technical >= float(syn["technical_volume_min"])) & (volume_squeeze >= float(syn["technical_volume_min"])),
        float(syn["technical_volume_bonus"]), 0.0,
    )
    bonus += np.where(
        (catalyst >= float(syn["catalyst_news_min"])) & (news >= float(syn["catalyst_news_min"])),
        float(syn["catalyst_news_bonus"]), 0.0,
    )
    breakout = _bool_score(out.get("breakout_20d_flag", pd.Series(index=out.index, dtype=object))).ge(100)
    squeeze = short_float.ge(float(syn["short_squeeze_short_pct_min"])) & rvol.ge(float(syn["short_squeeze_rvol_min"])) & breakout
    bonus += np.where(squeeze, float(syn["short_squeeze_bonus"]), 0.0)
    bonus = bonus.clip(upper=float(syn["max_total_bonus"]))

    penalties = pd.Series(0.0, index=out.index)
    profit_warning = out.get("profit_warning_flag", pd.Series(False, index=out.index)).astype(str).str.lower().isin({"true", "1", "yes"})
    penalties += np.where(profit_warning, float(cfg["gates"]["profit_warning_penalty"]), 0.0)
    topdown_gate = out.get("action_topdown_gate", pd.Series("", index=out.index)).astype(str).str.upper()
    penalties += np.where(topdown_gate.eq("BLOCK_BUY"), float(cfg["gates"]["topdown_block_penalty"]), 0.0)
    penalties += np.where(topdown_gate.eq("REVIEW_ONLY"), float(cfg["gates"]["topdown_review_penalty"]), 0.0)
    gap = _num(out, "gap_up_pct_v211")
    penalties += np.where(
        gap.gt(float(cfg["gates"]["extreme_gap_chase_above_pct"])),
        float(cfg["gates"]["extreme_gap_chase_penalty"]), 0.0,
    )
    penalties += np.where(_num(out, "max_drawdown_1y").lt(float(cfg["gates"]["max_drawdown_review_below_pct"])), -5.0, 0.0)

    out["tct_score_raw"] = raw.round(2)
    out["tct_score_coverage"] = coverage.round(3)
    out["tct_synergy_bonus"] = bonus.round(2)
    out["tct_risk_penalty"] = penalties.round(2)
    out["tct_score"] = (raw + bonus + penalties).clip(0.0, 100.0).round(2)
    out["tct_short_squeeze_pattern"] = squeeze
    if "tct_probability_20d_calibrated" not in out.columns:
        out["tct_probability_20d_calibrated"] = np.nan
    out["tct_probability_status"] = np.where(
        _num(out, "tct_probability_20d_calibrated").notna(), "CALIBRATED", "NOT_CALIBRATED"
    )
    return out


def apply_decisions(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    th = cfg["thresholds"]
    cov_cfg = cfg["coverage"]
    gates = cfg["gates"]
    score = _num(out, "tct_score")
    cov = _num(out, "tct_score_coverage")
    identity_raw = _num(out, "v182_ticker_validation_confidence_pct")
    identity = identity_raw.where(identity_raw <= 1.0, identity_raw / 100.0)
    pea_high = out.get("pea_confidence", pd.Series("", index=out.index)).astype(str).str.upper().str.startswith("HIGH")
    liq = _num(out, "liquidity_percentile", "iquidity_percentile")
    liq = liq.where(liq <= 1.0, liq / 100.0)
    probability = _num(out, "tct_probability_20d_calibrated")

    decisions: list[str] = []
    reasons: list[str] = []
    for i in out.index:
        s = score.loc[i]
        c = cov.loc[i]
        dec, reason = "REJECT_TCT", "SCORE_BELOW_SCAN"
        if pd.notna(s) and s >= float(th["COEUR_TCT_EXPLOSIF"]):
            dec, reason = "COEUR_TCT_EXPLOSIF", "SCORE"
        elif pd.notna(s) and s >= float(th["SATELLITE_TCT_EXPLOSIF"]):
            dec, reason = "SATELLITE_TCT_EXPLOSIF", "SCORE"
        elif pd.notna(s) and s >= float(th["SCAN_TCT_EXPLOSIF"]):
            dec, reason = "SCAN_TCT_EXPLOSIF", "SCORE"

        if pd.isna(c) or c < float(cov_cfg["min_score"]):
            dec, reason = "REJECT_TCT", "DATA_COVERAGE_LOW"
        elif dec == "COEUR_TCT_EXPLOSIF" and c < float(cov_cfg["min_core"]):
            dec, reason = "SATELLITE_TCT_EXPLOSIF", "DATA_COVERAGE_CORE_GATE"
        elif dec == "SATELLITE_TCT_EXPLOSIF" and c < float(cov_cfg["min_satellite"]):
            dec, reason = "SCAN_TCT_EXPLOSIF", "DATA_COVERAGE_SATELLITE_GATE"

        if pd.isna(identity.loc[i]) or identity.loc[i] < float(cov_cfg["identity_min"]):
            dec, reason = "REJECT_TCT", "IDENTITY_CONFIDENCE"
        if "pea_confidence" in out.columns and not bool(pea_high.loc[i]):
            dec, reason = "REJECT_TCT", "PEA_ELIGIBILITY_NOT_HIGH"
        if pd.notna(liq.loc[i]) and liq.loc[i] < float(gates["min_liquidity_percentile_any"]):
            dec, reason = "REJECT_TCT", "LIQUIDITY_BOTTOM_5PCT"
        elif dec == "COEUR_TCT_EXPLOSIF" and pd.isna(liq.loc[i]):
            dec, reason = "SATELLITE_TCT_EXPLOSIF", "LIQUIDITY_DATA_REQUIRED_FOR_CORE"
        elif dec == "COEUR_TCT_EXPLOSIF" and liq.loc[i] < float(gates["min_liquidity_percentile_core"]):
            dec, reason = "SATELLITE_TCT_EXPLOSIF", "LIQUIDITY_CORE_GATE"

        cat = _num(out, "tct_catalyst_event_score").loc[i]
        tech = _num(out, "tct_technical_impulse_score").loc[i]
        vol = _num(out, "tct_volume_squeeze_score").loc[i]
        if dec == "COEUR_TCT_EXPLOSIF" and not (
            (pd.notna(cat) and cat >= 65.0) or
            (pd.notna(tech) and pd.notna(vol) and tech >= 75.0 and vol >= 75.0)
        ):
            dec, reason = "SATELLITE_TCT_EXPLOSIF", "CORE_CONFIRMATION_MISSING"

        if dec == "COEUR_TCT_EXPLOSIF" and pd.notna(probability.loc[i]) and probability.loc[i] < float(cfg["objective"]["probability_target"]):
            dec, reason = "SATELLITE_TCT_EXPLOSIF", "CALIBRATED_PROBABILITY_BELOW_65"
        decisions.append(dec)
        reasons.append(reason)

    out["tct_decision"] = decisions
    out["tct_decision_reason"] = reasons
    actionable = out["tct_decision"].isin({"COEUR_TCT_EXPLOSIF", "SATELLITE_TCT_EXPLOSIF", "SCAN_TCT_EXPLOSIF"})
    out["tct_rank"] = _num(out, "tct_score").where(actionable).rank(method="min", ascending=False).astype("Int64")
    out["tct_top20"] = pd.to_numeric(out["tct_rank"], errors="coerce").le(20) & actionable
    return out


def _resolve_input(root: Path, cfg: dict) -> Path:
    for rel in cfg["input_candidates"]:
        p = root / rel
        if p.exists():
            return p
    raise FileNotFoundError("No V21.0 Actions input found for TCT Explosif")


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    cfg = json.loads((root / CONFIG.relative_to(ROOT)).read_text(encoding="utf-8"))
    source = _resolve_input(root, cfg)
    df = pd.read_csv(source, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    expected = int(cfg["canonical_universe_size"])
    if len(df) != expected:
        raise RuntimeError(f"TCT universe gate: expected {expected}, found {len(df)}")
    out = apply_decisions(compute_scores(df, cfg), cfg)

    output = root / cfg["output"]["full"]
    top20 = root / cfg["output"]["top20"]
    audit_path = root / cfg["output"]["audit"]
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, sep=";", index=False, encoding="utf-8-sig")
    out.loc[out["tct_top20"]].sort_values(["tct_rank", "tct_score"]).to_csv(top20, sep=";", index=False, encoding="utf-8-sig")

    counts = out["tct_decision"].value_counts(dropna=False).to_dict()
    probability_calibrated = bool(_num(out, "tct_probability_20d_calibrated").notna().any())
    audit = {
        "passed": True,
        "version": cfg["version"],
        "execution_mode": cfg["execution_mode"],
        "integration_mode": cfg["integration_mode"],
        "source": str(source.relative_to(root)),
        "rows": int(len(out)),
        "score_coverage_mean": round(float(_num(out, "tct_score_coverage").mean()), 4),
        "decision_counts": {str(k): int(v) for k, v in counts.items()},
        "top20_rows": int(out["tct_top20"].sum()),
        "probability_calibrated": probability_calibrated,
        "probability_gate_active": probability_calibrated,
        "production_weights_modified": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("V21_1_TCT_EXPLOSIF_OK", audit)
    return audit


if __name__ == "__main__":
    build()
