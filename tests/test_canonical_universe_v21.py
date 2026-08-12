from pathlib import Path
import pandas as pd

from v182.audit.canonical_universe import load_compressed_isins, filter_actions, EXPECTED_ACTIONS, EXPECTED_SHA256
from v182.reporting.committee_master_run import _enforce_canonical_actions

ROOT=Path(__file__).resolve().parents[1]
WHITELIST=ROOT/"config"/"V21_3_ACTION_UNIVERSE_1829_ISINS.parts"


def test_exact_v21_3_whitelist_count_and_digest():
    isins=load_compressed_isins(WHITELIST)
    assert len(isins)==EXPECTED_ACTIONS==1829
    assert len(set(isins))==1829
    assert EXPECTED_SHA256=="1e95d51d5a8fa3e616e97ec3fec0a033b29e841d0971ff5a644efa4f5049c085"


def test_filter_is_by_isin_not_by_row_count():
    isins=load_compressed_isins(WHITELIST)
    frame=pd.DataFrame({"isin":isins+["FR_OUTSIDE_V21_3"],"name":["x"]*(len(isins)+1)})
    result=filter_actions(frame,WHITELIST)
    assert len(result.included)==1829
    assert list(result.excluded["isin"])==["FR_OUTSIDE_V21_3"]
    assert result.whitelist_sha256==EXPECTED_SHA256


def test_committee_fallback_cannot_score_outside_v21_3_universe():
    isins=load_compressed_isins(WHITELIST)
    legacy=pd.DataFrame({"isin":isins+["FR_OUTSIDE_V21_3"],"name":["x"]*(len(isins)+1)})
    canonical,audit=_enforce_canonical_actions(legacy,ROOT)
    assert len(canonical)==1829
    assert "FR_OUTSIDE_V21_3" not in set(canonical["isin"])
    assert audit["input_rows"]==1830
    assert audit["canonical_rows"]==1829
    assert audit["excluded_rows"]==1
