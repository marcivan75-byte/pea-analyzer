from __future__ import annotations

from pathlib import Path
import json
import math
from typing import Mapping

import pandas as pd

GOLD_ASSET_CLASSES = {"GOLD_ETC", "GOLD_ETF", "GOLD_MINERS_ETF"}
CRYPTO_LONG_ASSET_CLASSES = {"CRYPTO_ETP", "CRYPTO_ETF"}
CRYPTO_SHORT_ASSET_CLASSES = {"CRYPTO_SHORT_ETF"}
SATELLITE_ASSET_CLASSES = GOLD_ASSET_CLASSES | CRYPTO_LONG_ASSET_CLASSES | CRYPTO_SHORT_ASSET_CLASSES


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "oui"}


def _lane(asset_class: str) -> str:
    asset = str(asset_class or "").strip().upper()
    if asset in {"GOLD_ETC", "GOLD_ETF"}:
        return "GOLD_PHYSICAL"
    if asset == "GOLD_MINERS_ETF":
        return "GOLD_MINERS"
    if asset in CRYPTO_LONG_ASSET_CLASSES:
        return "CRYPTO_LONG"
    if asset in CRYPTO_SHORT_ASSET_CLASSES:
        return "CRYPTO_SHORT_CONTEXT"
    return "EXCLUDED"


def _flow_context(score: float | None, cfg: Mapping) -> str:
    if score is None:
        return "DATA_INSUFFICIENT"
    thresholds = cfg.get("flow_context_thresholds") if isinstance(cfg.get("flow_context_thresholds"), Mapping) else {}
    inflow_min = float((thresholds or {}).get("inflow_support_min", 65.0))
    outflow_max = float((thresholds or {}).get("outflow_warning_max", 35.0))
    if score >= inflow_min:
        return "INFLOW_SUPPORT"
    if score <= outflow_max:
        return "OUTFLOW_WARNING"
    return "FLOW_NEUTRAL_CONTEXT"


def _gold_context(gold_decision: Mapping | None) -> dict:
    payload = dict(gold_decision or {})
    current = payload.get("current_scores") if isinstance(payload.get("current_scores"), Mapping) else {}
    tactical = current.get("TACTICAL_2_12W") if isinstance(current, Mapping) else {}
    strategic = current.get("STRATEGIC_6_24M") if isinstance(current, Mapping) else {}
    return {
        "gold_engine_available": bool(payload),
        "gold_score_ct": _num(payload.get("GOLD_SCORE_CT")),
        "gold_score_mt": _num(payload.get("GOLD_SCORE_MT")),
        "gold_qds": _num(payload.get("QDS_OR")),
        "gold_data_trust": _num(payload.get("DATA_TRUST_OR")),
        "gold_tactical_decision": str((tactical or {}).get("decision") or "UNAVAILABLE"),
        "gold_strategic_decision": str((strategic or {}).get("decision") or "UNAVAILABLE"),
    }


