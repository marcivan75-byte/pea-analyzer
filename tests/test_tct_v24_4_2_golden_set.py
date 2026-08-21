from __future__ import annotations

from pathlib import Path
import json

from v182.sources.tct_catalyst_news_v24_4_2 import classify_headline


ROOT = Path(__file__).resolve().parents[1]


def test_v242_catalyst_golden_set():
    cfg = json.loads((ROOT / "config" / "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    cases = json.loads((ROOT / "tests" / "fixtures" / "tct_v24_4_2_catalyst_golden_set.json").read_text(encoding="utf-8"))
    assert len(cases) >= 15
    failures = []
    for case in cases:
        predicted = classify_headline(case["headline"], cfg)[0]
        if predicted != case["event"]:
            failures.append((case["headline"], case["event"], predicted))
    assert not failures, failures
