from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.decision.committee_master import (
    criterion_coverage_report, decisions_from_scores, load_registry,
    overlay_etf_mt, sector_ranking, tct_adapter,
)
from v182.decision.action_overlays_v21_3 import apply_action_52w_overlay
from v182.decision.effective_weights import effective_weight_report
from v182.decision.etf_structural_overlay import apply_etf_structural_overlay
from v182.decision.tct_baseline_v24_1_8 import build_tct_baseline, NORMALIZATION_POLICY
from v182.decision.tct_timing_exact_v24_1_7 import build_exact_timing_snapshot
from v182.decision.tct_v24_1_7 import load_tct_config
from v182.reporting.horizon_cache_policy import write_horizon_priority_state

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]


def _read_table(path:Path)->pd.DataFrame:
    if not path.exists(): return pd.DataFrame()
    errors=[]
    for sep in (";",",","\t"):
        try:
            df=pd.read_csv(path,sep=sep,encoding="utf-8-sig",low_memory=False)
            if len(df.columns)>1: return df
        except Exception as exc:
            errors.append(f"{repr(sep)}:{type(exc).__name__}:{str(exc)[:120]}")
    try: return pd.read_csv(path,sep=None,engine="python",encoding="utf-8-sig",low_memory=False)
    except Exception as exc:
        logger.error("Unable to read %s; attempts=%s; fallback=%s: %s",path,errors,type(exc).__name__,exc); raise


def _first_existing(paths:list[Path])->Path|None:
    return next((p for p in paths if p.exists()),None)


def _enforce_canonical_actions(actions:pd.DataFrame,root:Path)->tuple[pd.DataFrame,dict]:
    if actions.empty: return actions,{"status":"EMPTY","canonical_rows":0,"excluded_rows":0}
    result=filter_actions(actions,root/"config"/"V21_3_ACTION_UNIVERSE_1829_ISINS.parts")
    audit={"status":"PASS","input_rows":int(len(actions)),"canonical_rows":int(len(result.included)),"excluded_rows":int(len(result.excluded)),"whitelist_count":int(result.whitelist_count),"whitelist_sha256":result.whitelist_sha256,"reference":"V21.3_1829"}
    return result.included.reset_index(drop=True),audit


def _failed_horizon(asset_class:str,horizon:str,version:str,exc:Exception)->pd.DataFrame:
    return pd.DataFrame([{"asset_class":asset_class,"horizon":horizon,"isin":"","name":f"{asset_class} {horizon} MODULE","sector":"TRANSVERSAL","score":None,"coverage_pct":0.0,"status":"FAILED","decision":"FAILED","active_criteria":0,"available_criteria":0,"score_source":version,"backtest_attribution":"","notes":f"{type(exc).__name__}: {str(exc)[:240]}"}])


def _safe_horizons(frame:pd.DataFrame,registry:dict,asset_class:str,horizons:list[str])->tuple[list[pd.DataFrame],list[pd.DataFrame],list[dict]]:
    decisions=[]; coverages=[]; failures=[]
    for horizon in horizons:
        try:
            decisions.append(decisions_from_scores(frame,registry,asset_class,[horizon])); coverages.append(criterion_coverage_report(frame,registry,asset_class,[horizon]))
        except Exception as exc:
            logger.exception("Committee %s %s failed without aborting other horizons",asset_class,horizon)
            decisions.append(_failed_horizon(asset_class,horizon,registry.get("version",""),exc)); failures.append({"asset_class":asset_class,"horizon":horizon,"error":type(exc).__name__,"detail":str(exc)[:240]})
    return decisions,coverages,failures


def _write_effective_weights(outdir:Path, actions:pd.DataFrame, etfs:pd.DataFrame, actions_reg:dict, etf_reg:dict)->dict:
    parts=[]
    if not actions.empty: parts.append(effective_weight_report(actions,actions_reg,"ACTION",["CT","MT","SHORT","TOP_DOWN"]))
    if not etfs.empty: parts.append(effective_weight_report(etfs,etf_reg,"ETF",["CT","SHORT","TOP_DOWN"]))
    weights=pd.concat([p for p in parts if not p.empty],ignore_index=True) if parts else pd.DataFrame()
    csv_path=outdir/"EFFECTIVE_WEIGHTS_100.csv"; xlsx_path=outdir/"EFFECTIVE_WEIGHTS_100.xlsx"
    weights.to_csv(csv_path,sep=";",index=False,encoding="utf-8-sig")
    summary=pd.DataFrame()
    if not weights.empty:
        summary=(weights.groupby(["asset_class","horizon","isin","name"],dropna=False).agg(effective_weight_sum_pct=("effective_weight_pct","sum"),available_raw_weight_pct=("available_raw_weight_pct","max"),available_criteria=("criterion_available","sum"),active_criteria=("criterion","count")).reset_index())
    with pd.ExcelWriter(xlsx_path,engine="openpyxl") as writer:
        summary.to_excel(writer,sheet_name="Synthese",index=False); weights.to_excel(writer,sheet_name="Poids_effectifs",index=False)
    return {"csv":str(csv_path),"xlsx":str(xlsx_path),"rows":int(len(weights)),"policy":"AVAILABLE_ACTIVE_CRITERIA_RENORMALIZED_TO_100"}


