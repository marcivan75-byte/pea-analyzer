"""Contrat d'entrée du moteur TCT.

Le moteur décisionnel travaille sur un snapshot Free Capture déjà enrichi. Il ne
fabrique pas silencieusement les données critiques absentes.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


CORE_REQUIRED = (
    "isin",
    "close",
    "avg_dollar_volume_20d",
    "days_to_earnings",
    "setup",
)


def validate_signal_contract(df: pd.DataFrame, required: Iterable[str] = CORE_REQUIRED) -> pd.DataFrame:
    """Valide les colonnes structurantes avant toute décision.

    ``setup`` doit être présent dans le snapshot, même si sa valeur est nulle
    pour les titres sans T1/T2. Le détecteur historique T1/T2 reste disponible
    comme bibliothèque, mais il n'est pas autorisé à déclencher des appels
    réseau cachés depuis le moteur de scoring.
    """
    if df is None or df.empty:
        raise ValueError("Free Capture vide")

    missing = [c for c in required if c not in df.columns]
    if "ticker" not in df.columns and "symbol" not in df.columns:
        missing.append("ticker|symbol")
    if "pea_eligible" not in df.columns and "pea_proof_level" not in df.columns:
        missing.append("pea_eligible|pea_proof_level")
    if missing:
        raise ValueError("Contrat Free Capture incomplet: " + ", ".join(missing))

    out = df.copy()
    # Les doublons ISIN créent des doubles recommandations et faussent les caps.
    isin = out["isin"].astype(str).str.strip().str.upper()
    dup = isin.ne("") & isin.duplicated(keep=False)
    if dup.any():
        examples = sorted(isin[dup].dropna().unique().tolist())[:5]
        raise ValueError(
            f"ISIN dupliqués dans Free Capture ({int(dup.sum())} lignes), exemples={examples}"
        )

    out["input_contract_valid"] = True
    return out
