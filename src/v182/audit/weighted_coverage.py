from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

from v182.decision.committee_master import active_criteria, resolve_field


def weighted_coverage(frame: pd.DataFrame, registry: dict, horizons: tuple[str, ...], asset_class: str) -> tuple[pd.DataFrame, list[dict]]:
    rows=[]
    summaries=[]
    n=max(len(frame),1)
    for horizon in horizons:
        criteria=active_criteria(registry,horizon)
        total=sum(weight for _,weight,_ in criteria)
        observed_weight=0.0
        for criterion,weight,direction in criteria:
            values,resolution=resolve_field(frame,criterion)
            available=int(values.notna().sum()) if values is not None else 0
            availability=available/n
            observed_weight += weight*availability
            rows.append({
                "asset_class":asset_class,"horizon":horizon,"criterion":criterion,
                "weight":weight,"direction":direction,"resolution":resolution,
                "available_rows":available,"universe_rows":len(frame),
                "availability_pct":round(availability*100,2),
                "weighted_coverage_points":round(weight*availability*100,4),
            })
        summaries.append({
            "asset_class":asset_class,"horizon":horizon,"criteria":len(criteria),
            "total_weight":round(total,12),
            "weighted_input_coverage_pct":round(observed_weight/total*100,2) if total else 0.0,
        })
    return pd.DataFrame(rows),summaries


def run(root: Path, actions_path: Path, etf_path: Path) -> dict:
    actions=pd.read_csv(actions_path,sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    etf=pd.read_csv(etf_path,sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    action_registry=json.loads((root/"config/V21_ACTIONS_REFERENCE_V21_0.json").read_text(encoding="utf-8"))
    etf_registry=json.loads((root/"config/V20_7_1_ETF_CRITERIA_REGISTRY.json").read_text(encoding="utf-8"))
    adetail,asummary=weighted_coverage(actions,action_registry,("CT","MT","LT","SHORT","TOP_DOWN"),"ACTION")
    edetail,esummary=weighted_coverage(etf,etf_registry,("CT","LT","SHORT","TOP_DOWN","MT_BASELINE_V20_7"),"ETF")
    detail=pd.concat([adetail,edetail],ignore_index=True)
    outdir=root/"outputs/audit"; outdir.mkdir(parents=True,exist_ok=True)
    detail.to_csv(outdir/"WEIGHTED_INPUT_COVERAGE.csv",sep=";",encoding="utf-8-sig",index=False)
    payload={
        "actions_rows":len(actions),"etf_rows":len(etf),"summaries":asummary+esummary,
        "rule":"Coverage is weighted by active input importance. Missing values remain missing; this audit does not authorize historical imputation.",
    }
    (outdir/"WEIGHTED_INPUT_COVERAGE.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return payload


if __name__=="__main__":
    root=Path(__file__).resolve().parents[3]
    run(root,root/"outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",root/"outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv")
