from __future__ import annotations
from pathlib import Path
import pandas as pd
from v183.smart_money.models import SmartMoneyEvent

REQUIRED = {"isin", "event_type", "event_subtype", "publication_date", "source_document_id"}


def load_normalized_official_events(path: str | Path) -> list[dict]:
    """Fail-closed AMF BDIF bridge.

    Intended for normalized exports produced from official AMF documents until a documented,
    stable machine endpoint is validated. REVIEW/ambiguous rows are not accepted as VALIDATED.
    """
    df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"missing AMF official event columns: {sorted(missing)}")
    events = []
    for _, r in df.iterrows():
        status = str(r.get("validation_status") or "VALIDATED")
        if status not in {"VALIDATED", "ISIN_MATCHED"}:
            continue
        e = SmartMoneyEvent(
            universe="ACTION",
            isin=str(r["isin"]).strip().upper(),
            event_type=str(r["event_type"]).strip().upper(),
            event_subtype=str(r["event_subtype"]).strip().upper(),
            source="AMF_BDIF_OFFICIAL_DOCUMENT",
            evidence_level="A",
            validation_status=status,
            publication_date=str(r["publication_date"])[:10],
            transaction_date=_s(r.get("transaction_date")),
            actor_name=_s(r.get("actor_name")),
            actor_role=_s(r.get("actor_role")),
            direction=int(float(r.get("direction") or 0)),
            quantity=_f(r.get("quantity")),
            price=_f(r.get("price")),
            currency=_s(r.get("currency")),
            value_eur=_f(r.get("value_eur")),
            stake_before=_f(r.get("stake_before")),
            stake_after=_f(r.get("stake_after")),
            threshold_pct=_f(r.get("threshold_pct")),
            source_document_id=str(r["source_document_id"]),
        )
        events.append(e.to_dict())
    return events


def _s(v):
    if v is None or pd.isna(v) or str(v).strip() == "":
        return None
    return str(v).strip()


def _f(v):
    try:
        if v is None or pd.isna(v) or str(v).strip() == "":
            return None
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
