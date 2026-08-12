from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import logging
import pandas as pd

from v182.decision.committee_master import (
    load_registry, decisions_from_scores, overlay_etf_mt, tct_adapter, gold_adapter,
    sector_ranking, criterion_coverage_report,
)
from v182.decision.tct_v24_1_7 import load_tct_config, tct_shadow_snapshot

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]


def _read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    errors=[]
    for sep in (";", ",", "\t"):
        try:
            df=pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
            if len(df.columns)>1:
                return df
        except Exception as exc:
            errors.append(f"{repr(sep)}:{type(exc).__name__}:{str(exc)[:120]}")
    try:
        return pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig", low_memory=False)
    except Exception as exc:
        logger.error("Unable to read %s; attempts=%s; fallback=%s: %s",path,errors,type(exc).__name__,exc)
        raise


def _first_existing(paths:list[Path]) -> Path|None:
    return next((p for p in paths if p.exists()),None)


def run(root: Path=ROOT) -> dict:
    config_dir=root/"config"; outputs=root/"outputs"; outdir=outputs/"committee_master"; outdir.mkdir(parents=True,exist_ok=True)
    master_cfg=json.loads((config_dir/"COMMITTEE_MASTER_V21.json").read_text(encoding="utf-8"))
    actions_reg=load_registry(config_dir/"V21_ACTIONS_CRITERIA_REGISTRY.json")
    etf_reg=load_registry(config_dir/"V20_7_1_ETF_CRITERIA_REGISTRY.json")
    tct_cfg=load_tct_config(config_dir/"TCT_V24_1_7_SHADOW.json")

    actions_path=_first_existing([outputs/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",root/"inputs"/"V18.2_PEA_ACTIONS_MASTER.csv"])
    etf_path=_first_existing([outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",root/"inputs"/"V18.2_PEA_ETF_MASTER.csv"])
    actions=_read_table(actions_path) if actions_path else pd.DataFrame(); etfs=_read_table(etf_path) if etf_path else pd.DataFrame()

    parts=[]; coverage_parts=[]
    if not actions.empty:
        action_horizons=["CT","MT","LT","SHORT","TOP_DOWN"]
        parts.append(decisions_from_scores(actions, actions_reg, "ACTION", action_horizons))
        coverage_parts.append(criterion_coverage_report(actions, actions_reg, "ACTION", action_horizons))
        tct_shadow=tct_shadow_snapshot(actions,tct_cfg)
        tct_shadow.to_csv(outdir/"TCT_SHADOW_V24_1_7.csv",sep=";",index=False,encoding="utf-8-sig")
        parts.append(tct_adapter(tct_shadow))
    else:
        parts.append(pd.DataFrame([{"asset_class":"ACTION","horizon":"ALL","isin":"","name":"ACTIONS MODULE","sector":"TRANSVERSAL","score":None,"coverage_pct":0.0,"status":"BLOCKED_INPUT","decision":"BLOCKED_INPUT","active_criteria":0,"available_criteria":0,"score_source":"V21.0","backtest_attribution":"","notes":"Actions master input missing."}]))
        parts.append(tct_adapter())

    if not etfs.empty:
        etf_horizons=["CT","LT","SHORT","TOP_DOWN"]
        parts.append(decisions_from_scores(etfs, etf_reg, "ETF", etf_horizons))
        coverage_parts.append(criterion_coverage_report(etfs, etf_reg, "ETF", etf_horizons))
    else:
        parts.append(pd.DataFrame([{"asset_class":"ETF","horizon":"CT/LT/SHORT/TOP_DOWN","isin":"","name":"ETF MODULE","sector":"TRANSVERSAL","score":None,"coverage_pct":0.0,"status":"BLOCKED_INPUT","decision":"BLOCKED_INPUT","active_criteria":0,"available_criteria":0,"score_source":"V20.7.1/V20.7","backtest_attribution":"","notes":"ETF master input missing."}]))

    mt_path=_first_existing([outputs/"etf_mt_v2081"/"V20.8.1_ETF_MT_RANKING.csv",outputs/"etf_mt_v2081"/"V20_8_1_ETF_MT_RANKING.csv"])
    parts.append(overlay_etf_mt(etfs, _read_table(mt_path) if mt_path else None))
    gold_required=master_cfg["assets"]["GOLD"]["required_registry"]
    parts.append(gold_adapter(root/gold_required))

    decisions=pd.concat([p for p in parts if p is not None and not p.empty],ignore_index=True,sort=False)
    criterion_coverage=pd.concat([p for p in coverage_parts if p is not None and not p.empty],ignore_index=True,sort=False) if coverage_parts else pd.DataFrame()
    generated=datetime.now(timezone.utc).isoformat()
    decisions["generated_at_utc"]=generated; decisions["live_orders_enabled"]=False
    ranks=sector_ranking(decisions)
    decisions.to_csv(outdir/"COMMITTEE_DECISIONS.csv",sep=";",index=False,encoding="utf-8-sig")
    ranks.to_csv(outdir/"SECTOR_RANKING.csv",sep=";",index=False,encoding="utf-8-sig")
    criterion_coverage.to_csv(outdir/"CRITERIA_COVERAGE.csv",sep=";",index=False,encoding="utf-8-sig")

    status_counts=decisions.groupby(["asset_class","horizon","status"],dropna=False).size().reset_index(name="count")
    decision_counts=decisions.groupby(["asset_class","horizon","decision"],dropna=False).size().reset_index(name="count")
    missing_by_horizon=[]
    if not criterion_coverage.empty:
        missing_by_horizon=(criterion_coverage[criterion_coverage["criterion_status"]=="MISSING"]
                            .groupby(["asset_class","horizon"])["criterion"].apply(list).reset_index(name="missing_criteria").to_dict("records"))

    tct_status=(decisions[decisions["horizon"]=="TCT"]["status"].value_counts().to_dict() if "horizon" in decisions else {})
    summary={
        "version":master_cfg["version"],"status":master_cfg["status"],"generated_at_utc":generated,"live_orders_enabled":False,
        "input_files":{"actions":str(actions_path.relative_to(root)) if actions_path else None,"etf":str(etf_path.relative_to(root)) if etf_path else None,"etf_mt":str(mt_path.relative_to(root)) if mt_path else None},
        "registry_integrity":{"actions_criteria_expected":633,"actions_criteria_loaded":int(actions_reg.get("criteria_count",0)),"etf_fields_expected":268,"etf_fields_loaded":int(etf_reg.get("criteria_count",0)),"t1_t2_scope":"ACTION_TCT_ONLY","tct_formula_version":tct_cfg.get("formula_version"),"gold_reference_present":(root/gold_required).exists()},
        "status_counts":status_counts.to_dict("records"),"decision_counts":decision_counts.to_dict("records"),"missing_active_criteria_by_horizon":missing_by_horizon,"tct_shadow_status":tct_status,
        "outputs":{"decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv","sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv","criteria_coverage":"outputs/committee_master/CRITERIA_COVERAGE.csv","tct_shadow":"outputs/committee_master/TCT_SHADOW_V24_1_7.csv"},
        "notes":[
            "No criterion is deleted because its weight is zero.",
            "Canonical V21 aliases resolve only semantically equivalent V18.2 fields; every resolution is audited in CRITERIA_COVERAGE.csv.",
            "ETF MT historical 90.91% attribution applies only to its 38 PIT dynamic core.",
            "Gold remains blocked until its exact 102-criterion registry is present.",
            "T1/T2 are ACTION TCT timing-only SHADOW overlays, require baseline Top20 first, and have zero base-score influence."
        ]
    }
    (outdir/"SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2)); return summary


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); run(Path(args.root))


if __name__=="__main__":
    main()
