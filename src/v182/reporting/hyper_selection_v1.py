from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

from v182.reporting.ci_light_source_context_v4 import collect_ci_light_context
from v182.risk.beta_metrics import load_cached_prices, to_returns


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/HYPER_SELECTION_V1.json")
SELECTED = Path("outputs/committee_master/HYPER_SELECTION_V1.csv")
PENDING = Path("outputs/committee_master/HYPER_SELECTION_PENDING_V1.csv")
REJECTED = Path("outputs/committee_master/HYPER_SELECTION_REJECTED_V1.csv")
AUDIT = Path("outputs/audit/HYPER_SELECTION_V1.json")
STABILITY_LEDGER = Path("state/hyper_selection/HYPER_STABILITY_LEDGER.csv")


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _rank(series: pd.Series, *, reverse: bool = False) -> pd.Series:
    numeric = _num(series)
    return numeric.rank(pct=True, ascending=not reverse, na_option="keep") * 100.0


def _field(frame: pd.DataFrame, name: str) -> pd.Series:
    return frame[name] if name in frame else pd.Series(np.nan, index=frame.index)


def _positive(value: object) -> bool:
    return str(value).strip().upper() in {"BUY", "STRONG_BUY", "ACHETER", "RENFORCER"}


def _core_scores(frame: pd.DataFrame, asset: str) -> pd.DataFrame:
    out = frame.copy()
    close = _num(_field(out, "last_close"))
    rsi = _num(_field(out, "rsi14"))
    volume = _num(_field(out, "volume")).where(lambda values: values > 0)
    out["H01_TRADINGVIEW"] = np.nan
    out["H02_BOURSORAMA"] = np.nan
    out["H03_RSI"] = pd.cut(rsi, [-np.inf, 30, 45, 65, 70, np.inf], labels=[30, 70, 100, 55, 25]).astype(float)
    out["RSI_STATE"] = pd.cut(rsi, [-np.inf, 30, 45, 70, np.inf], labels=["SURVENTE", "ZONE_BASSE", "NORMAL", "SURACHAT"]).astype(object)
    drawdown_field = "max_drawdown_1y" if "max_drawdown_1y" in out else "max_drawdown_1y_pct"
    beta_field = "beta" if asset == "ACTION" else "direct_beta3y"
    risk = pd.concat([
        _rank(_field(out, "volatility_60d"), reverse=True),
        _rank(_num(_field(out, drawdown_field)).abs(), reverse=True),
        (100.0 - (_num(_field(out, beta_field)) - 1.0).abs() * 50.0).clip(0, 100),
    ], axis=1).mean(axis=1)
    out["H09_GLOBAL_RISK"] = risk
    out["H10_LIQUIDITY"] = _rank(np.log1p(volume))
    out["H11_REVISIONS_OR_FLOWS"] = np.nan
    out["H13_MACRO_SECTOR"] = _rank(_field(out, "relative_strength"))
    out["H14_CATALYSTS"] = _rank(_field(out, "news_materiality"))
    if asset == "ACTION":
        growth = pd.concat([_rank(_field(out, "revenue_growth_yf")), _rank(_field(out, "earnings_growth_yf"))], axis=1).mean(axis=1)
        pe = _num(_field(out, "per_forward_yf")).where(lambda values: values > 0)
        target = _num(_field(out, "target_mean_yf"))
        out["H05_QUALITY"] = _rank(_field(out, "roe"))
        out["H06_GROWTH_PERSISTENCE"] = growth
        out["H07_VALUE_COST"] = _rank(pe, reverse=True)
        out["H08_BALANCE_TRACKING"] = _rank(_field(out, "debt_to_equity"), reverse=True)
        out["HYPER_POTENTIAL_PCT"] = 100.0 * (target / close - 1.0)
        out["H04_CENTRAL_POTENTIAL"] = _rank(out["HYPER_POTENTIAL_PCT"])
        out["H12_DIVERSIFICATION"] = np.nan
    else:
        diversification = _field(out, "direct_diversification_score").combine_first(_field(out, "diversification_direct_score"))
        tracking = _field(out, "tracking_error_3y_pct").combine_first(_field(out, "tracking_error_1y_pct"))
        concentration = _field(out, "direct_top_holdings_concentration_pct").combine_first(_field(out, "top_holdings_concentration_pct"))
        efficiency = _num(_field(out, "perf_6m_pct")) / _num(_field(out, "volatility_60d")).replace(0, np.nan)
        out["H05_QUALITY"] = _rank(diversification)
        out["H06_GROWTH_PERSISTENCE"] = pd.concat([_rank(_field(out, "perf_3y_pct")), _rank(_field(out, "perf_5y_pct"))], axis=1).mean(axis=1)
        out["H07_VALUE_COST"] = _rank(_field(out, "ter_pct"), reverse=True)
        out["H08_BALANCE_TRACKING"] = _rank(tracking, reverse=True)
        out["HYPER_POTENTIAL_PCT"] = np.nan
        out["H04_CENTRAL_POTENTIAL"] = _rank(efficiency)
        out["H10_LIQUIDITY"] = pd.concat([out["H10_LIQUIDITY"], _rank(_field(out, "aum_m"))], axis=1).mean(axis=1)
        out["H12_DIVERSIFICATION"] = _rank(concentration, reverse=True)
    raw_inputs = ["last_close", "rsi14", "volatility_60d", "volume", "relative_strength", "perf_3m_pct", "perf_6m_pct"]
    out["H15_DATA_QUALITY"] = 100.0 * pd.concat([_field(out, name).notna() for name in raw_inputs], axis=1).mean(axis=1)
    atr_pct = 100.0 * _num(_field(out, "atr14")) / close.replace(0, np.nan)
    rr_proxy = _num(out["HYPER_POTENTIAL_PCT"]) / (1.5 * atr_pct).replace(0, np.nan)
    if asset == "ETF":
        rr_proxy = _num(_field(out, "perf_6m_pct")) / _num(_field(out, "volatility_60d")).replace(0, np.nan)
    out["H16_REWARD_RISK"] = _rank(rr_proxy)
    evidence = _field(out, "evidence_level").astype(str).str.upper().map({"HIGH": 100.0, "MEDIUM": 65.0, "LOW": 35.0})
    out["H17_RELIABILITY"] = pd.concat([_num(_field(out, "data_trust_pct")), _num(_field(out, "coverage_pct")), evidence], axis=1).mean(axis=1)
    out["H18_TEMPORAL_STABILITY"] = np.nan
    downside_beta = _field(out, "risk_downside_beta_252d").combine_first(_field(out, "direct_beta3y" if asset == "ETF" else "beta"))
    out["H19_DOWNSIDE_RESILIENCE"] = pd.concat([
        _rank(_num(_field(out, drawdown_field)).abs(), reverse=True),
        _rank(_field(out, "volatility_60d"), reverse=True),
        _rank(downside_beta, reverse=True),
    ], axis=1).mean(axis=1)
    out["H20_RELATIVE_LIQUIDITY"] = pd.concat([_rank(_field(out, "rvol20")), _rank(_field(out, "spread_est_bps"), reverse=True)], axis=1).mean(axis=1)
    out["asset_class"] = asset
    out["horizon"] = "CT" if asset == "ACTION" else "MT"
    return out


