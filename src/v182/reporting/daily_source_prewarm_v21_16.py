from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.reporting.selected_source_enrichment import enrich_selected_rows, select_preselected_rows

ROOT = Path(__file__).resolve().parents[3]
SEED_PATH = Path("state/provenance/DAILY_SOURCE_PREWARM_V1.csv")
WEEKLY_SEED_PATH = Path("state/provenance/WEEKLY_SOURCE_PREWARM_V1.csv")
VERSION = "SOURCE_PREWARM_V21_16_2"
MAX_PERSISTED = 40
MAX_PREWARM = 20
MAX_SEED_AGE_DAYS = 8.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def persist_seed(
    rows: pd.DataFrame,
    root: Path = ROOT,
    *,
    now: datetime | None = None,
    seed_path: Path = SEED_PATH,
    max_persisted: int = MAX_PERSISTED,
) -> dict:
    current = (now or _utc_now()).astimezone(timezone.utc)
    selected = select_preselected_rows(rows, max_unique_instruments=max(0, int(max_persisted)))
    if selected.empty:
        return {"status": "NO_PRESELECTED_ROWS", "version": VERSION, "persisted_rows": 0}
    fields = [
        c
        for c in (
            "asset_class",
            "horizon",
            "isin",
            "name",
            "decision",
            "score",
            "selected_rank",
            "yahoo_ticker",
            "long_name_yf",
            "investing_url",
            "investing_technical_url",
            "boursorama_code",
        )
        if c in selected.columns
    ]
    seed = selected[fields].copy()
    seed["prewarm_seed_generated_at_utc"] = current.isoformat()
    path = root / seed_path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    seed.to_csv(temporary, sep=";", index=False, encoding="utf-8-sig")
    temporary.replace(path)
    return {
        "status": "SUCCESS",
        "version": VERSION,
        "persisted_rows": int(len(seed)),
        "persisted_unique_isins": int(seed["isin"].astype(str).nunique()),
        "path": str(seed_path),
        "max_persisted_unique_isins": int(max_persisted),
        "network_calls": 0,
    }


def prewarm(
    root: Path = ROOT,
    *,
    now: datetime | None = None,
    seed_path: Path = SEED_PATH,
    max_prewarm: int = MAX_PREWARM,
    max_seed_age_days: float = MAX_SEED_AGE_DAYS,
    profile: str = "DAILY_SOURCE_PREWARM",
) -> dict:
    current = (now or _utc_now()).astimezone(timezone.utc)
    path = root / seed_path
    if not path.exists():
        return {"status": "NO_SEED", "version": VERSION, "network_attempted": False, "path": str(seed_path)}
    try:
        seed = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        return {
            "status": "INVALID_SEED",
            "version": VERSION,
            "network_attempted": False,
            "path": str(seed_path),
            "error": type(exc).__name__,
        }
    if seed.empty or "isin" not in seed.columns:
        return {"status": "EMPTY_SEED", "version": VERSION, "network_attempted": False, "path": str(seed_path)}
    stamps = seed.get("prewarm_seed_generated_at_utc", pd.Series(dtype=object)).dropna().astype(str)
    stamp = _parse_utc(stamps.max()) if not stamps.empty else None
    if stamp is None:
        return {"status": "SEED_TIMESTAMP_MISSING", "version": VERSION, "network_attempted": False, "path": str(seed_path)}
    age_days = max(0.0, (current - stamp).total_seconds() / 86400.0)
    if age_days > float(max_seed_age_days):
        return {
            "status": "SEED_TOO_OLD",
            "version": VERSION,
            "network_attempted": False,
            "path": str(seed_path),
            "seed_age_days": round(age_days, 4),
            "max_seed_age_days": float(max_seed_age_days),
        }

    seed = select_preselected_rows(seed, max_unique_instruments=max(0, int(max_prewarm)))
    if seed.empty:
        return {"status": "NO_ELIGIBLE_SEED_ROWS", "version": VERSION, "network_attempted": False, "path": str(seed_path)}
    _enriched, context = enrich_selected_rows(
        seed,
        root,
        profile=profile,
        network_policy="LIVE_IF_DUE",
        persist_outputs=False,
    )
    return {
        "status": "SUCCESS",
        "version": VERSION,
        "profile": profile,
        "path": str(seed_path),
        "network_attempted": True,
        "seed_age_days": round(age_days, 4),
        "seed_rows_considered": int(len(seed)),
        "seed_unique_isins_considered": int(seed["isin"].astype(str).nunique()),
        "max_prewarm_unique_isins": int(max_prewarm),
        "current_decision_coverage_reduced": False,
        "current_gate_still_completes_new_candidates": True,
        "source_context": context,
    }


if __name__ == "__main__":
    print(json.dumps(prewarm(), ensure_ascii=False, indent=2, default=str))
