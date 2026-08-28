from __future__ import annotations

import pandas as pd

_STAR_OK = {1.0, 2.0, 3.0, 4.0, 5.0}
_SRI_OK = {1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0}


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame:
        return frame[column]
    return pd.Series(pd.NA, index=frame.index)


def _as_series(value, index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value
    return pd.Series(value, index=index)


def hydrate_etf_sri_from_boursorama(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    if "risk_indicator" not in result:
        result["risk_indicator"] = pd.NA
    asset = result.get("asset_class", pd.Series("", index=result.index)).astype(str).str.upper()
    sri = _as_series(pd.to_numeric(_series(result, "boursorama_etf_sri_risk"), errors="coerce"), result.index)
    status = _series(result, "boursorama_etf_sri_parse_status").astype(str)
    missing = _as_series(pd.to_numeric(result["risk_indicator"], errors="coerce").isna(), result.index)
    eligible = asset.eq("ETF") & missing & status.eq("OK") & sri.isin(list(_SRI_OK))
    result.loc[eligible, "risk_indicator"] = sri.loc[eligible]
    if "risk_indicator_source" not in result:
        result["risk_indicator_source"] = pd.NA
    result.loc[eligible, "risk_indicator_source"] = "BOURSORAMA_ETF_SRI_OK"
    return result


def hydrate_etf_morningstar_from_boursorama(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    if "morningstar_rating" not in result:
        result["morningstar_rating"] = pd.NA
    asset = result.get("asset_class", pd.Series("", index=result.index)).astype(str).str.upper()
    stars = _as_series(pd.to_numeric(_series(result, "boursorama_etf_morningstar_stars"), errors="coerce"), result.index)
    status = _series(result, "boursorama_etf_morningstar_parse_status").astype(str)
    missing = _as_series(pd.to_numeric(result["morningstar_rating"], errors="coerce").isna(), result.index)
    eligible = asset.eq("ETF") & missing & status.eq("OK") & stars.isin(list(_STAR_OK))
    result.loc[eligible, "morningstar_rating"] = stars.loc[eligible]
    if "morningstar_rating_source" not in result:
        result["morningstar_rating_source"] = pd.NA
    result.loc[eligible, "morningstar_rating_source"] = "BOURSORAMA_ETF_STARS_OK"
    return hydrate_etf_sri_from_boursorama(result)


def hydrate_etf_boursorama_quality(frame: pd.DataFrame) -> pd.DataFrame:
    return hydrate_etf_morningstar_from_boursorama(frame)