def _external_scores(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = frame.copy()
    bours = out.get("boursorama_consensus", pd.Series("", index=out.index)).astype(str).str.upper()
    out["H02_BOURSORAMA"] = bours.map({"STRONG_BUY": 100.0, "BUY": 80.0, "ACHETER": 100.0, "RENFORCER": 80.0})
    etf = out["asset_class"].eq("ETF")
    pea = out.get("boursorama_etf_pea_eligible_displayed", pd.Series(np.nan, index=out.index))
    out.loc[etf, "H02_BOURSORAMA"] = pea.loc[etf].map({True: 100.0, False: 0.0})
    daily = out.get("tradingview_daily_signal", pd.Series("", index=out.index)).astype(str).str.upper()
    weekly = out.get("tradingview_weekly_signal", pd.Series("", index=out.index)).astype(str).str.upper()
    monthly = out.get("tradingview_monthly_signal", pd.Series("", index=out.index)).astype(str).str.upper()
    signal_score = {"STRONG_BUY": 100.0, "BUY": 75.0, "NEUTRAL": 40.0, "SELL": 0.0, "STRONG_SELL": 0.0}
    out["H01_TRADINGVIEW"] = pd.concat([daily.map(signal_score), weekly.map(signal_score), monthly.map(signal_score)], axis=1).mean(axis=1)
    tv_complete = out.get("tradingview_technical_complete", pd.Series(np.nan, index=out.index)).map({True: 100.0, False: 0.0}).fillna(0.0)
    bours_evidence = out["H02_BOURSORAMA"].notna().astype(float).mul(100.0)
    out["H17_RELIABILITY"] = pd.concat([_num(_field(out, "H17_RELIABILITY")), tv_complete, bours_evidence], axis=1).mean(axis=1)
    revisions = _num(out.get("boursorama_net_upgrades_30d", pd.Series(np.nan, index=out.index)))
    out.loc[~etf, "H11_REVISIONS_OR_FLOWS"] = (50.0 + revisions * 10.0).clip(0, 100).loc[~etf]
    reasons, states = [], []
    for index, row in out.iterrows():
        asset = row["asset_class"]
        missing, negative = [], []
        if pd.isna(row.get("H02_BOURSORAMA")):
            missing.append("BOURSORAMA")
        elif row.get("H02_BOURSORAMA", 0) <= 0:
            negative.append("BOURSORAMA")
        signals = [str(row.get(field, "")).upper() for field in ("tradingview_daily_signal", "tradingview_weekly_signal", "tradingview_monthly_signal")]
        stars = pd.to_numeric(row.get("morningstar_rating"), errors="coerce")
        if asset == "ETF":
            if not signals[0] or signals[0] == "NAN": missing.append("TV_CT")
            elif not _positive(signals[0]): negative.append("TV_CT")
            if not signals[1] or signals[1] == "NAN":
                if not (pd.notna(stars) and stars >= cfg["etfs"]["minimum_morningstar_fallback"]): missing.append("TV_MT")
            elif not _positive(signals[1]): negative.append("TV_MT")
            if not signals[2] or signals[2] == "NAN":
                if not (pd.notna(stars) and stars >= cfg["etfs"]["minimum_morningstar_fallback"]): missing.append("TV_LT")
            elif signals[2] != cfg["etfs"]["monthly_signal_required"]: negative.append("TV_MONTHLY_NOT_STRONG_BUY")
        else:
            for label, signal in zip(("TV_CT", "TV_MT", "TV_LT"), signals):
                if not signal or signal == "NAN": missing.append(label)
                elif not _positive(signal): negative.append(label)
        if negative:
            states.append("REJECTED_CONFIRMATION"); reasons.append("|".join(negative))
        elif missing:
            states.append("PENDING_SOURCE"); reasons.append("|".join(missing))
        else:
            states.append("CONFIRMED"); reasons.append("PASS_BOURSORAMA_TRADINGVIEW")
    out["HYPER_CONFIRMATION_STATE"] = states
    out["HYPER_CONFIRMATION_REASON"] = reasons
    return out


def _weighted(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = frame.copy()
    columns = [f"H{i:02d}_" for i in range(1, len(cfg["weights"]) + 1)]
    actual = []
    for prefix in columns:
        column = next((name for name in out.columns if name.startswith(prefix)), f"{prefix}MISSING")
        if column not in out:
            out[column] = np.nan
        actual.append(column)
    weights = pd.Series(cfg["weights"], index=actual, dtype=float)
    values = out[actual].apply(pd.to_numeric, errors="coerce")
    available = values.notna().mul(weights, axis=1).sum(axis=1)
    core_weights = weights.iloc[2:]
    core_available = values[core_weights.index].notna().mul(core_weights, axis=1).sum(axis=1)
    out["HYPER_COVERAGE_PCT"] = 100.0 * core_available / core_weights.sum()
    out["HYPER_TOTAL_AVAILABLE_WEIGHT"] = available
    out["HYPER_SCORE"] = values.mul(weights, axis=1).sum(axis=1).div(available)
    return out


def _deduplicate_etfs(frame: pd.DataFrame, prices: dict[str, pd.Series], threshold: float = 0.90) -> tuple[pd.DataFrame, set[str]]:
    if frame.empty:
        return frame, set()
    actions = frame[~frame["asset_class"].eq("ETF")]
    etfs = frame[frame["asset_class"].eq("ETF")].sort_values("HYPER_SCORE", ascending=False)
    kept_indexes, kept_tickers, removed = [], [], set()
    for index, row in etfs.iterrows():
        ticker = str(row.get("yahoo_ticker", ""))
        conflict = False
        for incumbent in kept_tickers:
            if ticker not in prices or incumbent not in prices:
                continue
            pair = pd.concat([to_returns(prices[ticker]), to_returns(prices[incumbent])], axis=1, sort=False).dropna().tail(126)
            if len(pair) >= 60 and float(pair.iloc[:, 0].corr(pair.iloc[:, 1])) >= threshold:
                conflict = True
                break
        if conflict:
            removed.add(str(row.get("isin")))
        else:
            kept_indexes.append(index); kept_tickers.append(ticker)
    return pd.concat([actions, etfs.loc[kept_indexes]], ignore_index=True, sort=False), removed


def _challenger_observability(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    out = frame.copy()
    rvol = _num(_field(out, "rvol20"))
    spread = _num(_field(out, "spread_est_bps"))
    out["HYPER_LIQUIDITY_RELATIVE_SCORE"] = pd.concat([_rank(rvol), _rank(spread, reverse=True)], axis=1).mean(axis=1)
    out["HYPER_LIQUIDITY_RELATIVE_STATUS"] = np.where(rvol.notna() & spread.notna(), "COMPLETE", "PARTIAL_SHADOW")
    today = datetime.now(timezone.utc).date().isoformat()
    current = pd.DataFrame({
        "as_of_date": today,
        "isin": out["isin"].astype(str),
        "confirmation_state": out["HYPER_CONFIRMATION_STATE"].astype(str),
    })
    ledger_path = root / STABILITY_LEDGER
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = _read(ledger_path)
    ledger = pd.concat([ledger, current], ignore_index=True, sort=False) if not ledger.empty else current
    ledger = ledger.drop_duplicates(["as_of_date", "isin"], keep="last").sort_values(["isin", "as_of_date"])
    ledger.to_csv(ledger_path, sep=";", index=False, encoding="utf-8-sig")
    counts: dict[str, int] = {}
    for isin, history in ledger.groupby("isin", sort=False):
        states = history["confirmation_state"].astype(str).tolist()
        last = states[-1] if states else ""
        count = 0
        for state in reversed(states):
            if state != last:
                break
            count += 1
        counts[str(isin)] = count
    out["HYPER_STABILITY_OBSERVATIONS"] = out["isin"].astype(str).map(counts).fillna(0).astype(int)
    out["HYPER_STABILITY_STATE"] = np.select(
        [out["HYPER_STABILITY_OBSERVATIONS"].ge(3), out["HYPER_STABILITY_OBSERVATIONS"].ge(2)],
        ["STABLE_3", "CONFIRMING_2"], default="NEW_OBSERVATION",
    )
    out["H18_TEMPORAL_STABILITY"] = out["HYPER_STABILITY_OBSERVATIONS"].map({1: 35.0, 2: 65.0}).fillna(100.0)
    out.loc[out["HYPER_STABILITY_OBSERVATIONS"].le(0), "H18_TEMPORAL_STABILITY"] = np.nan
    out["H20_RELATIVE_LIQUIDITY"] = out["HYPER_LIQUIDITY_RELATIVE_SCORE"]
    return out


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    actions = _core_scores(_read(root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"), "ACTION")
    etfs = _core_scores(_read(root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv"), "ETF")
    core = _weighted(pd.concat([actions, etfs], ignore_index=True, sort=False), cfg)
    action_ok = core["asset_class"].eq("ACTION") & (core["HYPER_POTENTIAL_PCT"] >= cfg["actions"]["minimum_potential_pct"])
    eligible = core[action_ok | core["asset_class"].eq("ETF")].copy()
    pre = pd.concat([
        eligible[eligible["asset_class"].eq("ACTION")].nlargest(cfg["preselection"]["actions"], "HYPER_SCORE"),
        eligible[eligible["asset_class"].eq("ETF")].nlargest(cfg["preselection"]["etfs"], "HYPER_SCORE"),
    ], ignore_index=True, sort=False)
    enriched, source_audit = collect_ci_light_context(pre, root)
    scored = _weighted(_challenger_observability(_weighted(_external_scores(enriched, cfg), cfg), root), cfg)
    score_pass = (scored["HYPER_SCORE"] >= cfg["minimum_score"]) & (scored["HYPER_COVERAGE_PCT"] >= cfg["minimum_coverage_pct"])
    confirmed = scored[score_pass & scored["HYPER_CONFIRMATION_STATE"].eq("CONFIRMED")].copy()
    confirmed = pd.concat([
        confirmed[confirmed["asset_class"].eq("ACTION")].nlargest(cfg["final_maximum"]["actions"], "HYPER_SCORE"),
        confirmed[confirmed["asset_class"].eq("ETF")].nlargest(cfg["final_maximum"]["etfs"], "HYPER_SCORE"),
    ], ignore_index=True, sort=False)
    pending = scored[score_pass & scored["HYPER_CONFIRMATION_STATE"].eq("PENDING_SOURCE")].copy()
    pending = pd.concat([
        pending[pending["asset_class"].eq("ACTION")].nlargest(cfg["final_maximum"]["actions"], "HYPER_SCORE"),
        pending[pending["asset_class"].eq("ETF")].nlargest(cfg["final_maximum"]["etfs"], "HYPER_SCORE"),
    ], ignore_index=True, sort=False)
    etf_prices = load_cached_prices(root / "data/cache/etf")
    confirmed, overlap_removed_confirmed = _deduplicate_etfs(confirmed, etf_prices)
    pending, overlap_removed_pending = _deduplicate_etfs(pending, etf_prices)
    overlap_removed = overlap_removed_confirmed | overlap_removed_pending
    kept_isins = set(confirmed["isin"].astype(str)) | set(pending["isin"].astype(str))
    rejected = scored[~scored["isin"].astype(str).isin(kept_isins)].copy()
    for path in (SELECTED, PENDING, REJECTED, AUDIT): (root / path).parent.mkdir(parents=True, exist_ok=True)
    confirmed.to_csv(root / SELECTED, sep=";", index=False, encoding="utf-8-sig")
    pending.to_csv(root / PENDING, sep=";", index=False, encoding="utf-8-sig")
    rejected.to_csv(root / REJECTED, sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS", "version": cfg["version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "criteria_count": len(cfg["criteria"]), "criteria_weight_sum": sum(cfg["weights"]),
        "criteria": cfg["criteria"], "preselected": len(pre), "confirmed": len(confirmed),
        "pending_source": len(pending), "rejected": len(rejected),
        "average_core_coverage_pct": round(float(scored["HYPER_COVERAGE_PCT"].mean()), 2),
        "new_criteria_available": {
            field: int(scored[field].notna().sum())
            for field in ("H16_REWARD_RISK", "H17_RELIABILITY", "H18_TEMPORAL_STABILITY", "H19_DOWNSIDE_RESILIENCE", "H20_RELATIVE_LIQUIDITY")
        },
        "etf_overlap_removed": len(overlap_removed), "etf_overlap_removed_isins": sorted(overlap_removed),
        "source_context": source_audit, "shadow_only": True, "real_orders_enabled": False,
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__": print(json.dumps(run(), ensure_ascii=False, indent=2))
