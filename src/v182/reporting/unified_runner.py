from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import os

from v182.reporting import run as enrichment_run
from v182.reporting import etf_structure_refresh
from v182.reporting import etf_mt_v2081_run
from v182.reporting import committee_master_gold_v1_1
from v182.reporting import committee_performance_v21_4
from v182.decision import gold_v1_1

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]


def _safe_step(name: str, func) -> dict:
    try:
        result=func()
        if hasattr(result,"__dict__"): result=dict(result.__dict__)
        return {"status":"SUCCESS","result":result}
    except Exception as exc:
        logger.exception("Unified runner step %s failed; independent steps continue",name)
        return {"status":"FAILED","error":type(exc).__name__,"detail":str(exc)[:500]}


def _skip_dependency(reason:str)->dict:
    return {"status":"SKIPPED_DEPENDENCY","reason":reason}


def run(root: Path=ROOT) -> dict:
    """V21.4 hardened entrypoint with explicit dependency semantics.

    Scoring modules may continue independently after a source failure, but the
    virtual performance book never consumes stale/fallback Committee decisions:
    it requires both a successful current refresh and a successful Committee run.
    """
    outdir=root/"outputs"/"unified"; outdir.mkdir(parents=True,exist_ok=True); started=datetime.now(timezone.utc); run_id=started.strftime("%Y-%m-%dT%H-%M-%SZ")
    steps={}
    steps["refresh"]=_safe_step("refresh", enrichment_run.run)
    steps["etf_structure"]=_safe_step("etf_structure", lambda: etf_structure_refresh.run(root))
    steps["etf_mt"]=_safe_step("etf_mt", etf_mt_v2081_run.run)
    steps["gold"]=_safe_step("gold", lambda: gold_v1_1.run(root,os.environ.get("FRED_API_KEY")))
    steps["committee"]=_safe_step("committee", lambda: committee_master_gold_v1_1.run(root))
    if steps["refresh"]["status"]=="SUCCESS" and steps["committee"]["status"]=="SUCCESS":
        steps["performance"]=_safe_step("performance", lambda: committee_performance_v21_4.run(root))
    else:
        steps["performance"]=_skip_dependency("Requires SUCCESS refresh and SUCCESS current Committee; stale decisions are forbidden for virtual transactions.")

    outputs={
        "decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv","sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv","criteria_coverage":"outputs/committee_master/CRITERIA_COVERAGE.csv","effective_weights":"outputs/committee_master/EFFECTIVE_WEIGHTS_100.xlsx","tct_baseline":"outputs/committee_master/TCT_BASELINE_V24_1_8.csv","tct_shadow":"outputs/committee_master/TCT_SHADOW_V24_1_7.csv","collection_audit_latest":"outputs/data_audit/COLLECTION_DATA_AVAILABILITY_LATEST.xlsx","provenance":"outputs/audit/OBSERVATION_PROVENANCE.csv","sector_rotation":"outputs/V21_3_SECTOR_ROTATION.csv","performance_workbook":"outputs/performance/COMMITTEE_BUY_PERFORMANCE.xlsx","etf_mt_ranking":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_RANKING.csv","etf_mt_summary":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_SUMMARY.json","etf_structure_audit":"outputs/audit/V21_ETF_FUND_STRUCTURE.json","gold_decision":"outputs/gold_v1_1/GOLD_V1_1_DECISION.json","gold_criteria":"outputs/gold_v1_1/GOLD_V1_1_CRITERIA.csv","gold_sources":"outputs/gold_v1_1/GOLD_V1_1_SOURCE_STATUS.csv","gold_history":"state/GOLD_V1_1_SHADOW_HISTORY.csv",
    }
    existing={k:v for k,v in outputs.items() if (root/v).exists()}; failed=[k for k,v in steps.items() if v["status"]=="FAILED"]; skipped=[k for k,v in steps.items() if v["status"].startswith("SKIPPED")]
    overall="SUCCESS" if not failed and not skipped else "PARTIAL_SUCCESS"
    payload={
        "version":"UNIFIED_V21_4_AUDIT_HARDENED","run_id":run_id,"generated_at_utc":datetime.now(timezone.utc).isoformat(),"status":overall,"live_orders_enabled":False,"steps":steps,"persisted_outputs":existing,
        "shadow_modules":{
            "actions":"V21.4 reference decision + unvalidated rotation/catch-up/52w/Morningstar/total-return challengers; positive 52w overlay cannot create BUY",
            "tct":"V24.1.8 dynamic active-pillar baseline + exact V24.1.7 T1/T2; T1/T2 zero base-score influence",
            "etf_mt_challenger":"V20.8.2 dynamic missing-data challenger; no historical attribution",
            "gold":"GOLD_V1.1 autonomous SHADOW_RESEARCH_ONLY; outside PEA",
            "performance":"Bias-safe virtual book: next-run entry, one ISIN position, versioned cohorts"
        },
        "governance":[
            "Every collection publishes missing/partial/available data and actual observed source provenance when available.",
            "Per-field provenance controls source evidence/freshness merge decisions.",
            "Available active criteria are renormalized to 100% per instrument while coverage gates remain active.",
            "Unvalidated positive Action overlays cannot create a BUY; negative risk overlays may downgrade.",
            "Virtual BUYs execute no earlier than the next observed run after signal date; one consolidated position per ISIN.",
            "Virtual performance is version-cohorted and never runs on failed/stale Committee refreshes.",
            "T1/T2 are ACTION TCT only.",
            "ETF MT 90.91% historical OOS attribution belongs only to exact V20.8.1 38-PIT core.",
            "Gold remains autonomous outside PEA with no neutral missing-data imputation.",
            "No real orders are emitted."
        ]
    }
    path=outdir/f"UNIFIED_SUMMARY_{run_id}.json"; path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); (outdir/"UNIFIED_SUMMARY_LATEST.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2,default=str)); return payload


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); run(Path(args.root))


if __name__=="__main__": main()
