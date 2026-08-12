from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

from v182.audit.canonical_universe import EXPECTED_ACTIONS, filter_actions

IDENTITY_ONLY_STATUS="WHITELIST_ONLY_MISSING_METADATA"


def build_worklist(actions:pd.DataFrame)->pd.DataFrame:
    if "canonical_seed_status" not in actions.columns:
        return pd.DataFrame(columns=["isin","canonical_seed_status","hydration_state","required_action","scoring_eligible","source_provenance_required"])
    missing=actions[actions["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS)].copy()
    preferred=[c for c in ("isin","name","yahoo_ticker","exchange","country","currency") if c in missing.columns]
    out=missing[preferred].copy()
    out["canonical_seed_status"]=IDENTITY_ONLY_STATUS
    out["hydration_state"]="MISSING_IDENTITY_METADATA"
    out["required_action"]="VALIDATE_ISIN_NAME_TICKER_EXCHANGE_WITH_ATTRIBUTED_SOURCE"
    out["scoring_eligible"]=False
    out["source_provenance_required"]=True
    return out.sort_values("isin").reset_index(drop=True)


def run(root:Path)->dict:
    master_path=root/"inputs"/"V18.2_PEA_ACTIONS_MASTER.csv"
    whitelist=root/"config"/"V21_3_ACTION_UNIVERSE_1829_ISINS.parts"
    if not master_path.exists(): raise FileNotFoundError("ACTION_MASTER_NOT_FOUND")
    legacy=pd.read_csv(master_path,sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    canonical=filter_actions(legacy,whitelist)
    worklist=build_worklist(canonical.included)
    outdir=root/"outputs"/"gaps"; outdir.mkdir(parents=True,exist_ok=True)
    csv_path=outdir/"ACTION_IDENTITY_HYDRATION_WORKLIST.csv"
    json_path=outdir/"ACTION_IDENTITY_HYDRATION_SUMMARY.json"
    worklist.to_csv(csv_path,sep=";",encoding="utf-8-sig",index=False)
    payload={
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "canonical_actions":int(len(canonical.included)),
        "expected_actions":EXPECTED_ACTIONS,
        "legacy_input_rows":int(len(legacy)),
        "excluded_legacy_rows":int(len(canonical.excluded)),
        "identity_only_rows":int(len(worklist)),
        "market_data_eligible_rows":int(len(canonical.included)-len(worklist)),
        "scoring_policy":"IDENTITY_ONLY_ROWS_REMAIN_BLOCK_DATA_UNTIL_EXPLICIT_ATTRIBUTED_HYDRATION",
        "no_identity_invention":True,
        "worklist":str(csv_path.relative_to(root)),
    }
    json_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return payload


if __name__=="__main__":
    run(Path(__file__).resolve().parents[3])
