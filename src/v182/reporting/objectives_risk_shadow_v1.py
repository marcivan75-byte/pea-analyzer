from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd

from v182.risk.beta_metrics import load_cached_prices


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/OBJECTIVES_RISK_SHADOW_V1.json")
CSV = Path("outputs/committee_master/OBJECTIVES_RISK_SHADOW_V1.csv")
JSON = Path("outputs/audit/OBJECTIVES_RISK_SHADOW_V1.json")
MD = Path("outputs/mobile/ANDROID_OBJECTIVES_RISK_SHADOW_V1.md")
HYPER_TABLE = Path("outputs/committee_master/HYPER_SELECTION_OBJECTIVES_RISK_V1.csv")


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _num(value: object) -> float | None:
    try:
        number = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def _candidates(root: Path) -> pd.DataFrame:
    sources = (
        ("HYPER_SELECTION", "outputs/committee_master/HYPER_SELECTION_V1.csv"),
        ("HYPER_PENDING_CONFIRMATION", "outputs/committee_master/HYPER_SELECTION_PENDING_V1.csv"),
        ("CI", "outputs/committee_master/CI_SELECTION_V4.csv"),
        ("CI_POST_GATE_UNIVERSE", "outputs/committee_master/CI_ENTRY_CONFIDENCE_V22_2.csv"),
        ("CI_LIGHT", "outputs/committee_master/CI_LIGHT_V4.csv"),
        ("CI_BALANCED", "outputs/committee_master/CI_BALANCED_V4.csv"),
    )
    rows: list[pd.DataFrame] = []
    for label, relative in sources:
        frame = _read(root / relative)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["SIM_SELECTION_SOURCE"] = label
        rows.append(frame)
    if not rows:
        return pd.DataFrame()
    combined = pd.concat(rows, ignore_index=True, sort=False)
    hyper_potential = combined.groupby("isin")["HYPER_POTENTIAL_PCT"].first() if "HYPER_POTENTIAL_PCT" in combined else pd.Series(dtype=float)
    hyper_confirmation = combined.groupby("isin")["HYPER_CONFIRMATION_STATE"].first() if "HYPER_CONFIRMATION_STATE" in combined else pd.Series(dtype=object)
    provenance = combined.groupby("isin")["SIM_SELECTION_SOURCE"].agg(lambda values: "|".join(sorted(set(values))))
    combined = combined.sort_values("SIM_SELECTION_SOURCE").drop_duplicates("isin", keep="first")
    combined["SIM_SELECTION_SOURCE"] = combined["isin"].map(provenance)
    combined["HYPER_POTENTIAL_PCT"] = combined["isin"].map(hyper_potential)
    combined["HYPER_CONFIRMATION_STATE"] = combined["isin"].map(hyper_confirmation)
    return combined