def build_satellite_context(
    external_universe: pd.DataFrame,
    flow_instruments: pd.DataFrame,
    gold_decision: Mapping | None,
    cfg: Mapping,
) -> tuple[pd.DataFrame, dict]:
    required = {"instrument_id", "asset_class", "name", "is_pea", "is_inverse_or_leveraged"}
    missing = required - set(external_universe.columns)
    if missing:
        raise ValueError(f"ETP_SATELLITE_UNIVERSE_MISSING_COLUMNS:{','.join(sorted(missing))}")

    universe = external_universe.copy()
    universe["asset_class"] = universe["asset_class"].fillna("").astype(str).str.upper()
    universe = universe[universe["asset_class"].isin(SATELLITE_ASSET_CLASSES)].copy()
    universe["is_pea"] = universe["is_pea"].map(_parse_bool)
    universe["is_inverse_or_leveraged"] = universe["is_inverse_or_leveraged"].map(_parse_bool)
    if universe["is_pea"].any():
        bad = ",".join(universe.loc[universe["is_pea"], "instrument_id"].astype(str).head(10))
        raise RuntimeError(f"ETP_SATELLITE_PEA_CONTAMINATION:{bad}")

    universe["satellite_lane"] = universe["asset_class"].map(_lane)
    universe = universe[universe["satellite_lane"].ne("EXCLUDED")].copy()

    flow_cols = [
        col for col in (
            "instrument_id", "efs_shadow", "efs_readiness", "flow_price_state",
            "organic_flow_rate_20d", "organic_flow_rate_60d", "as_of",
        ) if col in flow_instruments.columns
    ]
    flows = flow_instruments[flow_cols].drop_duplicates("instrument_id") if flow_cols else pd.DataFrame(columns=["instrument_id"])
    out = universe.merge(flows, on="instrument_id", how="left")
    out["flow_score_shadow"] = pd.to_numeric(out.get("efs_shadow"), errors="coerce")
    out["flow_context_state"] = [
        _flow_context(None if pd.isna(value) else float(value), cfg) for value in out["flow_score_shadow"]
    ]

    contamination = out["is_inverse_or_leveraged"] & out["satellite_lane"].isin(["GOLD_PHYSICAL", "GOLD_MINERS", "CRYPTO_LONG"])
    out.loc[contamination, "flow_context_state"] = "EXCLUDED_INVERSE_LEVERAGED"

    out["lane_rank_shadow"] = pd.NA
    rankable = out["flow_score_shadow"].notna() & ~contamination
    if rankable.any():
        ranks = out.loc[rankable].groupby("satellite_lane")["flow_score_shadow"].rank(method="min", ascending=False)
        out.loc[rankable, "lane_rank_shadow"] = ranks.astype("Int64")

    gold_ctx = _gold_context(gold_decision)
    for key, value in gold_ctx.items():
        out[key] = value if key != "gold_engine_available" else False
    gold_mask = out["satellite_lane"].isin(["GOLD_PHYSICAL", "GOLD_MINERS"])
    for key, value in gold_ctx.items():
        out.loc[gold_mask, key] = value

    out["alpha_engine_status"] = "FLOW_CONTEXT_ONLY_NO_ALPHA_ENGINE"
    out.loc[gold_mask & out["gold_engine_available"].astype(bool), "alpha_engine_status"] = "GOLD_V1_1_CONTEXT_PLUS_FLOWS"
    out.loc[out["satellite_lane"].eq("CRYPTO_LONG"), "alpha_engine_status"] = "CRYPTO_PIT_OOS_ALPHA_NOT_IMPLEMENTED"
    out.loc[out["satellite_lane"].eq("CRYPTO_SHORT_CONTEXT"), "alpha_engine_status"] = "SPECULATIVE_SHORT_CONTEXT_ONLY"

    out["decision_role"] = "CONTEXT_ONLY"
    out.loc[contamination, "decision_role"] = "EXCLUDED"
    out["decision_influence"] = 0.0
    out["pea_score_influence"] = 0.0
    out["cross_lane_ranking_allowed"] = False
    out["t1_t2_enabled"] = False
    out["live_orders_enabled"] = False
    out["real_orders_allowed"] = False
    out["promotion_allowed"] = False

    lane_counts = out.groupby("satellite_lane")["instrument_id"].nunique().astype(int).to_dict()
    summary = {
        "version": str(cfg.get("version", "ETP_SATELLITE_V1.0_SHADOW")),
        "mode": str(cfg.get("mode", "SHADOW_ONLY")),
        "status": "SUCCESS",
        "instrument_count": int(out["instrument_id"].nunique()),
        "lane_counts": {str(key): int(value) for key, value in lane_counts.items()},
        "gold_engine_available": bool(gold_ctx["gold_engine_available"]),
        "flow_scorable_instruments": int(out["flow_score_shadow"].notna().sum()),
        "no_cross_lane_ranking": True,
        "pea_universe_score_influence": 0.0,
        "decision_influence": 0.0,
        "live_orders_enabled": False,
        "real_orders_allowed": False,
        "t1_t2_forbidden": True,
        "crypto_alpha_engine_status": "NOT_IMPLEMENTED_PIT_OOS",
        "gold_context_source": "GOLD_V1_1" if gold_ctx["gold_engine_available"] else "UNAVAILABLE_CURRENT_RUN",
    }
    return out.sort_values(["satellite_lane", "lane_rank_shadow", "instrument_id"], na_position="last").reset_index(drop=True), summary


def write_satellite_outputs(context: pd.DataFrame, summary: dict, root: Path) -> dict[str, str]:
    out_dir = root / "outputs" / "etp_satellite"
    audit_dir = root / "outputs" / "audit"
    mobile_dir = root / "outputs" / "mobile"
    for directory in (out_dir, audit_dir, mobile_dir):
        directory.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "ETP_SATELLITE_CONTEXT_SHADOW.csv"
    json_path = audit_dir / "ETP_SATELLITE_V1_SHADOW.json"
    mobile_path = mobile_dir / "ETP_SATELLITES_SHADOW.md"
    context.to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# ETP Satellite V1 — Or / Crypto SHADOW", "",
        "- PEA: **non** — voie satellite séparée",
        "- Influence décisionnelle: **0**",
        "- Ordres réels: **désactivés**",
        "- Classement commun PEA/Or/Crypto: **interdit**",
        "- T1/T2: **interdits**", "",
    ]
    for lane, group in context.groupby("satellite_lane", sort=True):
        lines.extend([f"## {lane}", ""])
        for _, row in group.sort_values("lane_rank_shadow", na_position="last").head(10).iterrows():
            score = row.get("flow_score_shadow")
            score_text = "n/a" if pd.isna(score) else f"{float(score):.1f}"
            lines.append(f"- {row.get('name', row.get('instrument_id'))}: flow {score_text} — {row.get('flow_context_state')} — {row.get('alpha_engine_status')}")
        lines.append("")
    mobile_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "context_csv": str(csv_path.relative_to(root)),
        "audit_json": str(json_path.relative_to(root)),
        "mobile_md": str(mobile_path.relative_to(root)),
    }
