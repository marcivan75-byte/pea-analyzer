from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import os

from v182.audit import criteria_study_governance
from v182.reporting import run as enrichment_run
from v182.reporting import cdc_refresh, etf_structure_refresh, etf_mt_v2081_run, committee_master_v21_4, committee_performance_v21_4
from v182.decision import gold_v1_1
from v182.risk import stop_loss_policy

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]
SOFTWARE_VERSION="21.6.3"
PROCESS_VERSION="UNIFIED_V21_6_3_CDC_POSTSELECTION_CRITERIA_STUDY"


def _safe_step(name:str,func)->dict:
    try:
        result=func(); result=dict(result.__dict__) if hasattr(result,"__dict__") else result
        return {"status":"SUCCESS","result":result}
    except Exception as exc:
        logger.exception("Unified runner step %s failed; independent steps continue",name)
        return {"status":"FAILED","error":type(exc).__name__,"detail":str(exc)[:500]}


def _skip_dependency(reason:str)->dict:
    return {"status":"SKIPPED_DEPENDENCY","reason":reason}


def _exit_code(payload:dict)->int:
    return 0 if payload.get("status")=="SUCCESS" else 1


def _criteria_governance_gate(root: Path) -> dict:
    payload=criteria_study_governance.run(root,root/"outputs"/"audit"/"CRITERIA_STUDY_GOVERNANCE.json")
    if payload.get("status") != "PASS":
        raise RuntimeError(f"CRITERIA_STUDY_GOVERNANCE_FAILED high={payload.get('high')}")
    return payload


