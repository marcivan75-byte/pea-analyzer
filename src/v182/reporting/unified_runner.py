from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging

from v182.reporting import run as enrichment_run
from v182.reporting import etf_structure_refresh
from v182.reporting import etf_mt_v2081_run
from v182.reporting import committee_master_run

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]


def _safe_step(name: str, func) -> dict:
    try:
        result=func()
        return {"status":"SUCCESS","result":result}
    except Exception as exc:
        logger.exception("Unified runner step %s failed; subsequent independent steps continue",name)
        return {"status":"FAILED","error":type(exc).__name__,"detail":str(exc)[:500]}


def run(root: Path=ROOT) -> dict:
    """Recommended robust entrypoint for V21.1.

    Refreshes the canonical data, enriches ETF structural coverage, runs the
    unchanged V20.8.1 MT engine, then the Committee. Each step is isolated and
    full rankings are persisted. Real orders remain disabled.
    """
    outdir=root/"outputs"/"unified"; outdir.mkdir(parents=True,exist_ok=True)
    run_id=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    steps={}
    steps["refresh"]=_safe_step("refresh", enrichment_run.run)
    # Independent observed structure enrichment. If it fails, the Committee can
    # still run on the refreshed/static structural fields; no neutral imputation.
    steps["etf_structure"]=_safe_step("etf_structure", lambda: etf_structure_refresh.run(root))
    steps["etf_mt"]=_safe_step("etf_mt", etf_mt_v2081_run.run)
    steps["committee"]=_safe_step("committee", lambda: committee_master_run.run(root))

    committee_outputs={
        "decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv",
        "sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv",
        "criteria_coverage":"outputs/committee_master/CRITERIA_COVERAGE.csv",
        "tct_shadow":"outputs/committee_master/TCT_SHADOW_V24_1_7.csv",
    }
    etf_mt_outputs={
        "ranking":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_RANKING.csv",
        "summary":"outputs/etf_mt_v2081/V20.8.1_ETF_MT_SUMMARY.json",
    }
    structural_outputs={
        "audit":"outputs/audit/V21_ETF_FUND_STRUCTURE.json",
        "failures":"outputs/gaps/V21_ETF_FUND_STRUCTURE_FAILURES.csv",
    }
    existing={k:v for k,v in {
        **committee_outputs,
        **{f"etf_mt_{k}":v for k,v in etf_mt_outputs.items()},
        **{f"etf_structure_{k}":v for k,v in structural_outputs.items()},
    }.items() if (root/v).exists()}

    overall="SUCCESS" if all(step["status"]=="SUCCESS" for step in steps.values()) else "PARTIAL_SUCCESS"
    payload={
        "version":"UNIFIED_V21_1",
        "run_id":run_id,
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "status":overall,
        "live_orders_enabled":False,
        "steps":steps,
        "persisted_outputs":existing,
        "shadow_modules":{
            "tct":"V24.1.7_T1_T2_V2_SHADOW via Committee output; zero base-score influence",
            "ml_etf":"SKIPPED_MODULE_NOT_PRESENT_IN_CURRENT_REPOSITORY"
        },
        "governance":[
            "One module failure must not discard independent Committee results.",
            "Full rankings are persisted as CSV and never thrown away after aggregation.",
            "ETF structural enrichment is observed-only; missing holdings/sector data are not imputed.",
            "T1/T2 are ACTION TCT only.",
            "ETF MT 90.91% historical OOS attribution belongs only to the 38 PIT dynamic core.",
            "No real orders are emitted."
        ]
    }
    path=outdir/f"UNIFIED_SUMMARY_{run_id}.json"
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    (outdir/"UNIFIED_SUMMARY_LATEST.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2,default=str))
    return payload


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); run(Path(args.root))


if __name__=="__main__": main()
