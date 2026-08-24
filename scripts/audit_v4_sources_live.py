from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.reporting import selected_source_enrichment as identity
from v182.reporting import selected_source_enrichment_v4 as sources


ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def run(candidate_path: Path, root: Path = ROOT) -> dict:
    rows = _read(candidate_path)
    if len(rows) > 40:
        raise ValueError(f"BOUNDED_POOL_EXCEEDED:{len(rows)}")
    actions = _read(root / "inputs/V18.2_PEA_ACTIONS_MASTER.csv")
    etfs = _read(root / "inputs/V18.2_PEA_ETF_MASTER.csv")
    rows = identity.attach_master_identity(rows, actions, etfs)
    enriched, source_payload = sources.enrich_selected_rows_v4(rows, root=root, profile="WEEKLY_V4_LIVE_AUDIT")

    tv = source_payload.get("tradingview", {})
    tv_metrics = tv.get("metrics", {}) if isinstance(tv, dict) else {}
    boursorama = source_payload.get("boursorama", {})
    failure_path = root / "outputs/source_context/WEEKLY_V4_LIVE_AUDIT_V4_SOURCE_FAILURES.csv"
    failures = _read(failure_path) if failure_path.exists() and failure_path.stat().st_size else pd.DataFrame()
    reasons = Counter(failures.get("reason", pd.Series(dtype=str)).dropna().astype(str))
    required = {
        "bounded_pool": len(rows) <= 40,
        "investing_disabled": source_payload.get("investing", {}).get("status") == "DISABLED_FOR_V4",
        "source_cannot_create_candidate": source_payload.get("source_can_create_candidate") is False,
        "raw_html_not_persisted": source_payload.get("raw_html_persisted") is False,
        "tradingview_identity_fail_closed": tv_metrics.get("identity_fail_closed") is True,
        "tradingview_score_influence_zero": float(tv_metrics.get("score_influence", 1.0)) == 0.0,
    }
    payload = {
        "status": "PASS" if all(required.values()) else "FAIL",
        "version": "WEEKLY_V4_SOURCE_LIVE_AUDIT_1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_path": str(candidate_path),
        "input_rows": int(len(rows)),
        "unique_isins": int(rows["isin"].nunique()),
        "required_checks": required,
        "tradingview": tv,
        "boursorama": boursorama,
        "failure_reasons": dict(sorted(reasons.items())),
        "enriched_rows": int(len(enriched)),
    }
    target = root / "outputs/audit/WEEKLY_V4_SOURCE_LIVE_AUDIT.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("candidate_path", type=Path)
    args = parser.parse_args()
    payload = run(args.candidate_path.resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
