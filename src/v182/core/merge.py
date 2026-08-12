from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import numbers
import pandas as pd

RANK={"A":4,"B":3,"C":2,"D":1}
ACCEPTED_VALIDATION_STATUSES={"VALIDATED","ISIN_MATCHED","AUTO_MATCH","ATTRIBUTED"}
MISSING_TOKENS={"","MISSING","UNKNOWN","NON_OBSERVE","NOT_LOADED","NAN","<NA>","N/A","NA","NULL","NONE"}


@dataclass(frozen=True)
class MergeDecision:
    action: str
    reason: str


def is_missing_value(value) -> bool:
    if value is None: return True
    try:
        marker=pd.isna(value)
        if isinstance(marker,bool) and marker: return True
        try:
            if bool(marker): return True
        except (TypeError,ValueError):
            pass
    except (TypeError,ValueError):
        pass
    return str(value).strip().upper() in MISSING_TOKENS


def _normalized_value(value):
    if is_missing_value(value): return ("MISSING",None)
    if isinstance(value,bool): return ("BOOL",value)
    if isinstance(value,numbers.Number):
        try: return ("NUMBER",Decimal(str(value)).normalize())
        except InvalidOperation: pass
    text=str(value).strip()
    try: return ("NUMBER",Decimal(text).normalize())
    except InvalidOperation: return ("TEXT",text.casefold())


def values_equal(left,right) -> bool:
    return _normalized_value(left)==_normalized_value(right)


def decide(existing: dict | None, incoming: dict) -> MergeDecision:
    if incoming.get("validation_status") not in ACCEPTED_VALIDATION_STATUSES:
        return MergeDecision("QUARANTINE","IDENTITY_NOT_VALIDATED")
    incoming_value=incoming.get("value")
    if is_missing_value(incoming_value):
        return MergeDecision("KEEP","NO_REGRESSION_MISSING")
    if existing is None:
        return MergeDecision("INSERT","FIRST_OBSERVATION")
    old_rank=RANK.get(str(existing.get("evidence_level","D")).upper(),0)
    new_rank=RANK.get(str(incoming.get("evidence_level","D")).upper(),0)
    if new_rank>old_rank:
        return MergeDecision("REPLACE","HIGHER_EVIDENCE")
    if new_rank<old_rank:
        return MergeDecision("KEEP","LOWER_EVIDENCE")
    if str(incoming.get("as_of","") or "")>str(existing.get("as_of","") or ""):
        return MergeDecision("REPLACE","FRESHER_EQUAL_EVIDENCE")
    if not values_equal(incoming_value,existing.get("value")):
        return MergeDecision("QUARANTINE","CONFLICT_EQUAL_EVIDENCE")
    return MergeDecision("KEEP","NO_CHANGE")
