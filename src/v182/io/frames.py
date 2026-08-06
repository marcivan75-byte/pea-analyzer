from __future__ import annotations
from pathlib import Path
import pandas as pd

MISSING_TOKEN = "NON_OBSERVE"


def load_master(path: str | Path) -> pd.DataFrame:
    """Charge un référentiel maître (Actions ou ETF). Les CSV du projet sont
    séparés par ';', encodés en UTF-8 avec BOM, valeurs vides = NaN."""
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, keep_default_na=True)


def save_master(frame: pd.DataFrame, path: str | Path) -> None:
    """Réécrit un référentiel maître dans le même format que l'entrée
    (';' + BOM), pour rester compatible avec le reste de la chaîne V18.2."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, sep=";", encoding="utf-8-sig", index=False)


def is_missing(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip().upper()
    return text in {"", "MISSING", "UNKNOWN", MISSING_TOKEN, "NOT_LOADED", "NAN", "<NA>", "N/A", "NA", "NULL"}


def apply_observations(frame: pd.DataFrame, observations: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """Applique une liste d'observations {isin, field, value, ...} sur le
    DataFrame en respectant la politique 'never_replace_observed_with_missing'.
    Retourne le frame mis à jour et la liste des conflits mis en quarantaine.
    """
    from v182.core.merge import decide

    frame = frame.set_index("isin", drop=False)
    quarantined: list[dict] = []

    for obs in observations:
        isin = obs.get("isin")
        field = obs.get("field")
        if isin is None or field is None or isin not in frame.index:
            continue
        if field not in frame.columns:
            frame[field] = pd.NA

        current_value = frame.at[isin, field]
        existing = None if is_missing(current_value) else {
            "value": current_value,
            "evidence_level": frame.at[isin, "evidence_level"] if "evidence_level" in frame.columns else "D",
            "as_of": frame.at[isin, "as_of_date"] if "as_of_date" in frame.columns else "",
        }
        decision = decide(existing, obs)

        if decision.action in {"INSERT", "REPLACE"}:
            value = obs.get("value")
            frame.at[isin, field] = "" if value is None else str(value)
        elif decision.action == "QUARANTINE":
            quarantined.append({**obs, "reason": decision.reason})
        # KEEP -> rien à faire

    return frame.reset_index(drop=True), quarantined
