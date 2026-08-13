from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

SNAPSHOT_SPECS = {
    "Euronext Live": {
        "path": "inputs/source_snapshots/EURONEXT_LIVE.csv",
        "universe": "BOTH",
        "evidence": "A",
    },
    "Boursorama": {
        "path": "inputs/source_snapshots/BOURSORAMA.csv",
        "universe": "BOTH",
        "evidence": "B",
    },
    "Amundi ETF": {
        "path": "inputs/source_snapshots/AMUNDI_ETF.csv",
        "universe": "ETF",
        "evidence": "A",
    },
    "BNPP ETF": {
        "path": "inputs/source_snapshots/BNPP_ETF.csv",
        "universe": "ETF",
        "evidence": "A",
    },
}

META_COLUMNS = {
    "isin", "source_url", "source_date", "source_name", "notes", "captured_at", "evidence_level",
}


def _missing(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"", "nan", "none", "n/a", "na", "unknown"}


def load_snapshot(
    root: Path,
    source_name: str,
    actions: pd.DataFrame,
    etfs: pd.DataFrame,
) -> tuple[list[dict], list[dict], dict]:
    spec = SNAPSHOT_SPECS[source_name]
    path = root / spec["path"]
    if not path.exists():
        return [], [{"source": source_name, "reason": "ATTRIBUTED_SNAPSHOT_MISSING", "path": spec["path"]}], {
            "mode": "ATTRIBUTED_SNAPSHOT_LOADER", "path": spec["path"], "rows": 0,
        }
    try:
        frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    except Exception as exc:
        return [], [{"source": source_name, "reason": type(exc).__name__, "detail": str(exc)[:180]}], {
            "mode": "ATTRIBUTED_SNAPSHOT_LOADER", "path": spec["path"], "rows": 0,
        }
    if frame.empty:
        return [], [{"source": source_name, "reason": "ATTRIBUTED_SNAPSHOT_EMPTY", "path": spec["path"]}], {
            "mode": "ATTRIBUTED_SNAPSHOT_LOADER", "path": spec["path"], "rows": 0,
        }
    required = {"isin", "source_url", "source_date"}
    missing_cols = required - set(frame.columns)
    if missing_cols:
        return [], [{"source": source_name, "reason": "SNAPSHOT_SCHEMA_MISSING", "columns": ",".join(sorted(missing_cols))}], {
            "mode": "ATTRIBUTED_SNAPSHOT_LOADER", "path": spec["path"], "rows": len(frame),
        }

    action_isins = set(actions["isin"].astype(str).str.strip()) if "isin" in actions.columns else set()
    etf_isins = set(etfs["isin"].astype(str).str.strip()) if "isin" in etfs.columns else set()
    observations: list[dict] = []
    failures: list[dict] = []
    accepted_rows = 0
    now = datetime.now(timezone.utc).isoformat()
    for _, row in frame.iterrows():
        isin = str(row.get("isin") or "").strip()
        source_url = str(row.get("source_url") or "").strip()
        source_date = str(row.get("source_date") or "").strip()[:10]
        if not isin or not source_url or not source_date:
            failures.append({"source": source_name, "isin": isin, "reason": "ATTRIBUTION_INCOMPLETE"})
            continue
        universe = "ACTION" if isin in action_isins else "ETF" if isin in etf_isins else ""
        if not universe:
            failures.append({"source": source_name, "isin": isin, "reason": "ISIN_OUTSIDE_CANONICAL_UNIVERSE"})
            continue
        if spec["universe"] == "ETF" and universe != "ETF":
            failures.append({"source": source_name, "isin": isin, "reason": "WRONG_UNIVERSE"})
            continue
        accepted_rows += 1
        for field in frame.columns:
            if field in META_COLUMNS:
                continue
            value = row.get(field)
            if _missing(value):
                continue
            observations.append({
                "universe": universe,
                "isin": isin,
                "field": field,
                "value": value,
                "source": source_name,
                "source_url": source_url,
                "collected_at": now,
                "as_of": source_date,
                "evidence_level": spec["evidence"],
                "validation_status": "ATTRIBUTED_SNAPSHOT",
            })
    return observations, failures, {
        "mode": "ATTRIBUTED_SNAPSHOT_LOADER",
        "path": spec["path"],
        "rows": int(len(frame)),
        "accepted_rows": accepted_rows,
        "observations": len(observations),
    }


def load_all_snapshots(root: Path, actions: pd.DataFrame, etfs: pd.DataFrame) -> tuple[list[dict], list[dict], dict]:
    observations: list[dict] = []
    failures: list[dict] = []
    stats = {}
    for source_name in SNAPSHOT_SPECS:
        obs, failed, source_stats = load_snapshot(root, source_name, actions, etfs)
        observations.extend(obs)
        failures.extend(failed)
        stats[source_name] = source_stats
    return observations, failures, stats
