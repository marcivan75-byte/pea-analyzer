"""
Short Interest – Univers Euronext (actions)

CONTEXTE (CDC + réglementation ESMA SSR) :
- Euronext NE publie PAS de short interest equity open data.
  Le produit "Volume & Open Interest" Euronext concerne les *dérivés* (payant, SFTP Data Shop).
- Les positions courtes nettes sur actions Euronext sont publiées par les
  autorités nationales compétentes (NCA) dès le seuil public (0,5 % en règle générale) :
    • France (Euronext Paris)     → AMF Open Data (gratuit)  ← source principale PEA
    • Pays-Bas (Amsterdam)       → AFM
    • Belgique (Brussels)        → FSMA
    • Portugal (Lisbon)          → CMVM
    • Irlande (Dublin)           → Central Bank of Ireland
- Pour le PEA français, AMF couvre l'essentiel de l'univers actionnable.

Ce module :
1. Agrège AMF (implémenté) + hooks NCA Euronext
2. Enrichit les signaux avec short_interest unifié
3. Tagge mic / marché Euronext quand disponible
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Any
from datetime import datetime

import numpy as np
import pandas as pd

from src.utils.logger import setup_logger
from src.data.amf_shorts import (
    fetch_amf_shorts,
    shorts_by_isin as amf_shorts_by_isin,
    enrich_with_amf_shorts,
    demo_amf_shorts,
)

logger = setup_logger("euronext_shorts")

# Mapping MIC Euronext courants (PEA / Europe)
EURONEXT_MICS = {
    "XPAR": "Paris",
    "XAMS": "Amsterdam",
    "XBRU": "Brussels",
    "XLIS": "Lisbon",
    "XMSM": "Dublin",  # Euronext Dublin
    "MTAA": "Milan",   # parfois lié écosystème
}

# Sources NCA par MIC (URLs de référence – à brancher Free Capture)
NCA_SOURCES = {
    "XPAR": {
        "authority": "AMF",
        "country": "FR",
        "url_hint": "https://www.data.gouv.fr/fr/datasets/positions-courtes-nettes-sur-les-actions/",
        "implemented": True,
    },
    "XAMS": {
        "authority": "AFM",
        "country": "NL",
        "url_hint": "https://www.afm.nl/en/sector/registers/meldingenregisters/short-selling",
        "implemented": False,  # hook Free Capture
    },
    "XBRU": {
        "authority": "FSMA",
        "country": "BE",
        "url_hint": "https://www.fsma.be/en/short-selling",
        "implemented": False,
    },
    "XLIS": {
        "authority": "CMVM",
        "country": "PT",
        "url_hint": "https://www.cmvm.pt/",
        "implemented": False,
    },
}


def fetch_euronext_short_interest(
    force_refresh: bool = False,
    include_demo_fallback: bool = False,
) -> pd.DataFrame:
    """
    Collecte le short interest pour l'univers Euronext.
    Aujourd'hui : AMF (Paris) opérationnel.
    AFM/FSMA/CMVM : réservés au Free Capture (pas d'appel réseau fragile ici).
    """
    frames = []

    # --- AMF (Euronext Paris) ---
    try:
        amf = fetch_amf_shorts(force_refresh=force_refresh)
        if amf is not None and not amf.empty:
            amf = amf.copy()
            amf["mic"] = "XPAR"
            amf["authority"] = "AMF"
            amf["market"] = "Euronext Paris"
            frames.append(amf)
            logger.info(f"Euronext shorts AMF : {len(amf)} ISIN")
    except Exception as e:
        logger.warning(f"AMF fetch error : {e}")

    # --- Hooks autres NCA (données attendues via Free Capture) ---
    capture_dir = Path("data/raw/euronext_shorts")
    for mic, meta in NCA_SOURCES.items():
        if meta["implemented"]:
            continue
        # Fichiers déposés par Free Capture : {MIC}_shorts.csv
        candidate = capture_dir / f"{mic}_shorts.csv"
        if candidate.exists():
            try:
                df = pd.read_csv(candidate)
                if "isin" in df.columns:
                    df["mic"] = mic
                    df["authority"] = meta["authority"]
                    df["market"] = EURONEXT_MICS.get(mic, mic)
                    frames.append(df)
                    logger.info(f"Euronext shorts {mic}/{meta['authority']} : {len(df)} lignes (Free Capture)")
            except Exception as e:
                logger.warning(f"Lecture {candidate} : {e}")

    if not frames:
        if include_demo_fallback:
            logger.warning("Aucune source NCA – demo AMF fallback")
            demo = demo_amf_shorts(60)
            demo["mic"] = "XPAR"
            demo["authority"] = "AMF_DEMO"
            demo["market"] = "Euronext Paris"
            return demo
        return pd.DataFrame(columns=["isin", "short_interest", "mic", "authority"])

    out = pd.concat(frames, ignore_index=True)

    # Déduplication ISIN : priorité AMF > autres, puis max short
    if "isin" in out.columns and "short_interest" in out.columns:
        out["short_interest"] = pd.to_numeric(out["short_interest"], errors="coerce")
        out["isin"] = out["isin"].astype(str).str.upper().str.strip()
        # Priorité authority
        prio = {"AMF": 0, "AMF_DEMO": 2, "AFM": 1, "FSMA": 1, "CMVM": 1}
        out["_prio"] = out["authority"].map(prio).fillna(5)
        out = out.sort_values(["isin", "_prio", "short_interest"], ascending=[True, True, False])
        out = out.drop_duplicates(subset=["isin"], keep="first")
        out = out.drop(columns=["_prio"], errors="ignore")

    # Cache
    cache = Path("data/raw/euronext_shorts/aggregated.csv")
    cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.to_csv(cache, index=False)
    except Exception:
        pass

    return out


def enrich_with_euronext_shorts(
    signals: pd.DataFrame,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Enrichit les signaux avec short interest univers Euronext.
    Colonnes : short_interest, short_source, short_authority, short_mic, short_n_holders
    """
    if signals is None or signals.empty:
        return signals

    shorts_df = fetch_euronext_short_interest(force_refresh=force_refresh, include_demo_fallback=False)
    if shorts_df is None or shorts_df.empty or "isin" not in shorts_df.columns:
        logger.warning("Euronext shorts vide – tentative AMF seul")
        return enrich_with_amf_shorts(signals, force_refresh=force_refresh)

    by_isin: Dict[str, Dict[str, Any]] = {}
    for _, row in shorts_df.iterrows():
        isin = str(row["isin"]).upper()
        by_isin[isin] = {
            "short_interest": float(row["short_interest"]) if pd.notna(row.get("short_interest")) else np.nan,
            "authority": row.get("authority", "NCA"),
            "mic": row.get("mic"),
            "n_holders": row.get("n_holders", np.nan),
            "asof_date": row.get("asof_date"),
        }

    out = signals.copy()
    si, src, auth, mic, holders = [], [], [], [], []
    for _, row in out.iterrows():
        isin = str(row.get("isin") or "").upper()
        info = by_isin.get(isin)
        if info and pd.notna(info.get("short_interest")):
            si.append(info["short_interest"])
            src.append("EURONEXT_NCA")
            auth.append(info["authority"])
            mic.append(info.get("mic"))
            holders.append(info.get("n_holders"))
        else:
            prev = row.get("short_interest")
            si.append(float(prev) if pd.notna(prev) else np.nan)
            src.append(row.get("short_source") or "NONE")
            auth.append(row.get("short_authority") or None)
            mic.append(row.get("mic") or row.get("short_mic"))
            holders.append(row.get("short_n_holders"))

    out["short_interest"] = si
    out["short_source"] = src
    out["short_authority"] = auth
    out["short_mic"] = mic
    out["short_n_holders"] = holders

    n = sum(1 for s in src if s == "EURONEXT_NCA")
    logger.info(f"Euronext shorts : {n}/{len(out)} titres enrichis")
    return out
