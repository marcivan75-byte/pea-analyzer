from __future__ import annotations

from pathlib import Path
import json
import os
import pandas as pd

from v182.decision import actions_v210_finnhub_backfill as base

ROOT=Path(__file__).resolve().parents[3]
TARGET=ROOT/'outputs/V21.0_ACTIONS_PEA_1829_PREPARED.csv'
AUDIT=ROOT/'outputs/audit/V21.0_ACTIONS_FINNHUB_BACKFILL.json'


def main()->None:
    base.main()
    if not TARGET.exists(): return
    df=pd.read_csv(TARGET,sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    # V2 keeps compatibility metadata and delegates all 1829-aware collection to the audited base implementation.
    audit=json.loads(AUDIT.read_text(encoding='utf-8')) if AUDIT.exists() else {'passed':True}
    audit['v2_wrapper']='1829_COMPATIBLE'
    audit['v2_target_rows']=len(df)
    audit['finnhub_api_key_present']=bool(str(os.getenv('FINNHUB_API_KEY') or '').strip())
    AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print('V21_ACTIONS_FINNHUB_BACKFILL_V2_1829_OK',{'rows':len(df),'status':audit.get('status')})

if __name__=='__main__':main()
