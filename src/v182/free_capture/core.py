from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "data/reference/V21.1_FREE_CAPTURE_CONFIG.json"
DEFAULT_ROOT = ROOT / "outputs/free_capture"

EXPLICIT_MISSING = {
    "", "nan", "none", "<na>", "na", "n/a", "not_available", "not available",
    "data_not_available", "unavailable", "unknown", "null", "not_applicable",
    "not applicable", "no_data", "no data", "missing"
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def is_observed(value: object) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    return s not in EXPLICIT_MISSING


def clean_text(value: object) -> str:
    return str(value).strip() if is_observed(value) else ""


def number(value: object) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    for c in columns:
        if c not in df:
            df[c] = ""
    return df[columns]


def write_csv(df: pd.DataFrame, path: Path, sort_by: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if sort_by:
        existing = [c for c in sort_by if c in out]
        if existing:
            out = out.sort_values(existing, kind="stable")
    out.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


IDENTITY_COLS = [
    "isin", "name", "source", "ticker", "exchange", "mic", "figi", "composite_figi",
    "share_class_figi", "security_type", "lei", "lei_source", "resolution_status", "as_of",
    "observed_at_utc"
]
MARKET_COLS = [
    "isin", "date", "open", "high", "low", "close", "volume", "currency", "source",
    "ticker", "mic", "observed_at_utc"
]
FACT_COLS = [
    "isin", "field", "value", "value_text", "as_of", "source", "evidence", "confidence",
    "status", "observed_at_utc"
]
HEALTH_COLS = [
    "source", "status", "attempted", "succeeded", "failed", "quota_used", "quota_left",
    "message", "observed_at_utc"
]


@dataclass
class CaptureStore:
    root: Path = DEFAULT_ROOT

    @property
    def identity_path(self) -> Path:
        return self.root / "V21.1_IDENTITY.csv"

    @property
    def market_path(self) -> Path:
        return self.root / "V21.1_MARKET_DAILY.csv"

    @property
    def facts_path(self) -> Path:
        return self.root / "V21.1_FACTS_LONG.csv"

    @property
    def health_path(self) -> Path:
        return self.root / "V21.1_SOURCE_HEALTH.csv"

    def identity(self) -> pd.DataFrame:
        return read_csv(self.identity_path, IDENTITY_COLS)

    def market(self) -> pd.DataFrame:
        return read_csv(self.market_path, MARKET_COLS)

    def facts(self) -> pd.DataFrame:
        return read_csv(self.facts_path, FACT_COLS)

    def health(self) -> pd.DataFrame:
        return read_csv(self.health_path, HEALTH_COLS)

    def upsert_identity(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        old = self.identity()
        new = pd.DataFrame(rows)
        for c in IDENTITY_COLS:
            if c not in new:
                new[c] = ""
        out = pd.concat([old, new[IDENTITY_COLS]], ignore_index=True)
        out = out.drop_duplicates(["isin", "source"], keep="last")
        write_csv(out, self.identity_path, ["isin", "source"])
        return len(new)

    def upsert_market(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        old = self.market()
        new = pd.DataFrame(rows)
        for c in MARKET_COLS:
            if c not in new:
                new[c] = ""
        out = pd.concat([old, new[MARKET_COLS]], ignore_index=True)
        out = out.drop_duplicates(["isin", "date", "source"], keep="last")
        write_csv(out, self.market_path, ["isin", "date", "source"])
        return len(new)

    def upsert_facts(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        old = self.facts()
        new = pd.DataFrame(rows)
        for c in FACT_COLS:
            if c not in new:
                new[c] = ""
        out = pd.concat([old, new[FACT_COLS]], ignore_index=True)
        out = out.drop_duplicates(["isin", "field", "source", "as_of"], keep="last")
        write_csv(out, self.facts_path, ["isin", "field", "as_of", "source"])
        return len(new)

    def add_health(self, source: str, status: str, attempted: int = 0, succeeded: int = 0,
                   failed: int = 0, quota_used: object = "", quota_left: object = "",
                   message: str = "") -> None:
        row = {
            "source": source, "status": status, "attempted": attempted, "succeeded": succeeded,
            "failed": failed, "quota_used": quota_used, "quota_left": quota_left,
            "message": message[:1000], "observed_at_utc": utcnow(),
        }
        out = pd.concat([self.health(), pd.DataFrame([row])], ignore_index=True)
        write_csv(out, self.health_path, ["observed_at_utc", "source"])


def load_universe(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 1429 or df["isin"].astype(str).nunique() != 1429:
        raise RuntimeError(f"V21.1 universe gate failed: rows={len(df)} isin={df['isin'].astype(str).nunique()}")
    for c in ["isin", "name", "yahoo_ticker", "euronext_symbol", "euronext_mic", "country"]:
        if c not in df:
            df[c] = ""
    return df


def priority_frame(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Rank rows for scarce free APIs: genuine missing fields first, then committee relevance."""
    out = df.copy()
    key = cfg["key_fields"]
    fields = key["fundamentals"] + key["valuation"] + key["prospective"]
    missing = pd.Series(0, index=out.index, dtype=float)
    for f in fields:
        if f in out:
            missing += (~out[f].map(is_observed)).astype(float)
        else:
            missing += 1.0
    score_mt = pd.to_numeric(out.get("score_mt", 0), errors="coerce").fillna(0)
    score_lt = pd.to_numeric(out.get("score_lt", 0), errors="coerce").fillna(0)
    selected = out.get("selection_mt", pd.Series(False, index=out.index)).astype(str).str.lower().isin({"true", "1"})
    selected |= out.get("selection_lt", pd.Series(False, index=out.index)).astype(str).str.lower().isin({"true", "1"})
    out["free_capture_priority"] = missing * 10.0 + (score_mt + score_lt) / 20.0 + selected.astype(float) * 30.0
    return out.sort_values("free_capture_priority", ascending=False, kind="stable")
