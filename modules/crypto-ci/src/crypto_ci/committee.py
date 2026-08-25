from __future__ import annotations

from collections import Counter
import csv
from io import StringIO
from typing import Any


def committee_payload(
    rows: list[dict[str, Any]],
    *,
    as_of: str,
    fingerprint: str,
    source_status: dict[str, Any],
    universe_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_counts = Counter(row["state"] for row in rows)
    actionable = [row for row in rows if row["state"] in {"STRONG_REVIEW", "READY_FOR_REVIEW", "WATCH"}]
    global_state = "READY_FOR_REVIEW" if any(row["state"] in {"STRONG_REVIEW", "READY_FOR_REVIEW"} for row in rows) else "REVIEW_WITH_WARNINGS"
    if not rows or all(row["state"].startswith("BLOCKED") or row["state"] == "WAIT_DATA" for row in rows):
        global_state = "BLOCKED"
    data_mode = "SYNTHETIC_TEST" if "SYNTHETIC" in source_status else "OBSERVED_PIT"
    if data_mode == "SYNTHETIC_TEST":
        global_state = "TEST_ONLY"
    return {
        "version": "CI_CRYPTO_V1.6.0_LIGHTWEIGHT_37_T1_T2_NO_STO_SAR",
        "as_of": as_of,
        "status": global_state,
        "data_mode": data_mode,
        "mode": "SHADOW_RESEARCH_ONLY",
        "asset_class": "CRYPTO_ONLY",
        "real_orders_enabled": False,
        "automatic_weight_promotion": False,
        "snapshot_fingerprint": fingerprint,
        "row_count": len(rows),
        "asset_count": len({row["asset_id"] for row in rows}),
        "universe": universe_audit or {},
        "state_counts": dict(sorted(state_counts.items())),
        "source_status": source_status,
        "priorities": actionable[:20],
        "rows": rows,
    }


def committee_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# CI Crypto V1",
        "",
        f"- Statut : **{payload['status']}**",
        f"- Mode de données : **{payload['data_mode']}**",
        f"- Snapshot UTC : `{payload['as_of']}`",
        f"- Empreinte : `{payload['snapshot_fingerprint']}`",
        f"- Cryptos traitées : **{payload['asset_count']}**",
        "- Mode : `SHADOW_RESEARCH_ONLY` — aucun ordre réel",
        "",
        "## Sélection par horizon",
        "",
        "| Horizon | Actif | Score | Couverture | Confiance | État | Timing T1/T2 | Risques |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in payload["rows"]:
        score = "NA" if row["score"] is None else f"{row['score']:.1f}"
        risks = ", ".join(row["universe_flags"] + row["hard_risk_flags"] + row["soft_risk_flags"]) or "—"
        lines.append(
            f"| {row['horizon']} | {row['symbol']} | {score} | {row['coverage']:.0%} | {row['confidence']:.1f} | {row['state']} | {row.get('timing_state', 'N/A')} | {risks} |"
        )
    lines.extend([
        "",
        "## Lecture",
        "",
        "Un état READY/STRONG signifie uniquement qu'un dossier a franchi les gates de données et mérite une revue humaine. "
        "Une source absente réduit la couverture ; elle ne devient jamais un signal négatif ou neutre.",
        "",
    ])
    return "\n".join(lines)


def committee_csv(rows: list[dict[str, Any]]) -> str:
    fields = ["asset_id", "symbol", "name", "horizon", "score", "coverage", "confidence", "confirmations", "state", "timing_state", "timing_quality", "timing_event_id", "timing_age_days", "missing_blocks", "risk_flags"]
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=";")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "asset_id": row["asset_id"],
            "symbol": row["symbol"],
            "name": row["name"],
            "horizon": row["horizon"],
            "score": row["score"],
            "coverage": row["coverage"],
            "confidence": row["confidence"],
            "confirmations": row["confirmations"],
            "state": row["state"],
            "timing_state": row.get("timing_state"),
            "timing_quality": row.get("timing_quality"),
            "timing_event_id": row.get("timing_event_id"),
            "timing_age_days": row.get("timing_age_days"),
            "missing_blocks": "|".join(row["missing_blocks"]),
            "risk_flags": "|".join(row["universe_flags"] + row["hard_risk_flags"] + row["soft_risk_flags"]),
        })
    return buffer.getvalue()
