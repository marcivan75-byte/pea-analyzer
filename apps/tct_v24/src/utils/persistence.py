from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Any

import numpy as np

from src.utils.logger import setup_logger

logger = setup_logger("persistence")


def _business_sessions_since(iso_date: str, as_of: date | None = None) -> int:
    """Approximate elapsed trading sessions with Mon-Fri business days.

    Exchange holidays are intentionally not guessed here; using business days makes
    the TTL deterministic and conservative enough for stale-T1 protection.
    """
    as_of = as_of or datetime.now(timezone.utc).date()
    try:
        start = date.fromisoformat(str(iso_date)[:10])
    except Exception:
        return 10**9
    if start >= as_of:
        return 0
    return int(np.busday_count(start.isoformat(), as_of.isoformat()))


def load_last_t1(
    path: str = "data/persistence/last_T1_bandwidth.json",
    ttl_sessions: int = 40,
) -> Dict[str, float]:
    """Load ``{ISIN: bandwidth}`` while pruning stale T1 states.

    V24.1.4 persists records as ``{"bandwidth": x, "detected_at": YYYY-MM-DD}``.
    Legacy scalar values remain readable but, because their age is unknowable, they
    are ignored when a positive TTL is requested. This is fail-closed: an ancient
    T1 must never remain eligible for T2 forever.
    """
    p = Path(path)
    if not p.exists():
        logger.info("Fichier de persistance absent – initialisation vide")
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, Mapping):
            raise ValueError("format persistence non dictionnaire")

        clean: Dict[str, float] = {}
        stale = 0
        legacy = 0
        for isin, raw in data.items():
            if isinstance(raw, Mapping):
                bw = raw.get("bandwidth")
                detected_at = raw.get("detected_at") or raw.get("date")
                try:
                    bw_f = float(bw)
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(bw_f):
                    continue
                if ttl_sessions > 0 and _business_sessions_since(str(detected_at)) > ttl_sessions:
                    stale += 1
                    continue
                clean[str(isin)] = bw_f
            elif isinstance(raw, (int, float)) and np.isfinite(float(raw)):
                legacy += 1
                if ttl_sessions <= 0:
                    clean[str(isin)] = float(raw)

        if stale:
            logger.info(f"T1 expirés par TTL: {stale}")
        if legacy and ttl_sessions > 0:
            logger.warning(
                f"{legacy} T1 legacy sans date ignorés pour éviter une confirmation T2 intemporelle"
            )
        logger.info(f"Persistance chargée : {len(clean)} ISINs actifs")
        return clean
    except Exception as e:
        logger.error(f"Erreur chargement persistance : {e}")
        return {}


def save_last_t1(
    data: Dict[str, float] | Dict[str, Dict[str, Any]],
    path: str = "data/persistence/last_T1_bandwidth.json",
    detected_at: str | None = None,
) -> None:
    """Atomically persist T1 states with their detection date."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    detected_at = detected_at or datetime.now(timezone.utc).date().isoformat()
    payload: Dict[str, Dict[str, Any]] = {}
    for isin, raw in data.items():
        if isinstance(raw, Mapping):
            try:
                bw = float(raw.get("bandwidth"))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(bw):
                continue
            payload[str(isin)] = {
                "bandwidth": bw,
                "detected_at": str(raw.get("detected_at") or detected_at)[:10],
            }
        else:
            try:
                bw = float(raw)
            except (TypeError, ValueError):
                continue
            if np.isfinite(bw):
                payload[str(isin)] = {"bandwidth": bw, "detected_at": detected_at[:10]}

    try:
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(p)
        logger.info(f"Persistance sauvegardée : {len(payload)} ISINs")
    except Exception as e:
        logger.error(f"Erreur sauvegarde persistance : {e}")
