from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import os

from v182.audit import criteria_study_governance
from v182.reporting import run as enrichment_run
from v182.reporting import cdc_refresh, etf_structure_refresh, etf_mt_v2081_run, committee_master_v21_4
from v182.decision import gold_v1_1
from v182.risk import beta_correlation_engine, entry_exit_governance_v21_8

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]
SOFTWARE_VERSION="21.8"
PROCESS_VERSION="UNIFIED_V21_8_ENTRY_EXIT_CHALLENGER_RISK_V1_SHADOW"


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
    """V21.8 challenger runtime with beta/correlation context kept shadow-only.

    Flow: scoring/committee -> beta context shadow -> entry/exit governance.
    Legacy fixed-stop execution and virtual performance execution remain disabled
    until a separately validated V21.8 risk-sizing policy exists.
    """
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
        steps["beta_correlation_risk"]=_safe_step("beta_correlation_risk",lambda:beta_correlation_engine.run(root))
        steps["entry_exit_governance"]=_safe_step("entry_exit_governance",lambda:entry_exit_governance_v21_8.run(root))
    else:
        steps["beta_correlation_risk"]=_skip_dependency("Requires SUCCESS current Committee decisions before shadow risk context can be attached.")
        steps["entry_exit_governance"]=_skip_dependency("Requires SUCCESS current Committee decisions before V21.8 entry/exit governance.")

    steps["performance"]={
        "status":"SKIPPED_GOVERNANCE",
        "reason":"V21.8 disables legacy fixed-stop risk sizing and virtual execution until a separately validated sizing policy exists."
    }

    outputs={
        "decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv",
        "entry_exit_challenger":"outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv",
        "entry_exit_audit":"outputs/audit/V21_8_ENTRY_EXIT_GOVERNANCE.json",
        "beta_correlation_risk_rows":"outputs/risk/BETA_CORRELATION_RISK_ROWS.csv",
        "portfolio_risk_summary":"outputs/risk/PORTFOLIO_RISK_SUMMARY.json",
        "sector_beta_risk_overlay":"outputs/risk/SECTOR_BETA_RISK_OVERLAY.csv",
        "beta_correlation_risk_audit":"outputs/audit/BETA_CORRELATION_RISK_ENGINE.json",
        "postselection_market_sheets":"outputs/committee_master/POSTSELECTION_MARKET_SHEETS.csv",
        "postselection_failures":"outputs/gaps/V21_6_3_POSTSELECTION_MARKET_SHEETS_FAILURES.csv",
        "sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv",
        "sector_ranking_challenger":"outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_7.csv",
        "action_reference_vs_challenger":"outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_7.csv",
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
        "etf_mt_ranking":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_RANKING.csv",
        "etf_mt_summary":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_SUMMARY.json",
        "gold_decision":"outputs/gold_v1_1/GOLD_V1_1_DECISION.json",
        "gold_criteria":"outputs/gold_v1_1/GOLD_V1_1_CRITERIA.csv",
        "gold_sources":"outputs/gold_v1_1/GOLD_V1_1_SOURCE_STATUS.csv"
    }
    existing={k:v for k,v in outputs.items() if (root/v).exists()}; failed=[k for k,v in steps.items() if v["status"]=="FAILED"]; skipped=[k for k,v in steps.items() if v["status"].startswith("SKIPPED_DEPENDENCY")]; overall="SUCCESS" if not failed and not skipped else "PARTIAL_SUCCESS"
    decision_tracks={
        "actions_final":"V21.0 frozen-weight reference; V21.8 changes entry/position governance only, not scoring weights",
        "actions_entry_exit_challenger":"TCT exact T2 confirmation required for challenger entry; CT/MT use HOLD/PROTECT/EXIT with temporal confirmation",
        "etf_full_referential":"268 criteria preserved",
        "etf_mt_reference":"V20.8.1 historical 38-dynamic-PIT sub-block only; not the full ETF/MT model",
        "etf_mt_entry_exit_challenger":"No fixed take-profit; no legacy fixed stop; profit giveback is context only",
        "tct":"V24.1.8 baseline + exact V24.1.7 T1/T2 shadow; T1/T2 ACTION TCT only",
        "gold":"V1.1 shadow",
        "beta_correlation_risk":"RISK_V1.1 robust context-only shadow; validated sizing variants rejected, zero score/decision/sizing/exit influence"
    }
    payload={
        "version":PROCESS_VERSION,"software_version":SOFTWARE_VERSION,"run_id":run_id,"generated_at_utc":datetime.now(timezone.utc).isoformat(),"status":overall,"live_orders_enabled":False,"steps":steps,"persisted_outputs":existing,"decision_tracks":decision_tracks,
        "governance":[
            "Selection is not entry. V21.8 attaches an explicit entry gate to current Committee decisions.",
            "TCT challenger entry requires exact T2 confirmation; T1 alone never opens a V21.8 challenger position.",
            "CT/MT positions use HOLD -> PROTECT -> EXIT. A first multifactor deterioration produces PROTECT; EXIT requires temporal confirmation after PROTECT or an explicit confirmed deterioration flag.",
            "Profit level and profit giveback are context only and never create a standalone sell signal.",
            "No fixed take-profit is operational in V21.8.",
            "Legacy fixed-stop assumptions, including the historical ETF -18% protocol, are not operational V21.8 rules.",
            "No new hard stop is promoted. The 7% figure remains a research risk ceiling, not a blind liquidation rule.",
            "Emergency exit requires an explicit emergency-risk flag; gaps/slippage mean no realized-loss cap can be guaranteed.",
            "Beta/correlation RISK_V1.1 remains context-only. Its validated sizing variants were rejected and it cannot mutate score, entry, exit, sizing or stop policy.",
            "Risk stress scenarios are diagnostics, not forecasts or guaranteed caps of total portfolio loss.",
            "ETF full referential contains 268 criteria. The figure 38 denotes only the historical dynamic PIT MT sub-block and 90.91% attribution remains confined to that sub-block.",
            "T1/T2 are ACTION TCT only.",
            "Legacy virtual execution is disabled in this challenger until risk sizing no longer depends on invalidated fixed-stop assumptions.",
            "No real orders are emitted."
        ]
    }
    path=outdir/f"UNIFIED_SUMMARY_{run_id}.json"; path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); (outdir/"UNIFIED_SUMMARY_LATEST.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2,default=str)); return payload


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); payload=run(Path(args.root)); raise SystemExit(_exit_code(payload))


if __name__=="__main__": main()
