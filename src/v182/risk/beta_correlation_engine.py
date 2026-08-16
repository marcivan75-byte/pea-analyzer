from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.risk.beta_metrics import (
    build_common_benchmark,
    clean_text,
    compute_beta_metrics,
    economic_engine_tags,
    load_cached_prices,
    num,
    to_returns,
)
from v182.risk.beta_portfolio import economic_overlap_scores, portfolio_summary, sector_overlay

ROOT = Path(__file__).resolve().parents[3]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _metadata_map(paths: list[Path]) -> dict[str, dict]:
    path = next((candidate for candidate in paths if candidate.exists()), None)
    if path is None:
        return {}
    frame = _read_csv(path)
    if "isin" not in frame.columns:
        return {}
    frame = frame.drop_duplicates("isin")
    return {str(row["isin"]): row.to_dict() for _, row in frame.iterrows()}


def _metadata_maps(root: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    actions = _metadata_map([
        root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",
        root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv",
    ])
    etfs = _metadata_map([
        root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv",
        root / "inputs" / "V18.2_PEA_ETF_MASTER.csv",
    ])
    return actions, etfs


def _context(row: pd.Series, action_meta: dict[str, dict], etf_meta: dict[str, dict]) -> dict:
    isin = str(row.get("isin") or "")
    asset_class = str(row.get("asset_class") or "").upper()
    if asset_class == "ACTION":
        return action_meta.get(isin, {})
    if asset_class == "ETF":
        return etf_meta.get(isin, {})
    return {}


def _return_map(
    rows: pd.DataFrame,
    action_meta: dict[str, dict],
    etf_meta: dict[str, dict],
    action_prices: dict[str, pd.Series],
    etf_prices: dict[str, pd.Series],
) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for _, row in rows.iterrows():
        context = _context(row, action_meta, etf_meta)
        ticker = clean_text(context.get("yahoo_ticker") or row.get("yahoo_ticker"))
        asset_class = str(row.get("asset_class") or "").upper()
        prices = action_prices.get(ticker) if asset_class == "ACTION" else etf_prices.get(ticker) if asset_class == "ETF" else None
        if prices is not None:
            returns = to_returns(prices)
            if not returns.empty:
                out[str(row.get("isin"))] = returns
    return out


def _risk_score(row: pd.Series) -> float | None:
    beta = num(row.get("risk_beta_252d"))
    if beta is None:
        return None
    downside = num(row.get("risk_downside_beta_252d"))
    ratio = num(row.get("risk_downside_upside_beta_ratio"))
    stress = num(row.get("risk_stress_correlation_252d"))
    stability = num(row.get("risk_beta_stability_span"))
    concentration = num(row.get("risk_etf_top_holdings_concentration_pct"))
    hhi = num(row.get("risk_etf_sector_hhi"))
    score = 25 if beta > 1.60 else 18 if beta > 1.30 else 8 if beta > 1.10 else 0
    if downside is not None:
        score += 30 if downside > 1.60 else 20 if downside > 1.30 else 8 if downside > 1.10 else 0
    if ratio is not None:
        score += 15 if ratio > 1.50 else 8 if ratio > 1.20 else 0
    if stress is not None:
        score += 15 if stress > 0.85 else 8 if stress > 0.70 else 0
    if stability is not None:
        score += 10 if stability > 0.50 else 5 if stability > 0.30 else 0
    if concentration is not None:
        score += 8 if concentration > 50 else 4 if concentration > 35 else 0
    if hhi is not None:
        score += 8 if hhi > 0.35 else 4 if hhi > 0.25 else 0
    return round(min(100.0, float(score)), 4)


def _verdict(score: float | None) -> str:
    if score is None:
        return "MISSING"
    if score < 20:
        return "GREEN"
    if score < 35:
        return "GREEN_AMBER"
    if score < 50:
        return "AMBER"
    if score < 70:
        return "ORANGE"
    return "RED"


def apply_risk_overlay(
    decisions: pd.DataFrame,
    action_meta: dict[str, dict],
    etf_meta: dict[str, dict],
    action_prices: dict[str, pd.Series],
    etf_prices: dict[str, pd.Series],
    benchmark: pd.Series,
) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    out = decisions.copy()
    score_guard = out["score"].copy() if "score" in out.columns else None
    decision_guard = out["decision"].copy() if "decision" in out.columns else None
    returns_by_isin = _return_map(out, action_meta, etf_meta, action_prices, etf_prices)
    contexts: list[dict] = []
    metrics_rows: list[dict] = []
    for _, row in out.iterrows():
        context = _context(row, action_meta, etf_meta)
        contexts.append(context)
        returns = returns_by_isin.get(str(row.get("isin") or ""))
        if returns is None or str(row.get("asset_class") or "").upper() not in {"ACTION", "ETF"}:
            metrics_rows.append({"status": "NOT_APPLICABLE_OR_MISSING_HISTORY"})
        else:
            metrics_rows.append(compute_beta_metrics(returns, benchmark))
    metrics = pd.DataFrame(metrics_rows, index=out.index)
    mapping = {
        "beta_63d": "risk_beta_63d",
        "beta_126d": "risk_beta_126d",
        "beta_252d": "risk_beta_252d",
        "upside_beta_252d": "risk_upside_beta_252d",
        "downside_beta_252d": "risk_downside_beta_252d",
        "downside_upside_beta_ratio": "risk_downside_upside_beta_ratio",
        "correlation_252d": "risk_correlation_252d",
        "stress_correlation_252d": "risk_stress_correlation_252d",
        "r2_252d": "risk_r2_252d",
        "beta_stability_span": "risk_beta_stability_span",
        "beta_class": "risk_beta_class",
        "beta_reliability": "risk_beta_reliability",
        "sessions_252d": "risk_sessions_252d",
        "status": "risk_metric_status",
    }
    for source, target in mapping.items():
        out[target] = metrics[source] if source in metrics.columns else None

    sectors: list[str] = []
    tags: list[str] = []
    concentrations: list[float | None] = []
    sector_hhis: list[float | None] = []
    for row_dict, context in zip(out.to_dict("records"), contexts):
        sector = clean_text(context.get("sector_yf") or context.get("sector") or context.get("sector_bucket") or row_dict.get("sector"))
        industry = clean_text(context.get("industry_yf") or context.get("industry"))
        category = clean_text(context.get("category") or context.get("morningstar_category") or context.get("boursorama_category"))
        name = clean_text(row_dict.get("name") or context.get("name"))
        sectors.append(sector)
        tags.append("|".join(economic_engine_tags(sector, industry, category, name)))
        concentrations.append(num(context.get("direct_top_holdings_concentration_pct")))
        sector_hhis.append(num(context.get("direct_sector_hhi")))
    out["risk_sector"] = sectors
    out["risk_engine_tags"] = tags
    out["risk_etf_top_holdings_concentration_pct"] = concentrations
    out["risk_etf_sector_hhi"] = sector_hhis
    out["risk_exact_holdings_overlap_status"] = "NOT_COMPUTED_SOURCE_NOT_PERSISTED"
    out["risk_overlap_method"] = "RETURN_CORR_70_ENGINE_TAG_30_SHADOW_HEURISTIC"
    out["risk_economic_overlap_score"] = economic_overlap_scores(out, returns_by_isin)

    scores = [_risk_score(row) for _, row in out.iterrows()]
    out["risk_score_0_100_shadow"] = scores
    out["risk_verdict"] = [_verdict(score) for score in scores]
    multipliers: list[float | None] = []
    for _, row in out.iterrows():
        beta = num(row.get("risk_downside_beta_252d")) or num(row.get("risk_beta_252d"))
        if beta is None:
            multipliers.append(None)
            continue
        overlap = num(row.get("risk_economic_overlap_score")) or 0.0
        beta_factor = 1.0 / max(1.0, beta)
        overlap_factor = 1.0 - 0.25 * min(1.0, overlap / 100.0)
        multipliers.append(round(max(0.50, min(1.00, beta_factor * overlap_factor)), 4))
    out["risk_position_multiplier_shadow"] = multipliers
    out["risk_score_decision_influence"] = 0.0
    out["risk_sizing_execution_influence"] = 0.0
    out["risk_stop_loss_influence"] = 0.0
    if score_guard is not None and not score_guard.reset_index(drop=True).equals(out["score"].reset_index(drop=True)):
        raise RuntimeError("RISK_OVERLAY_SCORE_MUTATION_FORBIDDEN")
    if decision_guard is not None and not decision_guard.reset_index(drop=True).equals(out["decision"].reset_index(drop=True)):
        raise RuntimeError("RISK_OVERLAY_DECISION_MUTATION_FORBIDDEN")
    return out, returns_by_isin


def run(root: Path = ROOT) -> dict:
    config_path = root / "config" / "BETA_CORRELATION_RISK_ENGINE.json"
    decisions_path = root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv"
    if not config_path.exists():
        return {"status": "BLOCKED_CONFIG_MISSING", "decision_influence": 0.0}
    if not decisions_path.exists():
        return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING", "decision_influence": 0.0}
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    decisions = _read_csv(decisions_path)
    action_meta, etf_meta = _metadata_maps(root)
    action_prices = load_cached_prices(root / "data" / "cache" / "actions")
    etf_prices = load_cached_prices(root / "data" / "cache" / "etf")
    benchmark_cfg = cfg.get("benchmark", {})
    benchmark, benchmark_diag = build_common_benchmark(
        action_prices,
        min_sessions=int(benchmark_cfg.get("min_sessions", 126)),
        min_constituents=int(benchmark_cfg.get("min_constituents", 20)),
    )
    outdir = root / "outputs" / "risk"
    audit_dir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    if benchmark is None:
        payload = {
            "status": "DEGRADED_BENCHMARK_UNAVAILABLE",
            "version": cfg.get("version"),
            "generated_at_utc": _now(),
            "benchmark": benchmark_diag,
            "decision_influence": 0.0,
            "sizing_execution_influence": 0.0,
            "stop_loss_influence": 0.0,
            "real_orders_enabled": False,
        }
        (audit_dir / "BETA_CORRELATION_RISK_ENGINE.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payload
    enriched, returns_by_isin = apply_risk_overlay(decisions, action_meta, etf_meta, action_prices, etf_prices, benchmark)
    enriched.to_csv(decisions_path, sep=";", index=False, encoding="utf-8-sig")
    risk_columns = [
        field
        for field in enriched.columns
        if field in {"asset_class", "horizon", "isin", "name", "decision", "score"} or field.startswith("risk_")
    ]
    enriched[risk_columns].to_csv(outdir / "BETA_CORRELATION_RISK_ROWS.csv", sep=";", index=False, encoding="utf-8-sig")
    scenarios = [float(value) for value in cfg.get("stress", {}).get("scenarios_pct", [-5, -10, -20, -30])]
    portfolio = portfolio_summary(enriched, returns_by_isin, benchmark, scenarios)
    (outdir / "PORTFOLIO_RISK_SUMMARY.json").write_text(json.dumps(portfolio, ensure_ascii=False, indent=2), encoding="utf-8")
    sectors = sector_overlay(enriched, root)
    if not sectors.empty:
        sectors.to_csv(outdir / "SECTOR_BETA_RISK_OVERLAY.csv", sep=";", index=False, encoding="utf-8-sig")
    valid = pd.to_numeric(enriched.get("risk_beta_252d"), errors="coerce").notna()
    payload = {
        "status": "SUCCESS",
        "version": cfg.get("version"),
        "generated_at_utc": _now(),
        "benchmark": benchmark_diag,
        "decision_rows": int(len(enriched)),
        "rows_with_beta_252d": int(valid.sum()),
        "coverage_pct": round(float(valid.mean() * 100.0), 4) if len(valid) else 0.0,
        "portfolio": portfolio,
        "sector_rows": int(len(sectors)),
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_execution_influence": 0.0,
        "stop_loss_influence": 0.0,
        "shadow_position_multiplier_produced": True,
        "exact_holdings_overlap": "NOT_COMPUTED_SOURCE_NOT_PERSISTED",
        "overlap_proxy": "MAX_PAIRWISE_126D_RETURN_CORRELATION_70_PLUS_ENGINE_TAG_JACCARD_30",
        "stress_semantic": "SYSTEMATIC_SENSITIVITY_ESTIMATE_NOT_TOTAL_LOSS_FORECAST",
        "promotion_gate": "PIT_OOS_MARGINAL_UPLIFT_REQUIRED_BEFORE_ANY_DECISION_OR_SIZING_INFLUENCE",
        "real_orders_enabled": False,
        "outputs": {
            "rows": "outputs/risk/BETA_CORRELATION_RISK_ROWS.csv",
            "portfolio": "outputs/risk/PORTFOLIO_RISK_SUMMARY.json",
            "sectors": "outputs/risk/SECTOR_BETA_RISK_OVERLAY.csv",
        },
    }
    (audit_dir / "BETA_CORRELATION_RISK_ENGINE.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
