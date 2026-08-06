from __future__ import annotations
from typing import Literal
from pydantic import BaseModel

class EnrichmentJob(BaseModel):
    universe: Literal["ACTION","ETF"]
    isin: str
    ticker: str | None = None
    group: str
    missing_fields: list[str]
    preferred_method: str
    priority_score: float
    status: Literal["PENDING","RUNNING","DONE","PARTIAL","FAILED","INPUT_REQUIRED"]