def _attach_master(rows: pd.DataFrame, root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    fields = (
        "isin", "yahoo_ticker", "last_close", "atr14", "mm20", "mm50", "mm200", "high_52w",
        "morningstar_rating", "volatility_20d", "volatility_60d",
    )
    for asset, relative in (
        ("ACTION", "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"),
        ("ETF", "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv"),
    ):
        master = _read(root / relative)
        if master.empty:
            continue
        keep = [field for field in fields if field in master]
        part = master[keep].drop_duplicates("isin", keep="last").copy()
        part["asset_class_master"] = asset
        frames.append(part)
    reference = pd.concat(frames, ignore_index=True, sort=False)
    result = rows.merge(reference, on="isin", how="left", suffixes=("", "_master"))
    if "asset_class" not in result:
        result["asset_class"] = result["asset_class_master"]
    for field in fields:
        master_field = f"{field}_master"
        if master_field not in result:
            continue
        if field not in result:
            result[field] = result[master_field]
        else:
            missing = result[field].isna() | result[field].astype(str).str.strip().isin({"", "nan", "None"})
            result.loc[missing, field] = result.loc[missing, master_field]
        result = result.drop(columns=master_field)
    return result


def _rolling_scenarios(prices: pd.Series, sessions: int) -> tuple[float, float, float, int]:
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    returns = clean.pct_change(sessions).dropna()
    if returns.empty:
        return 0.0, 0.0, 0.0, len(clean)
    recent = returns.tail(504)
    return (
        float(recent.quantile(0.25)),
        float(recent.quantile(0.50)),
        float(recent.quantile(0.75)),
        len(clean),
    )


def _simulate(row: pd.Series, prices: dict[str, pd.Series], cfg: dict) -> dict[str, object]:
    asset = _text(row.get("asset_class")).upper()
    horizon = _text(row.get("horizon")).upper() or "MT"
    if horizon not in cfg["forecast_sessions"]:
        horizon = "MT"
    ticker = _text(row.get("yahoo_ticker"))
    history = prices.get(ticker, pd.Series(dtype=float))
    current = _num(row.get("last_close"))
    if current is None and not history.empty:
        current = _num(history.dropna().iloc[-1])
    atr = _num(row.get("atr14"))
    if current is None or current <= 0 or atr is None or atr <= 0:
        return {"SIM_STATUS": "INSUFFICIENT_PRICE_OR_ATR", "SIM_RELIABILITY": 0.0}

    sessions = int(cfg["forecast_sessions"][horizon])
    q25, q50, q75, observations = _rolling_scenarios(history, sessions)
    upside = _num(row.get("POTENTIEL_PCT"))
    if upside is None:
        upside = _num(row.get("CI_POTENTIAL_UPSIDE_PCT"))
    if upside is None:
        upside = _num(row.get("CI_LIGHT_BOURSORAMA_UPSIDE_PCT"))
    if upside is None:
        upside = _num(row.get("HYPER_POTENTIAL_PCT"))
    if asset == "ACTION" and upside is not None:
        central_return = max(-0.10, min(0.60, upside / 100.0))
        target_method = "ACTION_CONSENSUS_POTENTIAL"
        prudent_return = min(central_return, max(q25, central_return * 0.55))
        optimistic_return = max(central_return, min(0.80, max(q75, central_return * 1.35)))
        target_source_quality = 1.0
    else:
        central_return = max(-0.10, min(0.35, q50))
        prudent_return = max(-0.15, min(central_return, q25))
        optimistic_return = min(0.50, max(central_return, q75))
        target_method = "ETF_EMPIRICAL_RETURN_DISTRIBUTION"
        target_source_quality = 0.8 if observations >= cfg["history_minimum_observations"] else 0.4

    target_central = current * (1.0 + central_return)
    target_prudent = current * (1.0 + prudent_return)
    target_optimistic = current * (1.0 + optimistic_return)
    supports = [value for value in (_num(row.get("mm20")), _num(row.get("mm50"))) if value and value < current]
    structural_support = max(supports) if supports else current - atr
    invalidation = min(structural_support - 0.25 * atr, current - float(cfg["atr_invalidation_multiple"][asset]) * atr)
    invalidation = max(0.01, invalidation)
    minimum_rr = float(cfg["minimum_reward_risk"][horizon])
    maximum_admissible_entry = (target_central + minimum_rr * invalidation) / (1.0 + minimum_rr)
    preferred_pullback = max(invalidation + atr, structural_support + float(cfg["entry_support_atr_buffer"]) * atr)
    optimal_entry = min(current, maximum_admissible_entry, preferred_pullback)
    if optimal_entry <= invalidation:
        return {"SIM_STATUS": "NO_FEASIBLE_ENTRY", "SIM_RELIABILITY": 0.0}
    risk_pct = 100.0 * (optimal_entry - invalidation) / optimal_entry
    reward_pct = 100.0 * (target_central - optimal_entry) / optimal_entry
    rr = reward_pct / risk_pct if risk_pct > 0 else None
    current_risk = current - invalidation
    current_rr = (target_central - current) / current_risk if current_risk > 0 else None
    technical_fields = ("atr14", "mm20", "mm50", "mm200", "high_52w")
    technical_coverage = sum(_num(row.get(field)) is not None for field in technical_fields) / len(technical_fields)
    history_quality = min(1.0, observations / max(1, int(cfg["history_minimum_observations"])))
    selection_quality = min(1.0, (_num(row.get("BALANCED_SCORE")) or _num(row.get("score")) or 70.0) / 100.0)
    weights = cfg["reliability"]
    reliability = (
        weights["history_weight"] * history_quality
        + weights["target_source_weight"] * target_source_quality
        + weights["technical_coverage_weight"] * technical_coverage
        + weights["selection_evidence_weight"] * selection_quality
    )
    confirmation_state = _text(row.get("HYPER_CONFIRMATION_STATE")) or "NOT_APPLICABLE_EXISTING_SELECTION"
    selection_sources = set(_text(row.get("SIM_SELECTION_SOURCE")).split("|"))
    independent_confirmation = bool(selection_sources.intersection({"CI", "CI_LIGHT", "CI_BALANCED"}))
    if confirmation_state == "PENDING_SOURCE" and not independent_confirmation:
        reliability *= 0.65
    status = "ENTRY_NOW_ACCEPTABLE" if current <= maximum_admissible_entry else "WAIT_FOR_ENTRY_ZONE"
    return {
        "SIM_STATUS": status,
        "SIM_CURRENT_PRICE": round(current, 4),
        "SIM_ENTRY_OPTIMAL": round(optimal_entry, 4),
        "SIM_ENTRY_MAX_RR": round(maximum_admissible_entry, 4),
        "SIM_TARGET_PRUDENT": round(target_prudent, 4),
        "SIM_TARGET_CENTRAL": round(target_central, 4),
        "SIM_TARGET_OPTIMISTIC": round(target_optimistic, 4),
        "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT": round(central_return * 100.0, 2),
        "SIM_INVALIDATION": round(invalidation, 4),
        "SIM_RISK_PCT_AT_OPTIMAL_ENTRY": round(risk_pct, 2),
        "SIM_REWARD_PCT_AT_OPTIMAL_ENTRY": round(reward_pct, 2),
        "SIM_REWARD_RISK_AT_CURRENT": round(current_rr, 2) if current_rr is not None else None,
        "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY": round(rr, 2) if rr is not None else None,
        "SIM_RELIABILITY": round(min(100.0, reliability), 1),
        "SIM_EXTERNAL_CONFIRMATION": confirmation_state,
        "SIM_TARGET_METHOD": target_method,
        "SIM_HISTORY_OBSERVATIONS": observations,
        "SIM_HORIZON": horizon,
        "SIM_SHADOW_ONLY": True,
        "SIM_REAL_ORDER_ALLOWED": False,
    }


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    candidates = _attach_master(_candidates(root), root)
    action_prices = load_cached_prices(root / "data/cache/actions")
    etf_prices = load_cached_prices(root / "data/cache/etf")
    prices = {**action_prices, **etf_prices}
    records = []
    for _, row in candidates.iterrows():
        identity = {field: row.get(field) for field in (
            "name", "isin", "asset_class", "horizon", "yahoo_ticker", "SIM_SELECTION_SOURCE",
            "HYPER_CONFIRMATION_STATE", "boursorama_consensus", "boursorama_n_analysts",
            "boursorama_target_upside_pct", "boursorama_etf_pea_eligible_displayed",
            "tradingview_daily_signal", "tradingview_weekly_signal", "tradingview_monthly_signal",
            "morningstar_rating", "rsi14", "RSI_STATE", "score", "BALANCED_SCORE", "HYPER_SCORE",
            "CI_CONFIDENCE_SCORE_0_100", "CI_CONFIDENCE_SCORE_V22_2_1",
            "CI_MARKET_ORIENTATION_EUROPE", "orientation_europe", "orientation_global",
            "v22_2_entry_state", "V22_2_1_ENTRY_STATE", "CI_SELECTION_GATE_STATUS_V4",
            "risk_downside_beta_252d", "risk_beta_252d", "risk_correlation_252d",
            "max_drawdown_1y", "max_drawdown_1y_pct", "volatility_60d", "sector",
            "official_benchmark", "category", "geo_exposure", "boursorama_sector",
        )}
        records.append({**identity, **_simulate(row, prices, cfg)})
    result = pd.DataFrame(records)
    generated = datetime.now(timezone.utc).isoformat()
    for path in (CSV, JSON, MD, HYPER_TABLE):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(root / CSV, sep=";", index=False, encoding="utf-8-sig")
    hyper = result[result["SIM_SELECTION_SOURCE"].astype(str).str.contains("HYPER")].copy()
    action_gate = cfg["action_output_gate"]
    action = hyper["asset_class"].astype(str).str.upper().eq("ACTION")
    action_pass = (
        pd.to_numeric(hyper["SIM_REWARD_RISK_AT_OPTIMAL_ENTRY"], errors="coerce")
        > float(action_gate["minimum_reward_risk_exclusive"])
    ) & (
        pd.to_numeric(hyper["SIM_RELIABILITY"], errors="coerce")
        > float(action_gate["minimum_reliability_exclusive"])
    )
    hyper = hyper[~action | action_pass].copy()
    hyper["SIM_COURSE_TO_ENTRY_PCT"] = (
        100.0 * (pd.to_numeric(hyper["SIM_CURRENT_PRICE"], errors="coerce") - pd.to_numeric(hyper["SIM_ENTRY_OPTIMAL"], errors="coerce"))
        / pd.to_numeric(hyper["SIM_CURRENT_PRICE"], errors="coerce")
    ).round(2)
    hyper["SIM_ENTRY_TO_INVALIDATION_PCT"] = (
        100.0 * (pd.to_numeric(hyper["SIM_ENTRY_OPTIMAL"], errors="coerce") - pd.to_numeric(hyper["SIM_INVALIDATION"], errors="coerce"))
        / pd.to_numeric(hyper["SIM_ENTRY_OPTIMAL"], errors="coerce")
    ).round(2)
    hyper_table = hyper.rename(columns={
        "name": "Instrument",
        "SIM_CURRENT_PRICE": "Cours du jour",
        "SIM_ENTRY_OPTIMAL": "Entrée optimale",
        "SIM_TARGET_CENTRAL": "Objectif central",
        "SIM_INVALIDATION": "Invalidation",
        "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT": "Potentiel",
        "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY": "R/R optimal",
        "SIM_RELIABILITY": "Fiabilité*",
        "SIM_COURSE_TO_ENTRY_PCT": "% cours → entrée optimale",
        "SIM_ENTRY_TO_INVALIDATION_PCT": "% entrée → invalidation",
        "boursorama_consensus": "Boursorama recommandation",
        "boursorama_n_analysts": "Boursorama analystes",
        "boursorama_target_upside_pct": "Boursorama potentiel %",
        "boursorama_etf_pea_eligible_displayed": "Boursorama PEA ETF",
        "tradingview_daily_signal": "TradingView CT",
        "tradingview_weekly_signal": "TradingView MT",
        "tradingview_monthly_signal": "TradingView LT",
        "morningstar_rating": "Morningstar étoiles",
        "rsi14": "RSI",
        "RSI_STATE": "État RSI",
    })
    selective_columns = [
        "Boursorama recommandation", "Boursorama analystes", "Boursorama potentiel %",
        "Boursorama PEA ETF", "TradingView CT", "TradingView MT", "TradingView LT",
        "Morningstar étoiles",
        "RSI", "État RSI",
    ]
    hyper_table[selective_columns] = hyper_table[selective_columns].fillna("INDISPONIBLE").replace("", "INDISPONIBLE")
    def boursorama_summary(row: pd.Series) -> str:
        if str(row.get("Boursorama recommandation")) != "INDISPONIBLE":
            return f"{row.get('Boursorama recommandation')} | analystes={row.get('Boursorama analystes')} | potentiel={row.get('Boursorama potentiel %')}%"
        if str(row.get("Boursorama PEA ETF")) != "INDISPONIBLE":
            return f"ETF PEA={row.get('Boursorama PEA ETF')}"
        return "INDISPONIBLE"

    def tradingview_summary(row: pd.Series) -> str:
        signals = [row.get("TradingView CT"), row.get("TradingView MT"), row.get("TradingView LT")]
        if all(str(value) == "INDISPONIBLE" for value in signals):
            return "INDISPONIBLE"
        return f"CT={signals[0]} | MT={signals[1]} | LT={signals[2]}"

    hyper_table["Synthèse Boursorama"] = hyper_table.apply(boursorama_summary, axis=1)
    hyper_table["Synthèse TradingView"] = hyper_table.apply(tradingview_summary, axis=1)
    hyper_table[["Cours du jour", "Instrument", "Entrée optimale", "% cours → entrée optimale", "Objectif central", "Invalidation", "% entrée → invalidation", "Potentiel", "R/R optimal", "Fiabilité*", *selective_columns, "Synthèse Boursorama", "Synthèse TradingView"]].to_csv(
        root / HYPER_TABLE, sep=";", index=False, encoding="utf-8-sig"
    )
    payload = {
        "status": "SUCCESS", "version": cfg["version"], "generated_at_utc": generated,
        "shadow_only": True, "real_orders_enabled": False, "instrument_count": len(result),
        "simulated_count": int(result.get("SIM_CURRENT_PRICE", pd.Series(dtype=float)).notna().sum()),
        "rows": result.where(pd.notna(result), None).to_dict("records"),
    }
    (root / JSON).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Objectifs et risques — simulation shadow", "", f"Generated: {generated}", ""]
    for record in payload["rows"]:
        lines.append(
            f"- {record.get('name')} | entrée {record.get('SIM_ENTRY_OPTIMAL')} | objectif central {record.get('SIM_TARGET_CENTRAL')} | "
            f"invalidation {record.get('SIM_INVALIDATION')} | R/R {record.get('SIM_REWARD_RISK_AT_OPTIMAL_ENTRY')} | fiabilité {record.get('SIM_RELIABILITY')}% | {record.get('SIM_STATUS')}"
        )
    (root / MD).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
