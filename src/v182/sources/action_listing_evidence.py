from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import re
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKLIST = ROOT / "config" / "V21_17_ACTION_SHORT_HISTORY_WORKLIST.csv"
DEFAULT_FROZEN_EVIDENCE = ROOT / "config" / "V21_17_ACTION_LISTING_EVIDENCE_A.csv"
LISTING_CONFLICT_TOLERANCE_DAYS = 7
FROZEN_SOURCE_LABEL = "EURONEXT_OFFICIAL_IPO_SHOWCASE_V21_17"
FROZEN_VALIDATION_STATUS = "EXACT_ISIN_OFFICIAL_LISTING_DATE"


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "nan", "None", "<NA>", "N/A", "NA", "NULL"}:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    dayfirst = bool(re.search(r"\d{1,2}[/.]\d{1,2}[/.]\d{4}", text))
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _is_missing(value: Any) -> bool:
    return _parse_date(value) is None and str(value).strip() in {
        "",
        "nan",
        "None",
        "<NA>",
        "N/A",
        "NA",
        "NULL",
    }


def load_worklist(path: str | Path = DEFAULT_WORKLIST) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    required = {"isin", "ticker", "first_observed_date", "source_run", "initial_status"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"ACTION_LISTING_WORKLIST_MISSING_COLUMNS:{','.join(sorted(missing))}")
    if frame["isin"].isna().any() or frame["isin"].duplicated().any():
        raise ValueError("ACTION_LISTING_WORKLIST_ISIN_NOT_UNIQUE")
    return frame.copy()


def load_frozen_listing_evidence(path: str | Path = DEFAULT_FROZEN_EVIDENCE) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    required = {
        "isin",
        "ticker",
        "official_listing_date",
        "first_observed_date",
        "source_name",
        "source_url",
        "evidence_level",
        "validation_status",
        "source_run",
        "source_artifact_sha256",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"ACTION_LISTING_EVIDENCE_MISSING_COLUMNS:{','.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("ACTION_LISTING_EVIDENCE_EMPTY")
    normalized_isins = frame["isin"].fillna("").astype(str).str.strip().str.upper()
    if normalized_isins.eq("").any() or normalized_isins.duplicated().any():
        raise ValueError("ACTION_LISTING_EVIDENCE_ISIN_NOT_UNIQUE")
    frame = frame.copy()
    frame["isin"] = normalized_isins
    if set(frame["evidence_level"].fillna("").astype(str).str.strip()) != {"A"}:
        raise ValueError("ACTION_LISTING_EVIDENCE_NOT_LEVEL_A")
    if set(frame["validation_status"].fillna("").astype(str).str.strip()) != {FROZEN_VALIDATION_STATUS}:
        raise ValueError("ACTION_LISTING_EVIDENCE_INVALID_STATUS")
    for _, row in frame.iterrows():
        official = _parse_date(row.get("official_listing_date"))
        first_observed = _parse_date(row.get("first_observed_date"))
        if official is None or first_observed is None:
            raise ValueError(f"ACTION_LISTING_EVIDENCE_INVALID_DATE:{row['isin']}")
        if official > first_observed + timedelta(days=LISTING_CONFLICT_TOLERANCE_DAYS):
            raise ValueError(f"ACTION_LISTING_EVIDENCE_AFTER_FIRST_OBSERVATION:{row['isin']}")
        digest = str(row.get("source_artifact_sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"ACTION_LISTING_EVIDENCE_INVALID_ARTIFACT_SHA256:{row['isin']}")
        if not str(row.get("source_url") or "").strip().startswith("https://live.euronext.com/"):
            raise ValueError(f"ACTION_LISTING_EVIDENCE_INVALID_SOURCE_URL:{row['isin']}")
    return frame


def apply_frozen_listing_evidence(
    actions_df: pd.DataFrame,
    path: str | Path = DEFAULT_FROZEN_EVIDENCE,
) -> dict[str, Any]:
    """Apply frozen Euronext listing dates by exact ISIN, fail-closed on conflicts."""
    if "isin" not in actions_df.columns:
        raise ValueError("ACTION_LISTING_EVIDENCE_TARGET_MISSING_ISIN")
    evidence = load_frozen_listing_evidence(path)
    for column in (
        "listing_or_launch_date",
        "listing_or_launch_date_source",
        "listing_or_launch_date_source_url",
        "listing_or_launch_date_evidence_level",
        "listing_or_launch_date_validation_status",
    ):
        if column not in actions_df.columns:
            actions_df[column] = pd.NA

    target_isins = actions_df["isin"].fillna("").astype(str).str.strip().str.upper()
    applied = 0
    already_matching = 0
    unmatched_evidence = 0
    for _, evidence_row in evidence.iterrows():
        isin = str(evidence_row["isin"]).strip().upper()
        matches = actions_df.index[target_isins.eq(isin)].tolist()
        if not matches:
            unmatched_evidence += 1
            continue
        official = _parse_date(evidence_row.get("official_listing_date"))
        if official is None:
            raise ValueError(f"ACTION_LISTING_EVIDENCE_INVALID_DATE:{isin}")
        for idx in matches:
            current_raw = actions_df.at[idx, "listing_or_launch_date"]
            current = _parse_date(current_raw)
            if not _is_missing(current_raw):
                if current is None:
                    raise ValueError(f"ACTION_LISTING_EVIDENCE_TARGET_DATE_UNPARSABLE:{isin}")
                if current != official:
                    raise ValueError(
                        f"ACTION_LISTING_EVIDENCE_CONFLICT:{isin}:{current.isoformat()}:{official.isoformat()}"
                    )
                already_matching += 1
            else:
                actions_df.at[idx, "listing_or_launch_date"] = official.isoformat()
                applied += 1
            actions_df.at[idx, "listing_or_launch_date_source"] = FROZEN_SOURCE_LABEL
            actions_df.at[idx, "listing_or_launch_date_source_url"] = str(evidence_row["source_url"]).strip()
            actions_df.at[idx, "listing_or_launch_date_evidence_level"] = "A"
            actions_df.at[idx, "listing_or_launch_date_validation_status"] = FROZEN_VALIDATION_STATUS

    return {
        "status": "SUCCESS",
        "source": FROZEN_SOURCE_LABEL,
        "evidence_rows": int(len(evidence)),
        "applied": applied,
        "already_matching": already_matching,
        "unmatched_evidence": unmatched_evidence,
        "synthetic_history_created": False,
        "calibration_eligibility_changed": False,
    }
