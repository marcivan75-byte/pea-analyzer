from pathlib import Path
import pandas as pd

from v182.audit.canonical_universe import load_compressed_isins, filter_actions, EXPECTED_ACTIONS, EXPECTED_SHA256

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
