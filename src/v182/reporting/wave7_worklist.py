from __future__ import annotations
from pathlib import Path
import pandas as pd

from v182.io.frames import is_missing

EURONEXT_BASE = "https://live.euronext.com"
AMF_GECO_SEARCH = "https://geco.amf-france.org"
CRITICAL_FIELDS = ["pea_confidence", "broker_pea_confirmed", "corporate_status"]
WORKLIST_COLUMNS = ["isin", "name", "field", "reason", "detail", "euronext_link", "amf_geco_search"]


def _euronext_link(row: pd.Series) -> str:
    link = row.get("euronext_link")
    if not is_missing(link):
        return EURONEXT_BASE + str(link)
    mic = row.get("euronext_mic")
    isin = row.get("isin")
    if not is_missing(mic) and not is_missing(isin):
        return f"{EURONEXT_BASE}/en/product/equities/{isin}-{mic}"
    return ""


def _action_metadata(actions_df: pd.DataFrame) -> pd.DataFrame:
    """Build the 1-row-per-ISIN metadata map once instead of per quarantine row."""
    if actions_df.empty or "isin" not in actions_df.columns:
        return pd.DataFrame(columns=["isin", "name", "euronext_link_resolved"])
    rows = actions_df.drop_duplicates("isin", keep="last").copy()
    links = [_euronext_link(row) for _, row in rows.iterrows()]
    return pd.DataFrame(
        {
            "isin": rows["isin"].astype(str).to_numpy(),
            "name": rows["name"].to_numpy() if "name" in rows.columns else "",
            "euronext_link_resolved": links,
        }
    )


def build_worklist(quarantine: list[dict], actions_df: pd.DataFrame) -> pd.DataFrame:
    """Build the official verification list with the same rows, vectorized."""
    metadata = _action_metadata(actions_df)
    parts: list[pd.DataFrame] = []

    if quarantine and not metadata.empty:
        qframe = pd.DataFrame(quarantine)
        if "isin" in qframe.columns and "field" in qframe.columns:
            qframe = qframe.copy()
            qframe["isin"] = qframe["isin"].astype(str)
            merged = qframe.merge(metadata, on="isin", how="inner", sort=False)
            if not merged.empty:
                conflict = pd.DataFrame(
                    {
                        "isin": merged["isin"],
                        "name": merged["name"],
                        "field": merged["field"],
                        "reason": "CONFLIT_EVIDENCE_EGALE",
                        "detail": merged["reason"] if "reason" in merged.columns else "",
                        "euronext_link": merged["euronext_link_resolved"],
                        "amf_geco_search": AMF_GECO_SEARCH,
                    }
                )
                parts.append(conflict)

    if not metadata.empty:
        link_by_isin = metadata.set_index("isin")["euronext_link_resolved"].to_dict()
        name_by_isin = metadata.set_index("isin")["name"].to_dict()
        for field in CRITICAL_FIELDS:
            if field not in actions_df.columns:
                continue
            missing = actions_df[field].apply(is_missing)
            gap_rows = actions_df.loc[missing, ["isin"]].copy()
            if gap_rows.empty:
                continue
            gap_rows["isin"] = gap_rows["isin"].astype(str)
            gap_rows["name"] = gap_rows["isin"].map(name_by_isin).fillna("")
            gap_rows["field"] = field
            gap_rows["reason"] = "GAP_CRITIQUE_PEA"
            gap_rows["detail"] = "Confirmation officielle requise, jamais déduite"
            gap_rows["euronext_link"] = gap_rows["isin"].map(link_by_isin).fillna("")
            gap_rows["amf_geco_search"] = AMF_GECO_SEARCH
            parts.append(gap_rows[WORKLIST_COLUMNS])

    if not parts:
        return pd.DataFrame(columns=WORKLIST_COLUMNS)
    return pd.concat(parts, ignore_index=True, sort=False)[WORKLIST_COLUMNS].drop_duplicates(
        ["isin", "field", "reason"], keep="first"
    )


def write_worklist(quarantine: list[dict], actions_df: pd.DataFrame, output_path: str | Path) -> int:
    worklist = build_worklist(quarantine, actions_df)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    worklist.to_csv(output_path, sep=";", index=False, encoding="utf-8-sig")
    return len(worklist)
