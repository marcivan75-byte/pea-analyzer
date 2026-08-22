from __future__ import annotations

import numpy as np
import pandas as pd

from v182.core import data_domain, merge
from v182.io import frames


def _old_frames_missing(value) -> bool:
    if value is None: return True
    try:
        if pd.isna(value): return True
    except (TypeError,ValueError):
        return False
    text=str(value).strip().upper()
    return text in {"","MISSING","UNKNOWN","NON_OBSERVE","NOT_LOADED","NAN","<NA>","N/A","NA","NULL"}


def _old_merge_missing(value) -> bool:
    if value is None: return True
    try:
        marker=pd.isna(value)
    except (TypeError,ValueError):
        marker=False
    if isinstance(marker,bool):
        if marker: return True
    else:
        try:
            if bool(marker): return True
        except (TypeError,ValueError):
            marker=False
    return str(value).strip().upper() in merge.MISSING_TOKENS


def _old_domain_missing(value) -> bool:
    if value is None: return True
    try:
        if pd.isna(value): return True
    except (TypeError,ValueError):
        return False
    return str(value).strip().upper() in data_domain.MISSING_TEXT


def test_fastpaths_are_exactly_equivalent_on_representative_values() -> None:
    values=[
        None,np.nan,pd.NA,0,1,False,True,3.14,
        "","   ","MISSING"," missing ","UNKNOWN","NON_OBSERVE","NOT_LOADED",
        "NAN","<NA>","N/A","NA","NULL","NONE","none","0","text",
        [1,2],[],{"x":1},(1,2),np.array([1,2]),
    ]
    for value in values:
        assert frames.is_missing(value) == _old_frames_missing(value)
        assert merge.is_missing_value(value) == _old_merge_missing(value)
        assert data_domain.is_effectively_missing(value) == _old_domain_missing(value)


def test_strings_bypass_pandas_isna_on_all_hot_paths(monkeypatch) -> None:
    original=pd.isna

    def guarded(value,*args,**kwargs):
        if isinstance(value,str):
            raise AssertionError("string path must not call pandas.isna")
        return original(value,*args,**kwargs)

    monkeypatch.setattr(pd,"isna",guarded)
    assert frames.is_missing("MISSING") is True
    assert frames.is_missing("observed") is False
    assert merge.is_missing_value("NONE") is True
    assert merge.is_missing_value("42") is False
    assert data_domain.is_effectively_missing("N/A") is True
    assert data_domain.is_effectively_missing("12.5") is False


def test_frame_missing_token_set_is_not_rebuilt_per_call() -> None:
    assert frames.MISSING_TOKEN in frames.MISSING_TOKENS
    identity=id(frames.MISSING_TOKENS)
    for _ in range(1000):
        frames.is_missing("observed")
        frames.is_missing("NA")
    assert id(frames.MISSING_TOKENS) == identity
