from __future__ import annotations

import pandas as pd

_STAR_OK = {1.0, 2.0, 3.0, 4.0, 5.0}


def hydrate_etf_morningstar_from_boursorama(frame: pd.DataFrame) -> pd.DataFrame:
    """Fill morningstar_rating from a verified Boursorama star count only.

    Does not invent ratings. Unparsed or non-OK rows stay missing and remain fail-closed.
    """
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    if "morningstar_rating" not in result:
        result["morningstar_rating"] = pd.NA
    asset = result.get("asset_class", pd.Series("", index=result.index)).astype(str).str.upper()
    stars = pd.to_numeric(result.get("boursorama_etf_morningstar_stars"), errors="coerce")
    status = result.get("boursorama_etf_morningstar_parse_status", pd.Series("", index=result.index)).astype(str)
    missing = pd.to_numeric(result["morningstar_rating"], errors="coerce").isna()
    eligible = asset.eq("ETF") & missing & status.eq("OK") & stars.isin(list(_STAR_OK))
    result.loc[eligible, "morningstar_rating"] = stars.loc[eligible]
    if "morningstar_rating_source" not in result:
        result["morningstar_rating_source"] = pd.NA
    result.loc[eligible, "morningstar_rating_source"] = "BOURSORAMA_ETF_STARS_OK"
    return result
