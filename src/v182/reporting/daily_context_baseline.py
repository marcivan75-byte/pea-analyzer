from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASELINE_DIR = Path("state/provenance/daily_fast")
ACTIONS_FILE = "ACTIONS_ENRICHED_BASELINE.parquet"
ETF_FILE = "ETF_ENRICHED_BASELINE.parquet"
META_FILE = "BASELINE_META.json"
VERSION = "DAILY_FAST_BASELINE_V1"


def _now_utc() -> datetime:
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


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def _atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _validate_identity(frame: pd.DataFrame, label: str) -> None:
    if frame.empty:
        raise RuntimeError(f"DAILY_FAST_{label}_BASELINE_EMPTY")
    if "isin" not in frame:
        raise RuntimeError(f"DAILY_FAST_{label}_BASELINE_ISIN_MISSING")
    isin = frame["isin"].astype(str).str.strip()
    if isin.eq("").any() or isin.duplicated().any():
        raise RuntimeError(f"DAILY_FAST_{label}_BASELINE_IDENTITY_INVALID")


def publish_context_baseline(
    actions: pd.DataFrame,
    etfs: pd.DataFrame,
    root: Path = ROOT,
    *,
    full_refresh: bool,
    profile: str,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Persist the complete enriched masters used as the next daily tactical starting point.

    Daily writes preserve the timestamp of the last full/weekly slow-source refresh.
    This lets the fast path carry forward all governed criteria without pretending that
    slow qualitative sources were refreshed daily.
    """
    _validate_identity(actions, "ACTION")
    _validate_identity(etfs, "ETF")
    current = (now or _now_utc()).astimezone(timezone.utc)
    directory = root / BASELINE_DIR
    meta_path = directory / META_FILE
    previous: dict = {}
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}
    last_full = current.isoformat() if full_refresh else previous.get("last_full_refresh_utc")
    if not last_full:
        raise RuntimeError("DAILY_FAST_BASELINE_FULL_REFRESH_TIMESTAMP_MISSING")

    _atomic_parquet(actions, directory / ACTIONS_FILE)
    _atomic_parquet(etfs, directory / ETF_FILE)
    payload = {
        "version": VERSION,
        "last_snapshot_utc": current.isoformat(),
        "last_full_refresh_utc": last_full,
        "snapshot_profile": str(profile),
        "run_id": run_id,
        "full_refresh_this_write": bool(full_refresh),
        "actions_rows": int(len(actions)),
        "actions_unique_isins": int(actions["isin"].astype(str).nunique()),
        "actions_columns": int(len(actions.columns)),
        "etf_rows": int(len(etfs)),
        "etf_unique_isins": int(etfs["isin"].astype(str).nunique()),
        "etf_columns": int(len(etfs.columns)),
        "slow_source_freshness_extended_by_daily_write": False,
        "score_logic_changed": False,
        "decision_logic_changed": False,
    }
    _atomic_json(payload, meta_path)
    return payload


def load_context_baseline(
    root: Path = ROOT,
    *,
    max_full_age_days: float = 8.0,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    current = (now or _now_utc()).astimezone(timezone.utc)
    directory = root / BASELINE_DIR
    meta_path = directory / META_FILE
    actions_path = directory / ACTIONS_FILE
    etf_path = directory / ETF_FILE
    if not meta_path.exists() or not actions_path.exists() or not etf_path.exists():
        raise RuntimeError("DAILY_FAST_BASELINE_MISSING")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("DAILY_FAST_BASELINE_META_INVALID") from exc
    if meta.get("version") != VERSION:
        raise RuntimeError("DAILY_FAST_BASELINE_VERSION_MISMATCH")
    last_full = _parse_utc(meta.get("last_full_refresh_utc"))
    if last_full is None:
        raise RuntimeError("DAILY_FAST_BASELINE_FULL_REFRESH_TIMESTAMP_INVALID")
    age_days = max(0.0, (current - last_full).total_seconds() / 86400.0)
    if age_days > float(max_full_age_days):
        raise RuntimeError(f"DAILY_FAST_BASELINE_STALE:{age_days:.2f}d")
    try:
        actions = pd.read_parquet(actions_path)
        etfs = pd.read_parquet(etf_path)
    except Exception as exc:
        raise RuntimeError(f"DAILY_FAST_BASELINE_READ_FAILED:{type(exc).__name__}") from exc
    _validate_identity(actions, "ACTION")
    _validate_identity(etfs, "ETF")
    if int(meta.get("actions_rows", -1)) != len(actions) or int(meta.get("etf_rows", -1)) != len(etfs):
        raise RuntimeError("DAILY_FAST_BASELINE_ROW_COUNT_MISMATCH")
    result = dict(meta)
    result["full_refresh_age_days"] = round(age_days, 4)
    result["max_full_age_days"] = float(max_full_age_days)
    return actions, etfs, result


def publish_from_outputs(root: Path = ROOT, *, profile: str = "WEEKLY_FULL_COMMITTEE") -> dict:
    actions_path = root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    etf_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not actions_path.exists() or not etf_path.exists():
        raise RuntimeError("DAILY_FAST_BASELINE_OUTPUT_MASTERS_MISSING")
    actions = pd.read_csv(actions_path, sep=";", encoding="utf-8-sig", low_memory=False)
    etfs = pd.read_csv(etf_path, sep=";", encoding="utf-8-sig", low_memory=False)
    return publish_context_baseline(actions, etfs, root, full_refresh=True, profile=profile)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--publish-from-outputs", action="store_true")
    parser.add_argument("--profile", default="WEEKLY_FULL_COMMITTEE")
    args = parser.parse_args()
    root = Path(args.root)
    if not args.publish_from_outputs:
        raise SystemExit("Use --publish-from-outputs")
    print(json.dumps(publish_from_outputs(root, profile=args.profile), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
