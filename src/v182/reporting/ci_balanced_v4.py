from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _band(value: object, bands: list[dict]) -> float | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    for band in bands:
        if float(numeric) >= float(band["minimum"]):
            return float(band["score"])
    return None


def _component(value: object, mapping: dict[str, float]) -> float | None:
    if pd.isna(value):
        return None
    key = str(value).strip().upper()
    return float(mapping[key]) if key in mapping else None


def _clean(value: object) -> str:
    if pd.isna(value):
        return ""
    text = " ".join(str(value).strip().upper().split())
    return "" if text in {"", "NAN", "NONE", "N/A", "NA", "UNKNOWN"} else text


def _economic_family(row: pd.Series) -> str:
    benchmark = _clean(row.get("OVERLAP_OFFICIAL_BENCHMARK"))
    category = _clean(row.get("OVERLAP_CATEGORY"))
    geo = _clean(row.get("OVERLAP_GEO_EXPOSURE"))
    if benchmark:
        return f"BENCHMARK:{benchmark}"
    if category and geo:
        return f"CATEGORY_GEO:{category}|{geo}"
    if category:
        return f"CATEGORY:{category}"
    return f"UNRESOLVED:{_clean(row.get('isin'))}"


def run(root: Path = ROOT) -> dict:
    config = json.loads((root / "config/CI_BALANCED_V4.json").read_text(encoding="utf-8"))
    light = pd.concat(
        [
            _read(root / "outputs/committee_master/CI_LIGHT_V4.csv"),
            _read(root / "outputs/committee_master/CI_LIGHT_REJECTED_V4.csv"),
        ],
        ignore_index=True,
    ).drop_duplicates(["isin", "horizon"], keep="first")
    committee = _read(root / "outputs/committee_master/COMMITTEE_DECISIONS.csv")
    committee = committee[committee["decision"].astype(str).eq("BUY_CANDIDATE")].copy()
    committee["score"] = pd.to_numeric(committee["score"], errors="coerce")
    committee = committee[["isin", "horizon", "score", "decision"]].rename(
        columns={"score": "BALANCED_COMMITTEE_SCORE", "decision": "BALANCED_COMMITTEE_DECISION"}
    )
    frame = light.merge(committee, on=["isin", "horizon"], how="left")
    signal_scores = config["signal_scores"]
    recommendation_scores = config["boursorama_recommendation_scores"]
    frame["BALANCED_TV_DAILY_SCORE"] = frame["CI_LIGHT_TRADINGVIEW_DAILY"].map(
        lambda value: _component(value, signal_scores)
    )
    frame["BALANCED_TV_WEEKLY_SCORE"] = frame["CI_LIGHT_TRADINGVIEW_WEEKLY"].map(
        lambda value: _component(value, signal_scores)
    )
    frame["BALANCED_TV_MONTHLY_SCORE"] = frame["CI_LIGHT_TRADINGVIEW_MONTHLY"].map(
        lambda value: _component(value, signal_scores)
    )
    frame["BALANCED_BOURSORAMA_RECOMMENDATION_SCORE"] = frame[
        "CI_LIGHT_BOURSORAMA_RECOMMENDATION"
    ].map(lambda value: _component(value, recommendation_scores))
    frame["BALANCED_BOURSORAMA_UPSIDE_SCORE"] = frame["CI_LIGHT_BOURSORAMA_UPSIDE_PCT"].map(
        lambda value: _band(value, config["upside_bands"])
    )
    frame["BALANCED_BOURSORAMA_ANALYST_SCORE"] = frame["CI_LIGHT_BOURSORAMA_ANALYSTS"].map(
        lambda value: _band(value, config["analyst_bands"])
    )
    columns = {
        "BALANCED_COMMITTEE_SCORE": "committee_score",
        "BALANCED_TV_DAILY_SCORE": "tradingview_daily",
        "BALANCED_TV_WEEKLY_SCORE": "tradingview_weekly",
        "BALANCED_TV_MONTHLY_SCORE": "tradingview_monthly",
        "BALANCED_BOURSORAMA_RECOMMENDATION_SCORE": "boursorama_recommendation",
        "BALANCED_BOURSORAMA_UPSIDE_SCORE": "boursorama_upside",
        "BALANCED_BOURSORAMA_ANALYST_SCORE": "boursorama_analyst_breadth",
    }
    numerator = pd.Series(0.0, index=frame.index)
    denominator = pd.Series(0.0, index=frame.index)
    for column, weight_name in columns.items():
        values = pd.to_numeric(frame[column], errors="coerce")
        weight = float(config["weights"][weight_name])
        observed = values.notna()
        numerator += values.fillna(0.0) * weight
        denominator += observed.astype(float) * weight
    frame["BALANCED_AVAILABLE_WEIGHT"] = denominator.round(4)
    frame["BALANCED_SCORE"] = (numerator / denominator.replace(0, pd.NA)).astype(float).round(4)
    frame["POTENTIEL_BOURSORAMA_PCT"] = pd.to_numeric(
        frame["CI_LIGHT_BOURSORAMA_UPSIDE_PCT"], errors="coerce"
    ).where(frame["asset_class"].astype(str).eq("ACTION"))
    action_master = _read(root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv").copy()
    action_master["_YAHOO_POTENTIAL_PCT"] = pd.to_numeric(
        action_master.get("upside_pct_yf"), errors="coerce"
    )
    target = pd.to_numeric(action_master.get("target_mean_yf"), errors="coerce")
    price = pd.to_numeric(action_master.get("last_close"), errors="coerce").replace(0, pd.NA)
    derived = (target / price - 1.0) * 100.0
    action_master["_YAHOO_POTENTIAL_PCT"] = action_master["_YAHOO_POTENTIAL_PCT"].fillna(derived)
    frame = frame.merge(
        action_master[["isin", "_YAHOO_POTENTIAL_PCT"]].drop_duplicates("isin"),
        on="isin",
        how="left",
    )
    frame["POTENTIEL_PCT"] = frame["POTENTIEL_BOURSORAMA_PCT"].fillna(
        frame["_YAHOO_POTENTIAL_PCT"]
    ).where(frame["asset_class"].astype(str).eq("ACTION"))
    frame["POTENTIEL_SOURCE"] = pd.NA
    frame.loc[frame["POTENTIEL_BOURSORAMA_PCT"].notna(), "POTENTIEL_SOURCE"] = "BOURSORAMA"
    frame.loc[
        frame["POTENTIEL_BOURSORAMA_PCT"].isna()
        & frame["_YAHOO_POTENTIAL_PCT"].notna()
        & frame["asset_class"].astype(str).eq("ACTION"),
        "POTENTIEL_SOURCE",
    ] = "YAHOO_TARGET_MEAN"
    frame["RECOMMANDATION_BOURSORAMA"] = frame["CI_LIGHT_BOURSORAMA_RECOMMENDATION"]
    frame["NOTATION_CT"] = frame["CI_LIGHT_TRADINGVIEW_DAILY"]
    frame["NOTATION_MT"] = frame["CI_LIGHT_TRADINGVIEW_WEEKLY"]
    frame["NOTATION_LT"] = frame["CI_LIGHT_TRADINGVIEW_MONTHLY"]
    frame["MORNINGSTAR_ETOILES"] = pd.to_numeric(
        frame["CI_LIGHT_MORNINGSTAR_RATING"], errors="coerce"
    )
    etf_master = _read(root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    overlap_fields = ["isin", "official_benchmark", "category", "geo_exposure"]
    etf_overlap = etf_master[[field for field in overlap_fields if field in etf_master.columns]].copy()
    etf_overlap = etf_overlap.rename(
        columns={
            "official_benchmark": "OVERLAP_OFFICIAL_BENCHMARK",
            "category": "OVERLAP_CATEGORY",
            "geo_exposure": "OVERLAP_GEO_EXPOSURE",
        }
    ).drop_duplicates("isin")
    frame = frame.merge(etf_overlap, on="isin", how="left")
    frame["ETF_ECONOMIC_FAMILY"] = frame.apply(
        lambda row: _economic_family(row) if str(row.get("asset_class")) == "ETF" else pd.NA,
        axis=1,
    )
    current_candidate = frame["BALANCED_COMMITTEE_DECISION"].eq("BUY_CANDIDATE")
    enough_evidence = denominator.ge(float(config["minimum_available_weight"]))
    enough_score = frame["BALANCED_SCORE"].ge(float(config["minimum_score"]))
    is_action = frame["asset_class"].astype(str).eq("ACTION")
    action_potential_ok = ~is_action | frame["POTENTIEL_PCT"].ge(
        float(config["action_minimum_potential_pct"])
    )
    is_etf = frame["asset_class"].astype(str).eq("ETF")
    etf_mt_ok = ~is_etf | frame["NOTATION_MT"].astype(str).str.upper().eq(
        str(config["etf_required_mt_signal"]).upper()
    )
    selected = current_candidate & enough_evidence & enough_score & action_potential_ok & etf_mt_ok
    frame["BALANCED_SELECTED"] = selected
    frame["BALANCED_REASON"] = "SELECTED"
    frame.loc[~current_candidate, "BALANCED_REASON"] = "NOT_CURRENT_COMMITTEE_BUY_CANDIDATE"
    frame.loc[current_candidate & ~enough_evidence, "BALANCED_REASON"] = "INSUFFICIENT_FACTUAL_WEIGHT"
    frame.loc[current_candidate & enough_evidence & ~enough_score, "BALANCED_REASON"] = "WEIGHTED_SCORE_BELOW_THRESHOLD"
    frame.loc[
        current_candidate & enough_evidence & enough_score & ~action_potential_ok,
        "BALANCED_REASON",
    ] = "ACTION_POTENTIAL_BELOW_20_OR_MISSING"
    frame.loc[
        current_candidate
        & enough_evidence
        & enough_score
        & action_potential_ok
        & ~etf_mt_ok,
        "BALANCED_REASON",
    ] = "ETF_MT_NOT_STRONG_BUY"
    overlap_removed = 0
    if bool(config["etf_overlap"]["enabled"]):
        maximum = int(config["etf_overlap"]["maximum_per_economic_family"])
        etf_selected = frame[frame["BALANCED_SELECTED"] & is_etf].sort_values(
            "BALANCED_SCORE", ascending=False
        )
        ranks = etf_selected.groupby("ETF_ECONOMIC_FAMILY", dropna=False).cumcount() + 1
        duplicate_indexes = etf_selected.index[ranks > maximum]
        overlap_removed = int(len(duplicate_indexes))
        frame.loc[duplicate_indexes, "BALANCED_SELECTED"] = False
        frame.loc[duplicate_indexes, "BALANCED_REASON"] = "ETF_ECONOMIC_OVERLAP_LOWER_SCORE"
    selected = frame["BALANCED_SELECTED"]
    frame = frame.sort_values("BALANCED_SCORE", ascending=False, na_position="last")
    outdir = root / "outputs/committee_master"
    selected_path = outdir / "CI_BALANCED_V4.csv"
    all_path = outdir / "CI_BALANCED_ALL_V4.csv"
    selected_columns = [
        "isin",
        "name",
        "asset_class",
        "horizon",
        "BALANCED_SCORE",
        "BALANCED_AVAILABLE_WEIGHT",
        "POTENTIEL_PCT",
        "POTENTIEL_SOURCE",
        "POTENTIEL_BOURSORAMA_PCT",
        "RECOMMANDATION_BOURSORAMA",
        "NOTATION_CT",
        "NOTATION_MT",
        "NOTATION_LT",
        "MORNINGSTAR_ETOILES",
        "ETF_ECONOMIC_FAMILY",
        "BALANCED_SELECTED",
        "BALANCED_REASON",
    ]
    frame.loc[frame["BALANCED_SELECTED"], selected_columns].to_csv(
        selected_path, sep=";", index=False, encoding="utf-8-sig"
    )
    frame.to_csv(all_path, sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS",
        "version": config["version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_rows": int(len(frame)),
        "current_committee_candidates": int(current_candidate.sum()),
        "enough_factual_weight": int((current_candidate & enough_evidence).sum()),
        "selected": int(selected.sum()),
        "selected_actions": int((selected & frame["asset_class"].astype(str).eq("ACTION")).sum()),
        "selected_etfs": int((selected & frame["asset_class"].astype(str).eq("ETF")).sum()),
        "etf_overlap_removed": overlap_removed,
        "etf_overlap_method": str(config["etf_overlap"]["fallback_method"]),
        "minimum_score": float(config["minimum_score"]),
        "minimum_available_weight": float(config["minimum_available_weight"]),
        "action_minimum_potential_pct": float(config["action_minimum_potential_pct"]),
        "etf_required_mt_signal": str(config["etf_required_mt_signal"]),
        "weights": config["weights"],
        "missing_source_is_negative": False,
        "full_222_referential_reclassified": True,
        "decision_mutation": False,
        "t1_t2_score_influence": 0.0,
        "real_orders_enabled": False,
        "outputs": {"selected": str(selected_path.relative_to(root)), "all": str(all_path.relative_to(root))},
    }
    audit_path = root / "outputs/audit/CI_BALANCED_V4.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
