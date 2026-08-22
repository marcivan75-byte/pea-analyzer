from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading

import pandas as pd

from v182.reporting import boursorama_shadow_run
from v182.reporting import run as enrichment_run
from v182.sources.boursorama_public import collect_action_snapshots_cached


CONSENSUS = """
<table>
<tr><th>Opinion</th><th>1 mois</th><th>Actuel</th></tr>
<tr><td>1. Acheter</td><td>1</td><td>1</td></tr>
<tr><td>2. Renforcer</td><td>0</td><td>0</td></tr>
<tr><td>3. Conserver</td><td>0</td><td>0</td></tr>
<tr><td>4. Alléger</td><td>0</td><td>0</td></tr>
<tr><td>5. Vendre</td><td>0</td><td>0</td></tr>
</table>
<div>Potentiel : 10,0%</div>
"""


class FakeResponse:
    text = CONSENSUS

    def raise_for_status(self):
        return None


def test_boursorama_network_latency_can_overlap_without_faster_request_starts(tmp_path: Path):
    rows = pd.DataFrame(
        [
            {"isin": "FR0000000001", "yahoo_ticker": "AAA.PA"},
            {"isin": "FR0000000002", "yahoo_ticker": "BBB.PA"},
        ]
    )
    barrier = threading.Barrier(2)

    def fetcher(url, timeout):
        barrier.wait(timeout=2)
        return FakeResponse()

    result = collect_action_snapshots_cached(
        rows,
        tmp_path / "cache.json",
        refresh_budget=2,
        request_start_interval_seconds=0,
        max_workers=2,
        fetcher=fetcher,
        now=datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc),
    )
    assert result.metrics["live_refresh_requested"] == 2
    assert result.metrics["live_refresh_success"] == 2
    assert result.metrics["max_workers"] == 2


def test_wave04_and_boursorama_shadow_are_true_parallel_peers(monkeypatch):
    barrier = threading.Barrier(2)

    def fake_wave4(actions, cfg):
        barrier.wait(timeout=2)
        return ([{"field": "x"}], [])

    def fake_shadow(actions, root, profile=None):
        barrier.wait(timeout=2)
        return {"status": "SHADOW_ONLY_NO_DECISION_INFLUENCE", "decision_influence": False}

    monkeypatch.setattr(enrichment_run.waves, "wave4_info_actions", fake_wave4)
    monkeypatch.setattr(boursorama_shadow_run, "run_for_actions", fake_shadow)
    wave4, shadow = enrichment_run._collect_wave4_boursorama_shadow_parallel(
        pd.DataFrame([{"isin": "A"}]), {}, "FULL"
    )
    assert wave4 == ([{"field": "x"}], [])
    assert shadow["decision_influence"] is False


def test_boursorama_shadow_failure_never_blocks_existing_wave04(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(enrichment_run.waves, "wave4_info_actions", lambda actions, cfg: ([], []))

    def fail_shadow(actions, root, profile=None):
        raise RuntimeError("shadow only")

    monkeypatch.setattr(boursorama_shadow_run, "run_for_actions", fail_shadow)
    monkeypatch.setattr(enrichment_run, "OUTPUTS", tmp_path)
    (tmp_path / "audit").mkdir(parents=True, exist_ok=True)
    wave4, shadow = enrichment_run._collect_wave4_boursorama_shadow_parallel(
        pd.DataFrame([{"isin": "A"}]), {}, "FULL"
    )
    assert wave4 == ([], [])
    assert shadow["status"] == "FAILED_SHADOW_NON_BLOCKING"
    assert shadow["decision_influence"] is False
    assert (tmp_path / "audit" / "BOURSORAMA_PUBLIC_SHADOW_METRICS.json").exists()
