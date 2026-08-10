"""AMF Open Data – positions courtes nettes publiques.

The AMF source is historical. This module derives the *currently published*
position per holder/ISIN before aggregating by issuer. Values are percentage
points (e.g. 0.52 means 0.52%), not fractions to multiply by 100.
"""
from __future__ import annotations

import io
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import requests

from src.utils.logger import setup_logger

logger = setup_logger("amf_shorts")

AMF_CANDIDATE_URLS = [
    # Stable resource URL documented by data.gouv.fr.
    "https://www.data.gouv.fr/api/1/datasets/r/c2539d1c-8531-4937-9cba-3bd8e9786cc5",
]

CACHE_DIR = Path("data/raw/amf")
CACHE_FILE = CACHE_DIR / "shorts_latest.parquet"
CACHE_CSV = CACHE_DIR / "shorts_latest.csv"


def _slug(value: Any) -> str:
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _download_csv(url: str, timeout: int = 30) -> Optional[pd.DataFrame]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "TCT-PEA-Research/1.0"})
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "").lower()
        if "json" in content_type:
            data = r.json()
            resources = data.get("resources") or data.get("data", {}).get("resources") or []
            for res in resources:
                fmt = str(res.get("format") or "").lower()
                rurl = res.get("url") or ""
                if fmt in ("csv", "tsv") or rurl.lower().endswith((".csv", ".tsv")):
                    return _download_csv(rurl, timeout=timeout)
            logger.warning("Aucune ressource CSV trouvée dans la réponse AMF/data.gouv")
            return None

        text = r.content
        for sep in (";", ",", "\t"):
            try:
                df = pd.read_csv(io.BytesIO(text), sep=sep, low_memory=False)
                if df.shape[1] >= 3:
                    return df
            except Exception:
                continue
        return pd.read_csv(io.BytesIO(text), low_memory=False)
    except Exception as e:
        logger.warning(f"Download AMF échoué ({url[:70]}…) : {e}")
        return None


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the official French column names into a canonical schema."""
    out = df.copy()
    colmap: Dict[Any, str] = {}

    for c in out.columns:
        cl = _slug(c)
        if "isin" in cl:
            colmap[c] = "isin"
        elif "detenteur" in cl or "holder" in cl or "titulaire" in cl:
            # Must be evaluated before generic issuer/name matching.
            if "holder" not in colmap.values() and not ("lei" in cl):
                colmap[c] = "holder"
        elif "position_courte_nette" in cl or cl in {
            "ratio", "position", "position_courte", "pct", "pourcentage",
            "short", "short_percent", "net_short", "short_interest",
        }:
            if "short_interest" not in colmap.values():
                colmap[c] = "short_interest"
        elif "date_fin" in cl and "publication" in cl:
            colmap[c] = "publication_end"
        elif "date_debut" in cl and "publication" in cl:
            colmap[c] = "publication_start"
        elif "date" in cl and ("position" in cl or "debut" in cl):
            if "asof_date" not in colmap.values():
                colmap[c] = "asof_date"
        elif (
            "emetteur" in cl or "issuer" in cl or cl in {"nom", "name", "societe"}
        ) and "holder" not in cl:
            if "name" not in colmap.values():
                colmap[c] = "name"

    out = out.rename(columns=colmap)

    if "isin" not in out.columns:
        for c in out.columns:
            sample = out[c].astype(str).str.strip().str.upper()
            if sample.str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$").mean() > 0.5:
                out = out.rename(columns={c: "isin"})
                break

    if "short_interest" in out.columns:
        raw = (
            out["short_interest"]
            .astype(str)
            .str.replace("%", "", regex=False)
            .str.replace("\u202f", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        out["short_interest"] = pd.to_numeric(raw, errors="coerce")
        # IMPORTANT: AMF values are already percentage points (0.5 = 0.5%).
        out.loc[~out["short_interest"].between(0, 100), "short_interest"] = np.nan

    if "isin" in out.columns:
        out["isin"] = out["isin"].astype(str).str.strip().str.upper()
        out.loc[~out["isin"].str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"), "isin"] = np.nan

    for c in ("asof_date", "publication_start", "publication_end"):
        if c in out.columns:
            out[c] = pd.to_datetime(out[c], errors="coerce", dayfirst=True)

    if "holder" in out.columns:
        out["holder"] = out["holder"].astype(str).str.strip()
        out.loc[out["holder"].isin(["", "nan", "None"]), "holder"] = np.nan
    return out


def _current_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Convert the historical AMF file to currently published holder positions."""
    if df is None or df.empty or "isin" not in df.columns or "short_interest" not in df.columns:
        return pd.DataFrame(columns=["isin", "short_interest", "asof_date", "name", "holder"])

    cur = df.dropna(subset=["isin", "short_interest"]).copy()

    # The historical file exposes publication end dates. Current disclosures have no end date.
    if "publication_end" in cur.columns:
        cur = cur[cur["publication_end"].isna()].copy()

    # AMF visualisation convention: a published crossing below 0.5% is treated as closure,
    # because subsequent changes below the public threshold are not observable.
    cur.loc[cur["short_interest"] < 0.5, "short_interest"] = 0.0

    date_col = "asof_date" if "asof_date" in cur.columns else (
        "publication_start" if "publication_start" in cur.columns else None
    )
    if date_col:
        cur = cur.sort_values(date_col)

    # Keep only the latest current record per holder/issuer if duplicates are present.
    if "holder" in cur.columns and cur["holder"].notna().any():
        cur = cur.drop_duplicates(subset=["isin", "holder"], keep="last")
    elif date_col:
        # Without holder identity, we cannot safely sum historical duplicates.
        latest = cur.groupby("isin")[date_col].transform("max")
        cur = cur[cur[date_col].eq(latest)]

    return cur


