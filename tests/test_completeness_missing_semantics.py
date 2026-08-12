from __future__ import annotations

import math
import pandas as pd

from v182.audit.completeness import completeness


def test_completeness_does_not_count_pandas_or_textual_missing_values():
    rows=[
        {"a":pd.NA,"b":float("nan"),"c":"N/A","d":"NULL","e":"value"},
        {"a":"<NA>","b":"nan","c":"UNKNOWN","d":"NON_OBSERVE","e":0},
    ]
    out=completeness(rows,["a","b","c","d","e"])
    assert out["possible"]==10
    assert out["observed"]==2
    assert math.isclose(out["coverage_pct"],20.0)


def test_completeness_matches_observed_zero_and_false_as_real_values():
    rows=[{"zero":0,"flag":False,"empty":""}]
    out=completeness(rows,["zero","flag","empty"])
    assert out["observed"]==2
    assert out["possible"]==3
    assert math.isclose(out["coverage_pct"],66.7)
