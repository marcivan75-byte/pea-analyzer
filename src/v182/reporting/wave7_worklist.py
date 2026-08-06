from __future__ import annotations
from pathlib import Path
import pandas as pd

from v182.io.frames import is_missing

EURONEXT_BASE = "https://live.euronext.com"
# Recherche générique GECO (l'AMF n'expose pas de paramètre de requête ISIN
# stable et documenté : on renvoie vers la page de recherche, à interroger
# manuellement avec l'ISIN ou le nom).
AMF_GECO_SEARCH = "https://geco.amf-france.org"

# Champs jugés "critiques" pour l'éligibilité PEA : sans confirmation
# officielle (courtier/AMF/Euronext), ils ne doivent jamais être devinés.
CRITICAL_FIELDS = ["pea_confidence", "broker_pea_confirmed", "corporate_status"]


def _euronext_link(row: pd.Series) -> str:
    link = row.get("euronext_link")
    if not is_missing(link):
        return EURONEXT_BASE + str(link)
    mic = row.get("euronext_mic")
    isin = row.get("isin")
    if not is_missing(mic) and not is_missing(isin):
        return f"{EURONEXT_BASE}/en/product/equities/{isin}-{mic}"
    return ""


def build_worklist(quarantine: list[dict], actions_df: pd.DataFrame) -> pd.DataFrame:
    """Construit la check-list Wave 07 : une ligne par point à vérifier
    manuellement auprès d'une source officielle, avec lien direct quand
    disponible. A remplir dans config/V18.2_MANUAL_OVERRIDES.csv une fois
    vérifié — jamais de valeur appliquée automatiquement ici."""
    rows_by_isin = actions_df.set_index("isin", drop=False)
    entries = []

    for item in quarantine:
        isin = item.get("isin")
        if isin not in rows_by_isin.index:
            continue
        row = rows_by_isin.loc[isin]
        entries.append({
            "isin": isin,
            "name": row.get("name", ""),
            "field": item.get("field"),
            "reason": "CONFLIT_EVIDENCE_EGALE",
            "detail": item.get("reason", ""),
            "euronext_link": _euronext_link(row),
            "amf_geco_search": AMF_GECO_SEARCH,
        })

    for field in CRITICAL_FIELDS:
        if field not in actions_df.columns:
            continue
        gap_rows = actions_df[actions_df[field].apply(is_missing)]
        for _, row in gap_rows.iterrows():
            entries.append({
                "isin": row["isin"],
                "name": row.get("name", ""),
                "field": field,
                "reason": "GAP_CRITIQUE_PEA",
                "detail": "Confirmation officielle requise, jamais déduite",
                "euronext_link": _euronext_link(row),
                "amf_geco_search": AMF_GECO_SEARCH,
            })

    return pd.DataFrame(entries).drop_duplicates(["isin", "field", "reason"])


def write_worklist(quarantine: list[dict], actions_df: pd.DataFrame, output_path: str | Path) -> int:
    worklist = build_worklist(quarantine, actions_df)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    worklist.to_csv(output_path, sep=";", index=False, encoding="utf-8-sig")
    return len(worklist)