def _aggregate_current(df: pd.DataFrame) -> pd.DataFrame:
    cur = _current_snapshot(df)
    if cur.empty:
        return pd.DataFrame(columns=["isin", "short_interest", "n_holders", "name", "asof_date"])

    agg_spec = {
        "short_interest": ("short_interest", "sum"),
        "n_holders": ("short_interest", lambda s: int((pd.to_numeric(s, errors="coerce") > 0).sum())),
    }
    if "name" in cur.columns:
        agg_spec["name"] = ("name", "first")
    if "asof_date" in cur.columns:
        agg_spec["asof_date"] = ("asof_date", "max")
    elif "publication_start" in cur.columns:
        agg_spec["asof_date"] = ("publication_start", "max")

    agg = cur.groupby("isin", as_index=False).agg(**agg_spec)
    if "name" not in agg.columns:
        agg["name"] = agg["isin"]
    if "asof_date" not in agg.columns:
        agg["asof_date"] = pd.Timestamp.now(tz="UTC").date().isoformat()
    else:
        agg["asof_date"] = pd.to_datetime(agg["asof_date"], errors="coerce").dt.strftime("%Y-%m-%d")

    agg["short_interest"] = pd.to_numeric(agg["short_interest"], errors="coerce").clip(0, 100)
    return agg


def fetch_amf_shorts(force_refresh: bool = False, max_age_hours: float = 24) -> pd.DataFrame:
    """Return a normalized *current* AMF public short-position snapshot."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not force_refresh and CACHE_CSV.exists():
        age_h = (time.time() - CACHE_CSV.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            try:
                cached = pd.read_csv(CACHE_CSV)
                logger.info(f"AMF shorts cache hit ({age_h:.1f}h) – {len(cached)} ISIN")
                return cached
            except Exception as e:
                logger.warning(f"Cache AMF illisible : {e}")

    raw = None
    for url in AMF_CANDIDATE_URLS:
        raw = _download_csv(url)
        if raw is not None and not raw.empty:
            logger.info(f"AMF historique téléchargé ({len(raw)} lignes)")
            break

    if raw is None or raw.empty:
        logger.warning("AMF shorts indisponible – DataFrame vide")
        return pd.DataFrame(columns=["isin", "short_interest", "n_holders", "asof_date", "name"])

    normalized = _normalize_columns(raw)
    df = _aggregate_current(normalized)
    if df.empty:
        logger.warning(f"Aucune position AMF courante dérivable; colonnes={list(normalized.columns)}")
        return df

    try:
        df.to_csv(CACHE_CSV, index=False)
        try:
            df.to_parquet(CACHE_FILE, index=False)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Sauvegarde cache AMF : {e}")

    logger.info(f"AMF snapshot courant : {len(df)} ISIN")
    return df


def shorts_by_isin(df_shorts: Optional[pd.DataFrame] = None) -> Dict[str, Dict[str, Any]]:
    if df_shorts is None:
        df_shorts = fetch_amf_shorts()
    if df_shorts is None or df_shorts.empty or "isin" not in df_shorts.columns:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for _, row in df_shorts.iterrows():
        isin = str(row["isin"]).upper()
        out[isin] = {
            "short_interest": float(row["short_interest"]) if pd.notna(row.get("short_interest")) else np.nan,
            "n_holders": int(row["n_holders"]) if pd.notna(row.get("n_holders")) else 0,
            "asof_date": row.get("asof_date"),
            "source": "AMF_OPEN_DATA",
            "freshness": "FRESH",
        }
    return out


def enrich_with_amf_shorts(signals: pd.DataFrame, force_refresh: bool = False) -> pd.DataFrame:
    if signals is None or signals.empty:
        return signals

    shorts = shorts_by_isin(fetch_amf_shorts(force_refresh=force_refresh))
    out = signals.copy()
    if not shorts:
        logger.warning("Aucun short AMF – valeurs amont conservées")
        if "short_interest" not in out.columns:
            out["short_interest"] = np.nan
        if "short_source" not in out.columns:
            out["short_source"] = "NONE"
        return out

    si_list, src_list, holders, asof = [], [], [], []
    for _, row in out.iterrows():
        isin = str(row.get("isin") or "").upper()
        info = shorts.get(isin)
        if info and pd.notna(info.get("short_interest")):
            si_list.append(info["short_interest"])
            src_list.append("AMF")
            holders.append(info.get("n_holders", 0))
            asof.append(info.get("asof_date"))
        else:
            prev = pd.to_numeric(pd.Series([row.get("short_interest")]), errors="coerce").iloc[0]
            si_list.append(float(prev) if pd.notna(prev) else np.nan)
            src_list.append(row.get("short_source") or "EXISTING")
            holders.append(row.get("short_n_holders", np.nan))
            asof.append(row.get("short_asof_date"))

    out["short_interest"] = si_list
    out["short_source"] = src_list
    out["short_n_holders"] = holders
    out["short_asof_date"] = asof
    logger.info(f"AMF shorts : {sum(s == 'AMF' for s in src_list)}/{len(out)} titres matchés")
    return out


def demo_amf_shorts(n: int = 50) -> pd.DataFrame:
    """Explicit test helper only; never used as a production fallback."""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "isin": [f"FR{str(i).zfill(10)}" for i in range(n)],
        "short_interest": np.round(rng.uniform(0.5, 3.0, n), 2),
        "n_holders": rng.integers(1, 4, n),
        "asof_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "name": [f"DEMO_{i}" for i in range(n)],
    })
