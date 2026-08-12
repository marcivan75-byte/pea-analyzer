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
from v182.audit.canonical_universe import filter_actions

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]


def _read_table(path: Path) -> pd.DataFrame:
    if not path.exists(): return pd.DataFrame()
    errors=[]
    for sep in (";", ",", "\t"):
        try:
            df=pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
            if len(df.columns)>1: return df
        except Exception as exc:
            errors.append(f"{repr(sep)}:{type(exc).__name__}:{str(exc)[:120]}")
    try:
        return pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig", low_memory=False)
    except Exception as exc:
        logger.error("Unable to read %s; attempts=%s; fallback=%s: %s",path,errors,type(exc).__name__,exc)
        raise


def _first_existing(paths:list[Path]) -> Path|None:
    return next((p for p in paths if p.exists()),None)


def _enforce_canonical_actions(actions: pd.DataFrame, root: Path) -> tuple[pd.DataFrame, dict]:
    """Committee-level hard lock on the exact V21.0 1,429-action universe.

    This is intentionally repeated after enrichment. If refresh fails and the
    unified runner falls back to the legacy 1,486-row input, the Committee still
    cannot score out-of-universe rows. Missing canonical ISINs fail closed.
    """
    if actions.empty:
        return actions,{"status":"EMPTY","canonical_rows":0,"excluded_rows":0}
    result=filter_actions(actions,root/"config"/"V21_ACTION_UNIVERSE_ISINS.parts")
    audit={
        "status":"PASS",
        "input_rows":int(len(actions)),
        "canonical_rows":int(len(result.included)),
        "excluded_rows":int(len(result.excluded)),
        "whitelist_count":int(result.whitelist_count),
        "whitelist_sha256":result.whitelist_sha256,
    }
    return result.included.reset_index(drop=True),audit


def _failed_horizon(asset_class: str, horizon: str, version: str, exc: Exception) -> pd.DataFrame:
    return pd.DataFrame([{
        "asset_class":asset_class,"horizon":horizon,"isin":"","name":f"{asset_class} {horizon} MODULE","sector":"TRANSVERSAL",
        "score":None,"coverage_pct":0.0,"status":"FAILED","decision":"FAILED","active_criteria":0,"available_criteria":0,
        "score_source":version,"backtest_attribution":"","notes":f"{type(exc).__name__}: {str(exc)[:240]}"
    }])


def _safe_horizons(frame: pd.DataFrame, registry: dict, asset_class: str, horizons: list[str]) -> tuple[list[pd.DataFrame], list[pd.DataFrame], list[dict]]:
    decisions=[]; coverages=[]; failures=[]
    for horizon in horizons:
        try:
            decisions.append(decisions_from_scores(frame,registry,asset_class,[horizon]))
            coverages.append(criterion_coverage_report(frame,registry,asset_class,[horizon]))
        except Exception as exc:
            logger.exception("Committee %s %s failed without aborting other horizons",asset_class,horizon)
            decisions.append(_failed_horizon(asset_class,horizon,registry.get("version",""),exc))
            failures.append({"asset_class":asset_class,"horizon":horizon,"error":type(exc).__name__,"detail":str(exc)[:240]})
    return decisions,coverages,failures