def run(root:Path=ROOT)->dict:
    config_dir=root/"config"; outputs=root/"outputs"; outdir=outputs/"committee_master"; outdir.mkdir(parents=True,exist_ok=True)
    master_cfg=json.loads((config_dir/"COMMITTEE_MASTER_V21.json").read_text(encoding="utf-8")); actions_reg=load_registry(config_dir/"V21_ACTIONS_CRITERIA_REGISTRY.json"); etf_reg=load_registry(config_dir/"V20_7_1_ETF_CRITERIA_REGISTRY.json"); tct_cfg=load_tct_config(config_dir/"TCT_V24_1_7_SHADOW.json")
    actions_path=_first_existing([outputs/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",root/"inputs"/"V18.2_PEA_ACTIONS_MASTER.csv"]); etf_path=_first_existing([outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",root/"inputs"/"V18.2_PEA_ETF_MASTER.csv"])
    actions=_read_table(actions_path) if actions_path else pd.DataFrame(); etfs=_read_table(etf_path) if etf_path else pd.DataFrame(); canonical_audit={"status":"NO_INPUT","canonical_rows":0,"excluded_rows":0}
    if not actions.empty:
        actions,canonical_audit=_enforce_canonical_actions(actions,root)
        if canonical_audit.get("excluded_rows",0): logger.warning("Committee excluded %s rows outside V21.3 1829 universe",canonical_audit["excluded_rows"])

    parts=[]; coverage_parts=[]; horizon_failures=[]; tct_baseline_audit={"status":"NOT_RUN"}; tct_exact_audit={"status":"NOT_RUN"}
    if not actions.empty:
        d,c,f=_safe_horizons(actions,actions_reg,"ACTION",["CT","MT","SHORT","TOP_DOWN"]); parts.extend(d); coverage_parts.extend(c); horizon_failures.extend(f)
        try:
            actions_with_tct,baseline=build_tct_baseline(actions,tct_cfg)
            tct_baseline_audit={"status":"SUCCESS","scoring_version":"V24.1.8_DYNAMIC_NORMALIZATION_SHADOW","universe_rows":baseline.universe_rows,"pea_gate_pass_rows":baseline.pea_gate_pass_rows,"coverage_pass_rows":baseline.coverage_pass_rows,"ranked_rows":baseline.ranked_rows,"top20_rows":baseline.top20_rows,"max_score":baseline.max_score,"max_coverage":baseline.max_coverage,"minimum_coverage":float(tct_cfg["scope"]["baseline_min_coverage"]),"setup_component_active":False,"missing_weight_policy":NORMALIZATION_POLICY,"available_active_pillars_effective_weight_sum_pct":100.0,"t1_t2_score_influence":0.0,"historical_performance_attribution":"NONE_UNTIL_V24_1_8_PIT_BACKTEST"}
            actions_with_tct.to_csv(outdir/"TCT_BASELINE_V24_1_8.csv",sep=";",index=False,encoding="utf-8-sig")
            state_path=root/str(tct_cfg.get("state",{}).get("path","state/TCT_V24_1_7_T1_STATE.json")); tct_shadow,exact=build_exact_timing_snapshot(actions_with_tct,root/"data"/"cache"/"actions",state_path,tct_cfg)
            tct_exact_audit={"status":"SUCCESS","formula_version":tct_cfg.get("formula_version"),"actions_rows":exact.actions_rows,"histories_found":exact.histories_found,"histories_usable":exact.histories_usable,"t1_detected_raw":exact.t1_detected_raw,"t1_baseline_eligible":exact.t1_baseline_eligible,"t2_confirmed":exact.t2_confirmed,"active_state_records":exact.active_state_records,"expired_state_records":exact.expired_state_records,"state_path":str(state_path.relative_to(root)),"t1_sequence":"5_SESSION_CONTINUOUS_SQUEEZE_THEN_BB_BREAKOUT_BANDS_EXPAND_STOCH_CROSS_VOLUME_RISE_MACD_BELOW_SIGNAL_CLOSE_GT_SAR_MM50","t2_sequence":"LINKED_T1_THEN_CLOSE_GT_BB_BANDS_EXPAND_STOCH_HOLD_MACD_CROSS_VOLUME_RISE_CLOSE_GT_SAR_MM50","quality_components_t1":6,"quality_components_t2":6,"quality_min_coverage":0.80,"live_orders_enabled":False,"score_influence":0.0}
        except Exception as exc:
            logger.exception("TCT baseline/exact timing failed"); tct_exact_audit={"status":"FAILED","error":type(exc).__name__,"detail":str(exc)[:240]}; tct_shadow=_failed_horizon("ACTION","TCT",tct_cfg.get("version","V24.1.7"),exc); horizon_failures.append({"asset_class":"ACTION","horizon":"TCT","error":type(exc).__name__,"detail":str(exc)[:240]})
        tct_shadow.to_csv(outdir/"TCT_SHADOW_V24_1_7.csv",sep=";",index=False,encoding="utf-8-sig"); parts.append(tct_adapter(tct_shadow))
    else:
        parts.append(_failed_horizon("ACTION","ALL","V21.3",RuntimeError("Actions master input missing"))); parts.append(tct_adapter())

    if not etfs.empty:
        d,c,f=_safe_horizons(etfs,etf_reg,"ETF",["CT","SHORT","TOP_DOWN"]); parts.extend(d); coverage_parts.extend(c); horizon_failures.extend(f)
    else: parts.append(_failed_horizon("ETF","CT/SHORT/TOP_DOWN","V20.7.1/V20.7",RuntimeError("ETF master input missing")))

    mt_path=_first_existing([outputs/"etf_mt_v2081"/"V20.8.1_ETF_MT_RANKING.csv",outputs/"etf_mt_v2081"/"V20_8_1_ETF_MT_RANKING.csv"])
    try: parts.append(overlay_etf_mt(etfs,_read_table(mt_path) if mt_path else None))
    except Exception as exc:
        logger.exception("ETF MT overlay failed"); parts.append(_failed_horizon("ETF","MT","V20.8.1_DYNAMIC_38_CORE",exc)); horizon_failures.append({"asset_class":"ETF","horizon":"MT","error":type(exc).__name__,"detail":str(exc)[:240]})

    decisions=pd.concat([p for p in parts if p is not None and not p.empty],ignore_index=True,sort=False); decisions=apply_etf_structural_overlay(decisions,etfs,etf_reg); decisions=apply_action_52w_overlay(decisions,actions,actions_reg)
    criterion_coverage=pd.concat([p for p in coverage_parts if p is not None and not p.empty],ignore_index=True,sort=False) if coverage_parts else pd.DataFrame(); effective_weights=_write_effective_weights(outdir,actions,etfs,actions_reg,etf_reg)
    generated=datetime.now(timezone.utc).isoformat(); decisions["generated_at_utc"]=generated; decisions["live_orders_enabled"]=False; ranks=sector_ranking(decisions)
    decisions.to_csv(outdir/"COMMITTEE_DECISIONS.csv",sep=";",index=False,encoding="utf-8-sig"); ranks.to_csv(outdir/"SECTOR_RANKING.csv",sep=";",index=False,encoding="utf-8-sig"); criterion_coverage.to_csv(outdir/"CRITERIA_COVERAGE.csv",sep=";",index=False,encoding="utf-8-sig")
    horizon_priority_state=write_horizon_priority_state(decisions,root/"state"/"provenance"/"HORIZON_REFRESH_PRIORITY_V1.csv",generated_at_utc=generated)
    if horizon_failures: pd.DataFrame(horizon_failures).to_csv(outdir/"HORIZON_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    status_counts=decisions.groupby(["asset_class","horizon","status"],dropna=False).size().reset_index(name="count"); decision_counts=decisions.groupby(["asset_class","horizon","decision"],dropna=False).size().reset_index(name="count"); missing_by_horizon=[]
    if not criterion_coverage.empty: missing_by_horizon=(criterion_coverage[criterion_coverage["criterion_status"]=="MISSING"].groupby(["asset_class","horizon"])["criterion"].apply(list).reset_index(name="missing_criteria").to_dict("records"))
    tct_status=decisions[decisions["horizon"]=="TCT"]["status"].value_counts().to_dict() if "horizon" in decisions else {}; etf_overlay=decisions[(decisions["asset_class"]=="ETF") & decisions["base_score"].notna()] if "base_score" in decisions else pd.DataFrame(); action_overlay=decisions[(decisions["asset_class"]=="ACTION") & decisions["base_score"].notna()] if "base_score" in decisions else pd.DataFrame()
    overlay_summary={"rows_with_base_score":int(len(etf_overlay)),"rows_with_morningstar_bonus":int((pd.to_numeric(etf_overlay.get("morningstar_bonus"),errors="coerce")>0).sum()) if not etf_overlay.empty else 0,"rows_with_risk_malus":int((pd.to_numeric(etf_overlay.get("risk_malus"),errors="coerce")<0).sum()) if not etf_overlay.empty else 0,"mt_core_selection_unchanged":True,"positive_bonus_cannot_create_buy":True}
    action_summary={"rows_with_52w_overlay":int(len(action_overlay)),"positive_52w_bonus_rows":int((pd.to_numeric(action_overlay.get("high_52w_bonus_malus_points"),errors="coerce")>0).sum()) if not action_overlay.empty else 0,"near_high_malus_rows":int((pd.to_numeric(action_overlay.get("high_52w_bonus_malus_points"),errors="coerce")<0).sum()) if not action_overlay.empty else 0}
    summary={"version":master_cfg["version"],"status":master_cfg["status"],"generated_at_utc":generated,"live_orders_enabled":False,"input_files":{"actions":str(actions_path.relative_to(root)) if actions_path else None,"etf":str(etf_path.relative_to(root)) if etf_path else None,"etf_mt":str(mt_path.relative_to(root)) if mt_path else None},"canonical_actions":canonical_audit,"registry_integrity":{"actions_universe_expected":1829,"actions_universe_loaded":int(len(actions)),"actions_criteria_expected":633,"actions_criteria_loaded":int(actions_reg.get("criteria_count",0)),"etf_fields_expected":268,"etf_fields_loaded":int(etf_reg.get("criteria_count",0)),"t1_t2_scope":"ACTION_TCT_ONLY","tct_formula_version":tct_cfg.get("formula_version")},"dynamic_weighting":{"policy":"AVAILABLE_ACTIVE_CRITERIA_RENORMALIZED_TO_100","effective_weight_report":effective_weights,"minimum_coverage_gates_preserved":True,"neutral_imputation_forbidden":True},"horizon_refresh_priority_state":horizon_priority_state,"tct_baseline":tct_baseline_audit,"tct_exact_timing":tct_exact_audit,"etf_structural_overlay":overlay_summary,"action_52w_rotation_overlay":action_summary,"status_counts":status_counts.to_dict("records"),"decision_counts":decision_counts.to_dict("records"),"missing_active_criteria_by_horizon":missing_by_horizon,"tct_shadow_status":tct_status,"horizon_failures":horizon_failures,"outputs":{"decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv","sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv","criteria_coverage":"outputs/committee_master/CRITERIA_COVERAGE.csv","effective_weights_csv":"outputs/committee_master/EFFECTIVE_WEIGHTS_100.csv","effective_weights_xlsx":"outputs/committee_master/EFFECTIVE_WEIGHTS_100.xlsx","tct_baseline":"outputs/committee_master/TCT_BASELINE_V24_1_8.csv","tct_shadow":"outputs/committee_master/TCT_SHADOW_V24_1_7.csv","tct_state":"state/TCT_V24_1_7_T1_STATE.json","horizon_refresh_priority":"state/provenance/HORIZON_REFRESH_PRIORITY_V1.csv"},"notes":["Committee enforces the exact V21.3 1829-Action whitelist recovered as 1429 canonical + 400 validated quarantine.","Available active criteria are renormalized to 100% per instrument while minimum coverage gates remain enforced.","TCT V24.1.8 dynamically renormalizes available active pillars; setup remains intentionally excluded and T1/T2 remain timing-only with zero baseline-score influence.","V24.1.8 and V21.3 Actions reweighting have no historical performance attribution until dedicated PIT backtests.","Action CT/MT scores receive an explicit 52-week-high bonus/malus; far-from-high bonus requires recovery evidence upstream.","ETF Morningstar/risk remains a Committee layer; ETF MT historical 90.91% remains attributable only to the exact 38 PIT core unless a new dynamic challenger is separately validated.","Actions LT, ETF LT, Gold, Crypto/ETP and IPO are excluded from the active Committee scope."]}
    (outdir/"SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2)); return summary


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); run(Path(args.root))


if __name__=="__main__": main()
