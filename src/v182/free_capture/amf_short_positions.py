from __future__ import annotations

from datetime import date
from io import BytesIO, StringIO
import os
import re
import unicodedata

import pandas as pd
import requests

from .core import CaptureStore, is_observed, number, utcnow


SOURCE = "AMF_SHORT_POSITIONS"
# Official AMF open-data resource documented in the PEA free-source reference.
DEFAULT_URL = "https://www.data.gouv.fr/api/1/datasets/r/c2531aef-777a-4d2d-82f1-869417867133"


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.upper()
    text = re.sub(r"\b(SA|SAS|SCA|SE|NV|PLC|SPA|AG|GROUP|GROUPE|HOLDING|HOLDINGS)\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def _column(columns: list[str], patterns: tuple[str, ...]) -> str | None:
    normalized = {c: re.sub(r"[^A-Z0-9]", "", _norm(c)) for c in columns}
    for pattern in patterns:
        p = re.sub(r"[^A-Z0-9]", "", _norm(pattern))
        for column, value in normalized.items():
            if p == value or p in value:
                return column
    return None


def _read_payload(content: bytes, content_type: str = "") -> pd.DataFrame:
    # The AMF resource is normally an Excel workbook. CSV fallbacks are kept because
    # data.gouv resource representations can change without changing the dataset contract.
    if content[:4] == b"PK\x03\x04" or "spreadsheet" in content_type or "excel" in content_type:
        book = pd.ExcelFile(BytesIO(content))
        frames = []
        for sheet in book.sheet_names:
            try:
                frame = pd.read_excel(book, sheet_name=sheet, dtype=object)
                if not frame.empty:
                    frames.append(frame)
            except Exception:
                continue
        if frames:
            return pd.concat(frames, ignore_index=True)
    text = content.decode("utf-8-sig", errors="replace")
    for sep in (";", ",", "\t", "|"):
        try:
            frame = pd.read_csv(StringIO(text), sep=sep, dtype=object, low_memory=False)
            if len(frame.columns) >= 3:
                return frame
        except Exception:
            continue
    raise RuntimeError("AMF_SHORT_POSITIONS_UNREADABLE_RESOURCE")


def _latest_public_positions(raw: pd.DataFrame) -> pd.DataFrame:
    columns = list(raw.columns)
    issuer_c = _column(columns, ("Emetteur", "Issuer", "Nom de l'emetteur", "Raison sociale"))
    isin_c = _column(columns, ("ISIN", "Code ISIN"))
    holder_c = _column(columns, ("Detenteur", "Holder", "Position holder", "Declarant"))
    pct_c = _column(columns, ("Position courte nette", "Net short position", "Position en %", "Position"))
    date_c = _column(columns, ("Date de la position", "Position date", "Date"))
    if issuer_c is None or pct_c is None or date_c is None:
        raise RuntimeError(f"AMF_SHORT_POSITIONS_SCHEMA_UNRECOGNIZED:{columns[:20]}")

    out = pd.DataFrame({
        "issuer": raw[issuer_c].astype(str).str.strip(),
        "isin": raw[isin_c].astype(str).str.strip().str.upper() if isin_c else "",
        "holder": raw[holder_c].astype(str).str.strip() if holder_c else "",
        "position_pct": pd.to_numeric(
            raw[pct_c].astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce",
        ),
        "position_date": pd.to_datetime(raw[date_c], errors="coerce", dayfirst=True),
    })
    out = out.dropna(subset=["position_pct", "position_date"])
    out = out[out["position_pct"].between(0, 100)]
    out["issuer_norm"] = out["issuer"].map(_norm)
    out["position_day"] = out["position_date"].dt.date.astype(str)
    return out


def _match_rows(universe: pd.DataFrame, positions: pd.DataFrame) -> list[dict]:
    today = date.today().isoformat()
    facts: list[dict] = []
    by_isin = {str(x).strip().upper(): i for i, x in enumerate(universe["isin"].astype(str))}
    by_name: dict[str, list[int]] = {}
    for i, name in enumerate(universe.get("name", pd.Series("", index=universe.index)).astype(str)):
        n = _norm(name)
        if n:
            by_name.setdefault(n, []).append(i)

    matched: dict[int, pd.DataFrame] = {}
    for _, row in positions.iterrows():
        idx = None
        isin = str(row.get("isin") or "").strip().upper()
        if isin and isin in by_isin:
            idx = by_isin[isin]
        else:
            candidates = by_name.get(str(row.get("issuer_norm") or ""), [])
            if len(candidates) == 1:
                idx = candidates[0]
        if idx is None:
            continue
        matched.setdefault(idx, []).append(row)

    for idx, rows in matched.items():
        g = pd.DataFrame(rows)
        latest_day = g["position_date"].max()
        current = g[g["position_date"].eq(latest_day)]
        if current.empty:
            continue
        aggregate = float(current["position_pct"].sum())
        max_holder = float(current["position_pct"].max())
        holder_count = int(current["holder"].replace("", pd.NA).dropna().nunique()) if "holder" in current else 0
        isin = str(universe.iloc[idx]["isin"])
        values = {
            "amf_public_net_short_pct": aggregate,
            "amf_public_net_short_max_holder_pct": max_holder,
            "amf_public_net_short_holders": holder_count,
            "amf_public_net_short_latest_date": latest_day.date().isoformat(),
        }
        for field, value in values.items():
            numeric = number(value)
            facts.append({
                "isin": isin,
                "field": field,
                "value": numeric if numeric is not None else "",
                "value_text": "" if numeric is not None else str(value),
                "as_of": latest_day.date().isoformat() if pd.notna(latest_day) else today,
                "source": SOURCE,
                "evidence": "AMF_OFFICIAL_DAILY_OPEN_DATA_PUBLIC_POSITIONS_GE_0_5PCT",
                "confidence": 0.98,
                "status": "OBSERVED_REGULATORY",
                "observed_at_utc": utcnow(),
            })
    return facts


def capture(universe: pd.DataFrame, store: CaptureStore) -> dict:
    url = str(os.getenv("V211_AMF_SHORT_POSITIONS_URL") or DEFAULT_URL).strip()
    try:
        response = requests.get(
            url,
            headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-V21.1-FreeCapture/1.2")},
            timeout=45,
        )
        response.raise_for_status()
        raw = _read_payload(response.content, response.headers.get("Content-Type", ""))
        positions = _latest_public_positions(raw)
        facts = _match_rows(universe, positions)
        added = store.upsert_facts(facts)
        matched_isin = len({x["isin"] for x in facts})
        status = "OK" if added else "NO_MATCHING_PUBLIC_POSITION"
        store.add_health(
            SOURCE,
            status,
            attempted=len(universe),
            succeeded=matched_isin,
            failed=0,
            message=f"raw_rows={len(raw)}; normalized_rows={len(positions)}; facts_added={added}",
        )
        return {
            "status": status,
            "resource": url,
            "raw_rows": len(raw),
            "normalized_rows": len(positions),
            "matched_isin": matched_isin,
            "facts_added": added,
            "public_threshold_note": "AMF public file contains published net short positions; public disclosure threshold applies",
        }
    except Exception as exc:
        store.add_health(SOURCE, "ERROR", attempted=1, failed=1, message=f"{type(exc).__name__}: {str(exc)[:500]}")
        return {"status": "ERROR", "resource": url, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