def run(root: Path=ROOT) -> dict:
    config_dir=root/"config"; outputs=root/"outputs"; outdir=outputs/"committee_master"; outdir.mkdir(parents=True,exist_ok=True)
    master_cfg=json.loads((config_dir/"COMMITTEE_MASTER_V21.json").read_text(encoding="utf-8"))
    actions_reg=load_registry(config_dir/"V21_ACTIONS_CRITERIA_REGISTRY.json")
    etf_reg=load_registry(config_dir/"V20_7_1_ETF_CRITERIA_REGISTRY.json")
    tct_cfg=load_tct_config(config_dir/"TCT_V24_1_7_SHADOW.json")
    actions_path=_first_existing([outputs/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",root/"inputs"/"V18.2_PEA_ACTIONS_MASTER.csv"])
    etf_path=_first_existing([outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",root/"inputs"/"V18.2_PEA_ETF_MASTER.csv"])
    actions=_read_table(actions_path) if actions_path else pd.DataFrame(); etfs=_read_table(etf_path) if etf_path else pd.DataFrame()
    canonical_audit={"status":"NO_INPUT","canonical_rows":0,"excluded_rows":0}
    if not actions.empty:
        actions,canonical_audit=_enforce_canonical_actions(actions,root)
        if canonical_audit.get("excluded_rows",0):
            logger.warning("Committee excluded %s legacy Action rows outside V21 canonical universe",canonical_audit["excluded_rows"])

    parts=[]; coverage_parts=[]; horizon_failures=[]
    if not actions.empty:
        d,c,f=_safe_horizons(actions,actions_reg,"ACTION",["CT","MT","LT","SHORT","TOP_DOWN"])
        parts.extend(d); coverage_parts.extend(c); horizon_failures.extend(f)
        try:
            tct_shadow=tct_shadow_snapshot(actions,tct_cfg)
        except Exception as exc:
            logger.exception("TCT shadow failed")
            tct_shadow=_failed_horizon("ACTION","TCT",tct_cfg.get("version","V24.1.7"),exc)
            horizon_failures.append({"asset_class":"ACTION","horizon":"TCT","error":type(exc).__name__,"detail":str(exc)[:240]})
        tct_shadow.to_csv(outdir/"TCT_SHADOW_V24_1_7.csv",sep=";",index=False,encoding="utf-8-sig")
        parts.append(tct_adapter(tct_shadow))
    else:
        parts.append(_failed_horizon("ACTION","ALL","V21.0",RuntimeError("Actions master input missing")))
        parts.append(tct_adapter())

    if not etfs.empty:
        d,c,f=_safe_horizons(etfs,etf_reg,"ETF",["CT","LT","SHORT","TOP_DOWN"])
        parts.extend(d); coverage_parts.extend(c); horizon_failures.extend(f)
    else:
        parts.append(_failed_horizon("ETF","CT/LT/SHORT/TOP_DOWN","V20.7.1/V20.7",RuntimeError("ETF master input missing")))

    mt_path=_first_existing([outputs/"etf_mt_v2081"/"V20.8.1_ETF_MT_RANKING.csv",outputs/"etf_mt_v2081"/"V20_8_1_ETF_MT_RANKING.csv"])
    try:
        parts.append(overlay_etf_mt(etfs, _read_table(mt_path) if mt_path else None))
    except Exception as exc:
        logger.exception("ETF MT overlay failed")
        parts.append(_failed_horizon("ETF","MT","V20.8.1_DYNAMIC_38_CORE",exc)); horizon_failures.append({"asset_class":"ETF","horizon":"MT","error":type(exc).__name__,"detail":str(exc)[:240]})

    gold_required=master_cfg["assets"]["GOLD"]["required_registry"]
    try:
        parts.append(gold_adapter(root/gold_required))
    except Exception as exc:
        logger.exception("Gold adapter failed")
        parts.append(_failed_horizon("GOLD","TACTICAL/STRATEGIC","GOLD_V1_CONTRACT",exc)); horizon_failures.append({"asset_class":"GOLD","horizon":"ALL","error":type(exc).__name__,"detail":str(exc)[:240]})

    decisions=pd.concat([p for p in parts if p is not None and not p.empty],ignore_index=True,sort=False)
    criterion_coverage=pd.concat([p for p in coverage_parts if p is not None and not p.empty],ignore_index=True,sort=False) if coverage_parts else pd.DataFrame()
    generated=datetime.now(timezone.utc).isoformat(); decisions["generated_at_utc"]=generated; decisions["live_orders_enabled"]=False
    ranks=sector_ranking(decisions)
    decisions.to_csv(outdir/"COMMITTEE_DECISIONS.csv",sep=";",index=False,encoding="utf-8-sig")
    ranks.to_csv(outdir/"SECTOR_RANKING.csv",sep=";",index=False,encoding="utf-8-sig")
    criterion_coverage.to_csv(outdir/"CRITERIA_COVERAGE.csv",sep=";",index=False,encoding="utf-8-sig")
    if horizon_failures:
        pd.DataFrame(horizon_failures).to_csv(outdir/"HORIZON_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    status_counts=decisions.groupby(["asset_class","horizon","status"],dropna=False).size().reset_index(name="count")
    decision_counts=decisions.groupby(["asset_class","horizon","decision"],dropna=False).size().reset_index(name="count")
    missing_by_horizon=[]
    if not criterion_coverage.empty:
        missing_by_horizon=(criterion_coverage[criterion_coverage["criterion_status"]=="MISSING"]
                            .groupby(["asset_class","horizon"])["criterion"].apply(list).reset_index(name="missing_criteria").to_dict("records"))
    tct_status=decisions[decisions["horizon"]=="TCT"]["status"].value_counts().to_dict() if "horizon" in decisions else {}
    summary={
        "version":master_cfg["version"],"status":master_cfg["status"],"generated_at_utc":generated,"live_orders_enabled":False,
        "input_files":{"actions":str(actions_path.relative_to(root)) if actions_path else None,"etf":str(etf_path.relative_to(root)) if etf_path else None,"etf_mt":str(mt_path.relative_to(root)) if mt_path else None},
        "canonical_actions":canonical_audit,
        "registry_integrity":{"actions_criteria_expected":633,"actions_criteria_loaded":int(actions_reg.get("criteria_count",0)),"etf_fields_expected":268,"etf_fields_loaded":int(etf_reg.get("criteria_count",0)),"t1_t2_scope":"ACTION_TCT_ONLY","tct_formula_version":tct_cfg.get("formula_version"),"gold_reference_present":(root/gold_required).exists()},
        "status_counts":status_counts.to_dict("records"),"decision_counts":decision_counts.to_dict("records"),"missing_active_criteria_by_horizon":missing_by_horizon,"tct_shadow_status":tct_status,"horizon_failures":horizon_failures,
        "outputs":{"decisions":"outputs/committee_master/COMMITTEE_DECISIONS.csv","sector_ranking":"outputs/committee_master/SECTOR_RANKING.csv","criteria_coverage":"outputs/committee_master/CRITERIA_COVERAGE.csv","tct_shadow":"outputs/committee_master/TCT_SHADOW_V24_1_7.csv"},
        "notes":["Committee enforces the exact V21 1429-Action whitelist even if enrichment falls back to the legacy input.","No criterion is deleted because its weight is zero.","Each Action/ETF horizon is isolated: one failure no longer aborts the other horizons.","ETF MT historical 90.91% attribution applies only to its 38 PIT dynamic core.","Gold remains blocked until its exact 102-criterion registry is present.","T1/T2 are ACTION TCT timing-only SHADOW overlays and have zero base-score influence."]
    }
    (outdir/"SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2)); return summary


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",default=str(ROOT)); args=parser.parse_args(); run(Path(args.root))


if __name__=="__main__": main()
