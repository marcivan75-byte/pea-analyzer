from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

import pandas as pd

from v182.reporting import tct_v24_4_pit_lineage as base


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json"
VERSION = "TCT_V24.4.1_PIT_LINEAGE_V2"
FINGERPRINT_ALGORITHM = "TCT_PIT_SHA256_CANONICAL_V2"

# Deliberately fixed. Adding unrelated columns to the ledger must never make a
# historical snapshot look modified. Only prediction-critical fields are hashed.
PREDICTION_FIELDS = (
    "version",
    "phase",
    "isin",
    "yahoo_ticker",
    "as_of_date",
    "reference_close",
    "source_tct_decision",
    "source_tct_setup",
    "source_t1_quality",
    "source_t2_quality",
    "entry_state",
    "entry_score",
    "entry_confirmation_count",
    "exit_state",
    "exit_risk_score",
    "atr14_pct",
    "range_expansion",
    "days_to_earnings",
    "movement_potential_score",
    "movement_potential_raw_score",
    "movement_potential_coverage",
    "direction_bias_score",
    "direction_bias_raw_score",
    "direction_coverage",
    "data_quality_state",
    "catalyst_state",
    "news_magnitude_score",
    "news_direction_score",
    "news_confidence",
    "news_article_count",
    "news_independent_sources",
    "news_event_types",
    "news_top_headlines",
    "news_window_start_utc",
    "news_window_end_utc",
    "news_error",
    "technical_impulse_score",
    "technical_direction_score",
    "known_event_proximity_score",
    "global_market_shock_score",
    "global_risk_on_score",
    "news_technical_conflict",
    "snapshot_generated_at_utc",
    "snapshot_window_start_utc",
    "snapshot_window_end_utc",
    "snapshot_key",
)


def prediction_fingerprint_v2(row: pd.Series) -> str:
    payload = {key: base._normalise(row.get(key)) for key in PREDICTION_FIELDS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


base.CONFIG = CONFIG
base.VERSION = VERSION
base.prediction_fingerprint = prediction_fingerprint_v2


def run(root: Path = ROOT) -> dict:
    payload = base.run(root=root)
    payload["fingerprint_algorithm"] = FINGERPRINT_ALGORITHM
    payload["fingerprint_fields"] = list(PREDICTION_FIELDS)

    old_audit = root / "outputs" / "audit" / "TCT_V24_4_0_PIT_LINEAGE_AUDIT.json"
    new_audit = root / "outputs" / "audit" / "TCT_V24_4_1_PIT_LINEAGE_AUDIT.json"
    new_audit.parent.mkdir(parents=True, exist_ok=True)
    new_audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if old_audit.exists():
        old_audit.unlink()
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
