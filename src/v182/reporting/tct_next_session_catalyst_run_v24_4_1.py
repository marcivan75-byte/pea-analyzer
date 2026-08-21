from __future__ import annotations

from pathlib import Path
import json

from v182.features import tct_catalyst_context_v24_4_1 as feature
from v182.reporting import tct_next_session_catalyst_run as base
from v182.sources import tct_catalyst_news_v24_4_1 as news_source


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json"
VERSION = feature.VERSION

# Reuse the audited V24.4 runner mechanics while replacing only the versioned
# scoring, news classification and configuration primitives.
base.CONFIG = CONFIG
base.VERSION = VERSION
base.catalyst_window = feature.catalyst_window
base.infer_phase = feature.infer_phase
base.select_catalyst_candidates = feature.select_catalyst_candidates
base.score_candidate = feature.score_candidate
base.fetch_candidate_news = news_source.fetch_candidate_news


def run(root: Path = ROOT, *, phase: str | None = None, now=None) -> dict:
    payload = base.run(root=root, phase=phase, now=now)

    old_ranked = root / "outputs" / "daily_tct_ct" / "TCT_NEXT_SESSION_CATALYST_V24_4_0.csv"
    new_ranked = root / "outputs" / "daily_tct_ct" / "TCT_NEXT_SESSION_CATALYST_V24_4_1.csv"
    if old_ranked.exists():
        new_ranked.parent.mkdir(parents=True, exist_ok=True)
        old_ranked.replace(new_ranked)
        if isinstance(payload.get("outputs"), dict):
            payload["outputs"]["ranked_candidates"] = str(new_ranked.relative_to(root))

    old_audit = root / "outputs" / "audit" / "TCT_NEXT_SESSION_CATALYST_V24_4_0_AUDIT.json"
    new_audit = root / "outputs" / "audit" / "TCT_NEXT_SESSION_CATALYST_V24_4_1_AUDIT.json"
    new_audit.parent.mkdir(parents=True, exist_ok=True)
    new_audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if old_audit.exists():
        old_audit.unlink()
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
