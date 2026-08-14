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
    """Return only disclosed AMF positions. Absence from the file is never treated as zero."""
    now = observed_at or datetime.now(timezone.utc)
    failures: list[dict] = []
    if source.empty:
        return [], [{"source": "AMF_OPEN_DATA", "reason": "EMPTY_DATASET"}]

    columns = list(source.columns)
    isin_col = _find_column(columns, ("isin", "code isin"))
    pct_col = _find_column(columns, ("position courte nette", "position nette", "net short position", "position"))
    date_col = _find_column(columns, ("date de la position", "date position", "date"))
    holder_col = _find_column(columns, ("detenteur", "holder", "nom du detenteur", "declarant"))
    if not isin_col or not pct_col or not date_col:
        return [], [{"source": "AMF_OPEN_DATA", "reason": "REQUIRED_COLUMNS_NOT_FOUND", "columns": "|".join(columns[:30])}]

    allowed = set(actions.get("isin", pd.Series(dtype=str)).astype(str).str.strip())
    work = source.copy()
    work["_isin"] = work[isin_col].astype(str).str.strip().str.upper()
    work = work.loc[work["_isin"].isin(allowed)].copy()
    if work.empty:
        return [], []

    work["_date"] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work["_pct"] = work[pct_col].map(_pct)
    work = work.loc[work["_date"].notna() & work["_pct"].notna()].copy()
    observations: list[dict] = []
    for isin, group in work.groupby("_isin"):
        latest = group["_date"].max()
        current = group.loc[group["_date"] == latest]
        position_sum = float(current["_pct"].sum())
        holder_count = int(current[holder_col].astype(str).nunique()) if holder_col else int(len(current))
        values = {
            "amf_short_position_pct": round(position_sum, 6),
            "amf_short_holder_count": holder_count,
            "amf_short_latest_date": latest.date().isoformat(),
            "amf_short_disclosed_flag": 1,
        }
        for field, value in values.items():
            observations.append({
                "universe": "ACTION",
                "isin": isin,
                "field": field,
                "value": value,
                "source": "AMF Open Data - positions courtes nettes",
                "collected_at": now.isoformat(),
                "as_of": latest.date().isoformat(),
                "evidence_level": "A",
                "validation_status": "ISIN_MATCHED",
            })
    return observations, failures


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
