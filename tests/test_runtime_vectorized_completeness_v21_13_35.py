from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from v182.audit.completeness import completeness
from v182.reporting import run as reporting_run


def test_dataframe_path_exactly_matches_historical_record_path():
    frame=pd.DataFrame(
        {
            "isin":["FR1","FR2","FR3","FR4"],
            "name":["A","B","C","D"],
            "canonical_seed_status":["WHITELIST_ONLY_MISSING_METADATA"]*4,
            "text":[" value ","N/A",None,"none"],
            "number":[1.25,np.nan,0,-3],
            "flag":[True,False,pd.NA,"UNKNOWN"],
            "object":[[1,2],{"a":1},None,"NULL"],
        }
    )
    fields=["text","number","flag","object","canonical_seed_status","absent_field"]
    historical=completeness(frame.to_dict("records"),fields)
    vectorized=completeness(frame,fields)
    assert vectorized == historical


def test_dataframe_path_preserves_empty_and_all_missing_contracts():
    empty=pd.DataFrame(columns=["a","canonical_seed_status"])
    assert completeness(empty,["a","canonical_seed_status"]) == {
        "observed":0,"possible":0,"coverage_pct":0
    }

    frame=pd.DataFrame({"a":[None,"MISSING"," NON_OBSERVE ",pd.NA]})
    assert completeness(frame,["a","not_present"]) == {
        "observed":0,"possible":8,"coverage_pct":0.0
    }


def test_runner_uses_dataframe_completeness_without_record_materialization():
    source=inspect.getsource(reporting_run._run_pipeline)
    assert 'completeness(actions_df.to_dict("records")' not in source
    assert 'completeness(etf_df.to_dict("records")' not in source
    assert source.count("completeness(actions_df, _fields(actions_df))") == 2
    assert source.count("completeness(etf_df, _fields(etf_df))") == 2
