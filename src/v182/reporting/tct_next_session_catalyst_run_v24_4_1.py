from __future__ import annotations

from pathlib import Path
import json

from v182.features import tct_catalyst_context_v24_4_1 as feature
from v182.reporting.tct_next_session_catalyst_engine import run_engine
from v182.sources import tct_catalyst_news_v24_4_1 as news_source


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json"
VERSION = feature.VERSION


def _fetch_news_compat(candidates, *, start_utc, end_utc, phase, cfg, budget_seconds=None):
    del budget_seconds
    return news_source.fetch_candidate_news(
        candidates,
        start_utc=start_utc,
        end_utc=end_utc,
        phase=phase,
        cfg=cfg,
    )


def run(root: Path = ROOT, *, phase: str | None = None, now=None) -> dict:
    """Run historical V24.4.1 semantics without mutating V24.4.0 globals."""
    return run_engine(
        root=root,
        config_filename=CONFIG,
        version=VERSION,
        catalyst_window_fn=feature.catalyst_window,
        infer_phase_fn=feature.infer_phase,
        select_candidates_fn=feature.select_catalyst_candidates,
        score_candidate_fn=feature.score_candidate,
        fetch_news_fn=_fetch_news_compat,
        phase=phase,
        now=now,
        output_filename="TCT_NEXT_SESSION_CATALYST_V24_4_1.csv",
        audit_filename="TCT_NEXT_SESSION_CATALYST_V24_4_1_AUDIT.json",
        android_filename="ANDROID_TCT_NEXT_SESSION_CATALYST.md",
    )


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