def run(root:Path=ROOT)->dict:
    """Runtime with criterion-study gate, CDC, ETF rank history, stop-loss governance and postselection confirmation."""
    outdir=root/"outputs"/"unified"; outdir.mkdir(parents=True,exist_ok=True); run_id=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    steps={}
    steps["criteria_governance"]=_safe_step("criteria_governance",lambda:_criteria_governance_gate(root))
    if steps["criteria_governance"]["status"]=="SUCCESS":
        steps["refresh"]=_safe_step("refresh",enrichment_run.run)
    else:
        steps["refresh"]=_skip_dependency("Criterion-study governance must PASS before any scoring refresh.")
    if steps["refresh"]["status"]=="SUCCESS":
        steps["cdc"]=_safe_step("cdc",lambda:cdc_refresh.run(root))
        steps["etf_structure"]=_safe_step("etf_structure",lambda:etf_structure_refresh.run(root))
    else:
        steps["cdc"]=_skip_dependency("Requires SUCCESS current refresh; CDC observations may not be merged into a stale Action master.")
        steps["etf_structure"]=_skip_dependency("Requires SUCCESS current refresh; ETF structure/rank observations may not be merged into a stale ETF master.")
    if steps["criteria_governance"]["status"]=="SUCCESS":
        steps["etf_mt"]=_safe_step("etf_mt",etf_mt_v2081_run.run)
        steps["gold"]=_safe_step("gold",lambda:gold_v1_1.run(root,os.environ.get("FRED_API_KEY")))
    else:
        steps["etf_mt"]=_skip_dependency("Criterion-study governance failed.")
        steps["gold"]=_skip_dependency("Criterion-study governance failed.")
    committee_dependencies=("criteria_governance","refresh","cdc","etf_structure")
    if all(steps[name]["status"]=="SUCCESS" for name in committee_dependencies):
        steps["committee"]=_safe_step("committee",lambda:committee_master_v21_4.run(root))
    else:
        steps["committee"]=_skip_dependency("Requires PASS criterion-study governance and SUCCESS refresh + CDC + ETF structure/rank stages.")
    if steps["committee"]["status"]=="SUCCESS":
        steps["stop_loss"]=_safe_step("stop_loss",lambda:stop_loss_policy.run(root))
    else:
        steps["stop_loss"]=_skip_dependency("Requires SUCCESS current Committee decisions before stop-loss plans can be attached.")
    if steps["committee"]["status"]=="SUCCESS" and steps["stop_loss"]["status"]=="SUCCESS":
        steps["performance"]=_safe_step("performance",lambda:committee_performance_v21_4.run(root))
    else:
        steps["performance"]=_skip_dependency("Requires SUCCESS current Committee and stop-loss governance; stale or unprotected virtual transactions are forbidden.")
    outputs={
        "decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv",
        "stop_loss_plan":"outputs/committee_master/STOP_LOSS_PLAN.csv",
        "stop_loss_audit":"outputs/audit/STOP_LOSS_GOVERNANCE.json",
        "postselection_market_sheets":"outputs/committee_master/POSTSELECTION_MARKET_SHEETS.csv",
        "postselection_failures":"outputs/gaps/V21_6_3_POSTSELECTION_MARKET_SHEETS_FAILURES.csv",
        "sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv",
        "sector_ranking_challenger":"outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_7.csv",
        "sector_ranking_challenger_legacy_alias":"outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_4.csv",
        "action_reference_vs_challenger":"outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_7.csv",
        "action_reference_vs_challenger_legacy_alias":"outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv",
        "criteria_coverage":"outputs/committee_master/CRITERIA_COVERAGE.csv",
        "criteria_study_audit":"outputs/audit/CRITERIA_STUDY_GOVERNANCE.json",
        "effective_weights":"outputs/committee_master/EFFECTIVE_WEIGHTS_100.xlsx",
        "tct_baseline":"outputs/committee_master/TCT_BASELINE_V24_1_8.csv",
        "tct_shadow":"outputs/committee_master/TCT_SHADOW_V24_1_7.csv",
        "collection_audit_latest":"outputs/data_audit/COLLECTION_DATA_AVAILABILITY_LATEST.xlsx",
        "cdc_audit":"outputs/audit/V21_6_3_CDC_REFRESH.json",
        "eps_estimate_history":"state/finnhub/EPS_ESTIMATE_HISTORY.csv",
        "boursorama_etf_rank_history":"state/boursorama/ETF_CATEGORY_RANK_HISTORY.csv",
        "canonical_etf_rank_history":"state/boursorama/ETF_CANONICAL_CATEGORY_RANK_HISTORY.csv",
        "provenance":"state/provenance/OBSERVATION_PROVENANCE.csv",
        "sector_rotation":"outputs/V21_3_SECTOR_ROTATION.csv",
        "performance_workbook":"outputs/performance/COMMITTEE_BUY_PERFORMANCE.xlsx",
        "etf_mt_ranking":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_RANKING.csv",
        "etf_mt_summary":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_SUMMARY.json",
        "gold_decision":"outputs/gold_v1_1/GOLD_V1_1_DECISION.json",
        "gold_criteria":"outputs/gold_v1_1/GOLD_V1_1_CRITERIA.csv",
        "gold_sources":"outputs/gold_v1_1/GOLD_V1_1_SOURCE_STATUS.csv"
    }
    existing={k:v for k,v in outputs.items() if (root/v).exists()}; failed=[k for k,v in steps.items() if v["status"]=="FAILED"]; skipped=[k for k,v in steps.items() if v["status"].startswith("SKIPPED")]; overall="SUCCESS" if not failed and not skipped else "PARTIAL_SUCCESS"
    decision_tracks={
        "actions_final":"V21.0 frozen-weight reference on current 1829 universe + V21.6.3 CDC/post-selection context",
        "actions_challenger":"V21.7 criterion-study hardened challenger; family budgets explicit; derived double-counting removed",
        "etf_full_referential":"268 criteria preserved",
        "etf_mt_reference":"V20.8.1 historical 38-dynamic-PIT sub-block only; not the full ETF/MT model",
        "etf_mt_target_composite":"43 criteria = 38 dynamic PIT + 5 structural, 69/31, research-only until dedicated full-composite PIT/OOS backtest",
        "etf_category_rank_challenger":"rank_cat_1y/3y/5y + PIT 12/24/36m trajectories, zero decision influence until validation",
        "etf_mt_challenger":"V20.8.2 missing-data dynamic shadow",
        "tct":"V24.1.8 baseline + exact V24.1.7 T1/T2 shadow",
        "gold":"V1.1 shadow"
    }
    payload={
        "version":PROCESS_VERSION,"software_version":SOFTWARE_VERSION,"run_id":run_id,"generated_at_utc":datetime.now(timezone.utc).isoformat(),"status":overall,"live_orders_enabled":False,"steps":steps,"persisted_outputs":existing,"decision_tracks":decision_tracks,
        "governance":[
            "Criterion-by-criterion study is an executable pre-scoring gate: aliases, gates/metadata and derived reporting fields cannot silently become alpha weights.",
            "Actions challenger uses explicit family budgets before future intra-family optimisation; final Action decisions remain on frozen V21.0 reference weights until PIT/OOS promotion.",
            "Target-upside and dividend >4% reinforcement is folded into the canonical criterion family rather than double-counted as a separate derived weighted field.",
            "Raw volume/market-cap levels are cross-sectionally percentile-scored before weighting.",
            "ETF full referential contains 268 criteria. The figure 38 denotes only the historical dynamic PIT MT sub-block. The 43-criterion 38+5 composite remains research-only.",
            "Canonical ETF rank_cat_1y/3y/5y values are PIT snapshotted; 12/24/36m trajectory factors remain missing until sufficient history exists and have zero decision influence until validation.",
            "Boursorama raw annual category ranks remain semantically separate from canonical rank_cat percentile fields unless equivalence and denominator are proven.",
            "Missing canonical Action ISINs are materialized as identity-only rows; no ticker/name/market data are invented.",
            "Finnhub earnings-calendar estimates are snapshotted PIT by fiscal period; a first observation never fabricates an EPS revision; exact exchange symbols have priority and ambiguous bare aliases are quarantined.",
            "AMF short data is an open public-disclosure proxy, not true current short interest; ended publications are excluded, latest active publication per holder is retained, and absence is never imputed to zero.",
            "Boursorama and Investing Action sheets are fetched only after preselection; ambiguous instrument matches are rejected and all postselection signals have zero decision influence until PIT/OOS validation.",
            "Every collection publishes retained-value provenance plus missing/partial/available data.",
            "Dynamic available-criterion weights renormalize to 100% while minimum coverage gates remain active.",
            "Stop-loss governance is explicit in Committee outputs: Actions TCT/CT/MT/LT use the existing 6/8/12/18% assumptions and ETF MT preserves the V20.8.1 18% hard stop. Gap/slippage risk means no realized-loss cap is guaranteed.",
            "Virtual BUYs execute no earlier than the next observed run and one consolidated position is allowed per ISIN.",
            "T1/T2 are ACTION TCT only.",
            "No real orders are emitted."
        ]
    }
    path=outdir/f"UNIFIED_SUMMARY_{run_id}.json"; path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); (outdir/"UNIFIED_SUMMARY_LATEST.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2,default=str)); return payload


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); payload=run(Path(args.root)); raise SystemExit(_exit_code(payload))


if __name__=="__main__": main()
