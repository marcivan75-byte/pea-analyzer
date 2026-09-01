from __future__ import annotations

import pandas as pd

import scripts.v22_1_tabport_final_holdout as target

_original_merge_asof = pd.merge_asof


def _normalized_merge_asof(left, right, *args, **kwargs):
    on = kwargs.get("on")
    if on == "date":
        left = left.copy()
        right = right.copy()
        left["date"] = pd.to_datetime(left["date"], errors="coerce").dt.normalize().astype("datetime64[ns]")
        right["date"] = pd.to_datetime(right["date"], errors="coerce").dt.normalize().astype("datetime64[ns]")
    return _original_merge_asof(left, right, *args, **kwargs)


pd.merge_asof = _normalized_merge_asof
raise SystemExit(target.main())
