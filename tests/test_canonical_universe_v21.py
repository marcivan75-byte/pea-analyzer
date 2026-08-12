from pathlib import Path
import pandas as pd

from v182.audit.canonical_universe import load_compressed_isins, filter_actions, EXPECTED_ACTIONS, EXPECTED_SHA256
from v182.reporting.committee_master_run import _enforce_canonical_actions

ROOT=Path(__file__).resolve().parents[1]
WHITELIST=ROOT/"config"/"V21_ACTION_UNIVERSE_ISINS.parts"


def test_exact_v21_whitelist_count_and_digest():
    isins=load_compressed_isins(WHITELIST)
    assert len(isins)==EXPECTED_ACTIONS==1429
    assert len(set(isins))==1429


def test_filter_is_by_isin_not_by_row_count():
    isins=load_compressed_isins(WHITELIST)
    frame=pd.DataFrame({"isin":isins+["FR_OUTSIDE_V21"],"name":["x"]*(len(isins)+1)})
    result=filter_actions(frame,WHITELIST)
    assert len(result.included)==1429
    assert list(result.excluded["isin"])==["FR_OUTSIDE_V21"]
    assert result.whitelist_sha256==EXPECTED_SHA256


def test_committee_fallback_cannot_score_legacy_outside_v21_universe():
    isins=load_compressed_isins(WHITELIST)
    legacy=pd.DataFrame({"isin":isins+["FR_OUTSIDE_V21"],"name":["x"]*(len(isins)+1)})
    canonical,audit=_enforce_canonical_actions(legacy,ROOT)
    assert len(canonical)==1429
    assert "FR_OUTSIDE_V21" not in set(canonical["isin"])
    assert audit["input_rows"]==1430
    assert audit["canonical_rows"]==1429
    assert audit["excluded_rows"]==1
