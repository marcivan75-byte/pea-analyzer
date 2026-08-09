from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd

from v182.decision.etf102_committee_v2043 import build

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "data/reference/V20.4.3_ETF102_CONFIG.json"
IN = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
OUT = ROOT / "outputs/V20.4.3_ETF102_COMMITTEE.csv"
AUDIT = ROOT / "outputs/audit/V20.4.3_ETF102_COMMITTEE_AUDIT.json"
SUMMARY = ROOT / "outputs/V20.4.3_ETF102_COMMITTEE_SUMMARY.md"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _disable_sparse_components(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict[str, dict]]:
    out = df.copy()
    threshold = float(cfg["coverage"].get("component_activation_min_universe_coverage", 0.30))
    status: dict[str, dict] = {}
    for component, rules in cfg["component_rules"].items():
        fields = list(rules)
        masks = [pd.to_numeric(out.get(f), errors="coerce").notna() if f in out.columns else pd.Series(False, index=out.index) for f in fields]
        any_observed = pd.concat(masks, axis=1).any(axis=1) if masks else pd.Series(False, index=out.index)
        universe_coverage = float(any_observed.mean()) if len(out) else 0.0
        active = universe_coverage >= threshold
        status[component] = {
            "active": bool(active),
            "universe_coverage": round(universe_coverage, 4),
            "activation_threshold": threshold,
            "fields": fields,
        }
        if not active:
            for f in fields:
                if f not in out.columns:
                    out[f] = np.nan
                else:
                    out[f] = np.nan
    return out, status


def _apply_funnel_gate(out: pd.DataFrame) -> pd.DataFrame:
    if "funnel_risk_gate" not in out.columns:
        return out
    gated = out.copy()
    gates = gated["funnel_risk_gate"].fillna("PASS").astype(str).str.upper()
    for hz in ("ct", "mt", "lt"):
        dec_col = f"decision_{hz}"
        reason_col = f"decision_reason_{hz}"
        if dec_col not in gated.columns:
            continue
        buy_or_watch = gated[dec_col].isin(["BUY_CANDIDATE", "WATCH"])
        severe = gates.eq("BLOCK_BUY") & buy_or_watch
        review = gates.eq("REVIEW_ONLY") & buy_or_watch
        gated.loc[severe, dec_col] = "REVIEW"
        gated.loc[severe, reason_col] = "FUNNEL_CONTEXT_BLOCK_BUY"
        gated.loc[review, dec_col] = "REVIEW"
        gated.loc[review, reason_col] = "FUNNEL_CONTEXT_REVIEW_ONLY"
    return gated


def _audit(out: pd.DataFrame, cfg: dict, component_status: dict[str, dict]) -> dict:
    coverage = {}
    for field in [
        "ter_pct", "fund_total_assets_eur_m", "spread_pct", "morningstar_rating",
        "diversification_direct_score", "tracking_error_1y_pct", "tracking_error_3y_pct",
        "tracking_error_5y_pct", "weight_coverage_ct", "weight_coverage_mt", "weight_coverage_lt"
    ]:
        coverage[field] = int(_num(out, field).notna().sum())
    return {
        "passed": True,
        "version": cfg["version"],
        "rows": len(out),
        "unique_isin": int(out["isin"].astype(str).nunique()),
        "legacy_266_used": False,
        "missing_data_policy": cfg["missing_data_policy"],
        "sparse_component_policy": "DISABLE_COMPONENT_FOR_ENTIRE_UNIVERSE_BELOW_ACTIVATION_THRESHOLD",
        "component_status": component_status,
        "coverage_count_of_102": coverage,
        "mean_weight_coverage": {hz: round(float(_num(out, f"weight_coverage_{hz}").mean()), 4) for hz in ["ct", "mt", "lt", "short"]},
        "decisions": {hz: out[f"decision_{hz}"].value_counts().to_dict() for hz in ["ct", "mt", "lt", "short"]},
        "selection_counts": {hz: int(out[f"selection_{hz}"].sum()) for hz in ["ct", "mt", "lt", "short"]},
        "funnel_gate_counts": out.get("funnel_risk_gate", pd.Series("NOT_AVAILABLE", index=out.index)).value_counts().to_dict(),
        "funnel_context_mean_coverage": round(float(_num(out, "funnel_context_coverage").mean()), 4) if "funnel_context_coverage" in out else None,
        "smart_money_rows_present": int(_num(out, "ifs_effective").notna().sum()),
        "smart_money_positive_score_boost_allowed": False,
        "execution": "RESEARCH_ONLY",
    }


def main() -> None:
    if not IN.exists():
        raise RuntimeError(f"Missing ETF102 enriched input: {IN}")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    source = pd.read_csv(IN, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(source) != 102 or source["isin"].astype(str).nunique() != 102:
        raise RuntimeError("ETF102 hardened runner canonical universe gate failed")
    prepared, component_status = _disable_sparse_components(source, cfg)
    out = build(prepared, cfg)
    out = _apply_funnel_gate(out)

    # Selection must be recalculated after funnel and Smart Money decision gates.
    limits = cfg["selection_limits"]
    for hz in ("ct", "mt", "lt"):
        out[f"selection_{hz}"] = (out[f"rank_{hz}"] <= int(limits[hz.upper()])) & out[f"decision_{hz}"].isin(["BUY_CANDIDATE", "WATCH"])
    out["selection_short"] = (out["rank_short"] <= int(limits["SHORT"])) & out["decision_short"].isin(["SHORT_CANDIDATE", "WATCH_SHORT"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep=";", index=False, encoding="utf-8-sig")
    audit = _audit(out, cfg, component_status)
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    inactive = [k for k, v in component_status.items() if not v["active"]]
    lines = [
        "# V20.4.3 ETF102 Committee — hardened",
        "",
        "Universe: **102 validated ETF only**  ",
        "Legacy 266: **OFF / forbidden**  ",
        "Missing data: **no neutral 50; row weights renormalized; globally sparse components disabled uniformly**  ",
        f"Globally inactive components this run: **{inactive or 'none'}**  ",
        "Funnel: **macro -> country -> news -> sentiment -> structure/fundamentals -> technical -> Smart Money -> risk gate**  ",
        "Smart Money: **negative high-confidence risk gate only; positive score boost disabled pending empirical validation**  ",
        "Execution: **RESEARCH_ONLY**",
        "",
    ]
    for hz in ["ct", "mt", "lt", "short"]:
        lines += [f"## {hz.upper()}", str(out[f"decision_{hz}"].value_counts().to_dict()), f"Top selection count: {int(out[f'selection_{hz}'].sum())}", ""]
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print("V20.4.3_ETF102_HARDENED_OK", json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
