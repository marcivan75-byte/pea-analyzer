from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import os

from v182.reporting import run as enrichment_run
from v182.reporting import cdc_refresh, etf_structure_refresh, etf_mt_v2081_run, committee_master_v21_4, committee_performance_v21_4
from v182.decision import gold_v1_1

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]
SOFTWARE_VERSION="21.6.3"
PROCESS_VERSION="UNIFIED_V21_6_3_CDC_POSTSELECTION"


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
    """Scheduled/CLI runs must fail visibly when the full process is not complete."""
    return 0 if payload.get("status")=="SUCCESS" else 1


def run(root:Path=ROOT)->dict:
    """V21.6.3 runtime with CDC enrichment and post-selection market-sheet confirmation."""
    outdir=root/"outputs"/"unified"; outdir.mkdir(parents=True,exist_ok=True); run_id=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    steps={}
    steps["refresh"]=_safe_step("refresh",enrichment_run.run)
    if steps["refresh"]["status"]=="SUCCESS":
        steps["cdc"]=_safe_step("cdc",lambda:cdc_refresh.run(root))
        steps["etf_structure"]=_safe_step("etf_structure",lambda:etf_structure_refresh.run(root))
    else:
        steps["cdc"]=_skip_dependency("Requires SUCCESS current refresh; CDC observations may not be merged into a stale Action master.")
        steps["etf_structure"]=_skip_dependency("Requires SUCCESS current refresh; Boursorama ETF ranks may not be merged into a stale ETF master.")
    steps["etf_mt"]=_safe_step("etf_mt",etf_mt_v2081_run.run)
    steps["gold"]=_safe_step("gold",lambda:gold_v1_1.run(root,os.environ.get("FRED_API_KEY")))
    committee_dependencies=("refresh","cdc","etf_structure")
    if all(steps[name]["status"]=="SUCCESS" for name in committee_dependencies):
        steps["committee"]=_safe_step("committee",lambda:committee_master_v21_4.run(root))
    else:
        steps["committee"]=_skip_dependency("Requires SUCCESS refresh + CDC + ETF structure/rank stages; stale or partially enriched masters are forbidden for Committee decisions.")
    if steps["committee"]["status"]=="SUCCESS":
        steps["performance"]=_safe_step("performance",lambda:committee_performance_v21_4.run(root))
    else:
        steps["performance"]=_skip_dependency("Requires SUCCESS current Committee; stale decisions are forbidden for virtual transactions.")
    outputs={
        "decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv","postselection_market_sheets":"outputs/committee_master/POSTSELECTION_MARKET_SHEETS.csv","postselection_failures":"outputs/gaps/V21_6_3_POSTSELECTION_MARKET_SHEETS_FAILURES.csv","sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv","sector_ranking_challenger":"outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_4.csv","action_reference_vs_challenger":"outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv","criteria_coverage":"outputs/committee_master/CRITERIA_COVERAGE.csv","effective_weights":"outputs/committee_master/EFFECTIVE_WEIGHTS_100.xlsx","tct_baseline":"outputs/committee_master/TCT_BASELINE_V24_1_8.csv","tct_shadow":"outputs/committee_master/TCT_SHADOW_V24_1_7.csv","collection_audit_latest":"outputs/data_audit/COLLECTION_DATA_AVAILABILITY_LATEST.xlsx","cdc_audit":"outputs/audit/V21_6_3_CDC_REFRESH.json","eps_estimate_history":"state/finnhub/EPS_ESTIMATE_HISTORY.csv","boursorama_etf_rank_history":"state/boursorama/ETF_CATEGORY_RANK_HISTORY.csv","provenance":"state/provenance/OBSERVATION_PROVENANCE.csv","sector_rotation":"outputs/V21_3_SECTOR_ROTATION.csv","performance_workbook":"outputs/performance/COMMITTEE_BUY_PERFORMANCE.xlsx","etf_mt_ranking":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_RANKING.csv","etf_mt_summary":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_SUMMARY.json","gold_decision":"outputs/gold_v1_1/GOLD_V1_1_DECISION.json","gold_criteria":"outputs/gold_v1_1/GOLD_V1_1_CRITERIA.csv","gold_sources":"outputs/gold_v1_1/GOLD_V1_1_SOURCE_STATUS.csv"
    }
    existing={k:v for k,v in outputs.items() if (root/v).exists()}; failed=[k for k,v in steps.items() if v["status"]=="FAILED"]; skipped=[k for k,v in steps.items() if v["status"].startswith("SKIPPED")]; overall="SUCCESS" if not failed and not skipped else "PARTIAL_SUCCESS"
    decision_tracks={"actions_final":"V21.0 frozen-weight reference on current 1829 universe + V21.6.3 post-selection confirmation columns","actions_challenger":"V21.4 enriched shadow challenger","etf_mt_reference":"V20.8.1 exact 38-PIT core; Boursorama category rank shadow only","etf_mt_challenger":"V20.8.2 missing-data dynamic shadow","tct":"V24.1.8 baseline + exact V24.1.7 T1/T2 shadow","gold":"V1.1 shadow"}
    payload={
        "version":PROCESS_VERSION,"software_version":SOFTWARE_VERSION,"run_id":run_id,"generated_at_utc":datetime.now(timezone.utc).isoformat(),"status":overall,"live_orders_enabled":False,"steps":steps,"persisted_outputs":existing,"decision_tracks":decision_tracks,
        "governance":[
            "Runtime/software version is distinct from model versions; decision_tracks is the authoritative model-version map.",
            "Missing canonical Action ISINs are materialized as identity-only rows; no ticker/name/market data are invented.",
            "Finnhub earnings-calendar estimates are snapshotted PIT by fiscal period; a first observation never fabricates an EPS revision.",
            "AMF net-short absence is missing data, never an imputed zero position.",
            "Boursorama ETF category rank and its run-to-run evolution are shadow confirmation only and do not modify the exact V20.8.1 MT score.",
            "Boursorama and Investing Action sheets are fetched only after preselection; weekly/monthly Investing signals are confirmation columns with zero decision influence until PIT/OOS validation.",
            "New/unvalidated Action factors, including 52-week overlays, remain challenger-only until dedicated PIT/OOS validation.",
            "Every collection publishes retained-value provenance plus missing/partial/available data.",
            "Per-field retained provenance governs evidence/freshness merge decisions and persists across runs.",
            "Dynamic available-criterion weights renormalize to 100% while minimum coverage gates remain active.",
            "Virtual BUYs execute no earlier than the next observed run and one consolidated position is allowed per ISIN.",
            "Virtual performance is model-version cohorted and never consumes stale Committee decisions.",
            "A partial unified run returns a non-zero CLI exit code so GitHub cannot display false green success.",
            "T1/T2 are ACTION TCT only.",
            "ETF MT 90.91% historical OOS attribution belongs only to exact V20.8.1 38-PIT core.",
            "No real orders are emitted."
        ]
    }
    path=outdir/f"UNIFIED_SUMMARY_{run_id}.json"; path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); (outdir/"UNIFIED_SUMMARY_LATEST.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2,default=str)); return payload


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); payload=run(Path(args.root)); raise SystemExit(_exit_code(payload))


if __name__=="__main__": main()
