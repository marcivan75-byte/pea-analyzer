from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
import re
import unicodedata
import pandas as pd

AMF_CURRENT_RESOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/c2539d1c-8531-4937-9cba-3bd8e9786cc5"


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _find_column(columns: list[str], needles: tuple[str, ...]) -> str | None:
    normalized = {col: _norm(col) for col in columns}
    for needle in needles:
        key = _norm(needle)
        for col, normed in normalized.items():
            if key == normed or key in normed:
                return col
    return None


def _read_csv_text(text: str) -> pd.DataFrame:
    attempts = (";", ",", "\t")
    best = pd.DataFrame()
    for separator in attempts:
        try:
            frame = pd.read_csv(StringIO(text), sep=separator, dtype=str, low_memory=False)
        except (pd.errors.ParserError, UnicodeError, ValueError):
            continue
        if frame.shape[1] > best.shape[1]:
            best = frame
    return best


def _pct(value: object) -> float | None:
    raw = str(value or "").strip().replace("%", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_amf_short_positions(actions: pd.DataFrame, source: pd.DataFrame, *, observed_at: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Build a proxy from AMF publications that are still open at observation time.

    This is deliberately not labelled `current short interest`. The AMF public
    file is historical and a last public position can remain published even when
    the holder has subsequently moved below the public-disclosure threshold.
    Absence from the public file is therefore never treated as zero exposure.
    """
    now = observed_at or datetime.now(timezone.utc)
    if source.empty:
        return [], [{"source": "AMF_OPEN_DATA", "reason": "EMPTY_DATASET"}]

    columns = list(source.columns)
    isin_col = _find_column(columns, ("isin", "code isin"))
    pct_col = _find_column(columns, ("position courte nette", "position nette", "net short position"))
    position_date_col = _find_column(columns, ("date de debut de position", "date de la position", "date position"))
    start_pub_col = _find_column(columns, ("date de debut de publication", "debut de publication", "publication start"))
    end_pub_col = _find_column(columns, ("date de fin de publication", "fin de publication", "publication end"))
    holder_col = _find_column(columns, ("nom du detenteur", "detenteur", "position holder", "holder"))
    required = {
        "isin": isin_col,
        "position_pct": pct_col,
        "position_date": position_date_col,
        "publication_end": end_pub_col,
        "holder": holder_col,
    }
    missing = [name for name, col in required.items() if not col]
    if missing:
        return [], [{
            "source": "AMF_OPEN_DATA",
            "reason": "REQUIRED_COLUMNS_NOT_FOUND",
            "missing": "|".join(missing),
            "columns": "|".join(columns[:40]),
        }]

    allowed = set(actions.get("isin", pd.Series(dtype=str)).astype(str).str.strip().str.upper())
    work = source.copy()
    work["_isin"] = work[isin_col].astype(str).str.strip().str.upper()
    work = work.loc[work["_isin"].isin(allowed)].copy()
    if work.empty:
        return [], []

    work["_position_date"] = pd.to_datetime(work[position_date_col], errors="coerce", dayfirst=True)
    work["_start_pub"] = pd.to_datetime(work[start_pub_col], errors="coerce", dayfirst=True) if start_pub_col else pd.NaT
    work["_end_pub"] = pd.to_datetime(work[end_pub_col], errors="coerce", dayfirst=True)
    work["_pct"] = work[pct_col].map(_pct)
    work["_holder"] = work[holder_col].astype(str).str.strip()
    work = work.loc[work["_position_date"].notna() & work["_pct"].notna() & work["_holder"].ne("")].copy()

    as_of_day = pd.Timestamp(now.date())
    active = work.loc[work["_end_pub"].isna() | (work["_end_pub"] >= as_of_day)].copy()
    if active.empty:
        return [], []

    # Defensive de-duplication: one active record per holder/issuer, keeping the
    # latest position date (and publication start as a tie-breaker).
    active["_start_pub_sort"] = active["_start_pub"].fillna(pd.Timestamp.min)
    active = active.sort_values(["_isin", "_holder", "_position_date", "_start_pub_sort"])
    active = active.groupby(["_isin", "_holder"], as_index=False, sort=False).tail(1)

    observations: list[dict] = []
    for isin, group in active.groupby("_isin"):
        position_sum = float(group["_pct"].sum())
        holder_count = int(group["_holder"].nunique())
        latest = group["_position_date"].max()
        values = {
            "amf_public_short_disclosed_sum_pct": round(position_sum, 6),
            "amf_public_short_holder_count": holder_count,
            "amf_public_short_latest_position_date": latest.date().isoformat(),
            "amf_public_short_open_publication_count": int(len(group)),
            "amf_public_short_proxy_flag": 1,
            "amf_public_short_not_true_current_interest_flag": 1,
        }
        for field, value in values.items():
            observations.append({
                "universe": "ACTION",
                "isin": isin,
                "field": field,
                "value": value,
                "source": "AMF Open Data - public net short disclosures",
                "collected_at": now.isoformat(),
                "as_of": now.date().isoformat(),
                "evidence_level": "A",
                "validation_status": "ISIN_MATCHED",
            })
    return observations, []


def fetch_amf_short_positions(
    actions: pd.DataFrame,
    *,
    url: str = AMF_CURRENT_RESOURCE_URL,
    requests_module=None,
    observed_at: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    import requests as requests_default

    requests = requests_module or requests_default
    try:
        response = requests.get(url, timeout=45, headers={"User-Agent": "PEA-Analyzer/21.6.3 data-quality"})
        response.raise_for_status()
        raw = response.content
        text = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            return [], [{"source": "AMF_OPEN_DATA", "reason": "DECODE_FAILED"}]
        frame = _read_csv_text(text)
        return parse_amf_short_positions(actions, frame, observed_at=observed_at)
    except Exception as exc:
        return [], [{"source": "AMF_OPEN_DATA", "reason": type(exc).__name__, "detail": str(exc)[:240]}]
