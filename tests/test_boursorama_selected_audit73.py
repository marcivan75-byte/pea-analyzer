from datetime import datetime, timedelta, timezone
import json

import pandas as pd

from v182.sources.boursorama_selected_audit73 import (
    collect_selected_action_context_cached,
    load_audit73_pit_observations,
)

HTML = """
<html><body>
<table>
<tr><th>Consensus</th><th>3 mois</th><th>2 mois</th><th>1 mois</th><th>7 jours</th><th>22/08/2026</th></tr>
<tr><td>Acheter</td><td>13</td><td>13</td><td>13</td><td>15</td><td>15</td></tr>
<tr><td>Renforcer</td><td>5</td><td>5</td><td>5</td><td>4</td><td>4</td></tr>
<tr><td>Conserver</td><td>3</td><td>3</td><td>3</td><td>3</td><td>4</td></tr>
<tr><td>Alléger</td><td>1</td><td>1</td><td>1</td><td>0</td><td>0</td></tr>
<tr><td>Vendre</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>Note médiane</td><td>1,64</td><td>1,64</td><td>1,64</td><td>1,45</td><td>1,52</td></tr>
<tr><td>Objectif de cours médian</td><td>197,50</td><td>197,00</td><td>181,59</td><td>193,12</td><td>196,09</td></tr>
<tr><td>Potentiel</td><td>10,0%</td><td>10,0%</td><td>8,0%</td><td>12,0%</td><td>14,38%</td></tr>
</table>
<div>Ouverture 170,00 Clôture veille 169,00 + Haut 172,00 + Bas 168,00 Volume 123 456</div>
</body></html>
"""


class Response:
    def __init__(self, text=HTML, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP_{self.status_code}")


def rows():
    return pd.DataFrame([
        {
            "isin": "FR0000120271",
            "asset_class": "ACTION",
            "boursorama_code": "1rPTTE",
            "yahoo_ticker": "TTE.PA",
        }
    ])


def fetch_ok(url: str, *, timeout: float):
    return Response()


def test_live_consensus_refresh_appends_one_audit73_capture(tmp_path):
    cache = tmp_path / "b.json"
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    result = collect_selected_action_context_cached(
        rows(), cache, dynamic_ttl_hours=8, deep_ttl_hours=168,
        refresh_budget=5, request_start_interval_seconds=0,
        max_workers=1, fetcher=fetch_ok, now=now,
    )
    payload = json.loads(cache.read_text(encoding="utf-8"))
    entry = payload["entries"]["FR0000120271"]
    history = entry["audit73_consensus_history"]
    assert len(history) == 1
    assert len(history[0]["rows"]) == 5
    assert history[0]["captured_at_utc"] == "2026-08-22T18:00:00+00:00"
    assert result.metrics["audit73_captures_appended"] == 1
    assert result.metrics["audit73_rows_appended"] == 5


def test_cache_hit_does_not_manufacture_new_pit_observation(tmp_path):
    cache = tmp_path / "b.json"
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    collect_selected_action_context_cached(
        rows(), cache, dynamic_ttl_hours=8, deep_ttl_hours=168,
        refresh_budget=5, request_start_interval_seconds=0,
        max_workers=1, fetcher=fetch_ok, now=now,
    )
    second = collect_selected_action_context_cached(
        rows(), cache, dynamic_ttl_hours=8, deep_ttl_hours=168,
        refresh_budget=5, request_start_interval_seconds=0,
        max_workers=1, fetcher=fetch_ok, now=now + timedelta(hours=1),
    )
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert len(payload["entries"]["FR0000120271"]["audit73_consensus_history"]) == 1
    assert second.metrics["audit73_captures_appended"] == 0


def test_same_html_at_later_real_refresh_is_a_distinct_pit_capture(tmp_path):
    cache = tmp_path / "b.json"
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    for stamp in (now, now + timedelta(hours=9)):
        collect_selected_action_context_cached(
            rows(), cache, dynamic_ttl_hours=8, deep_ttl_hours=168,
            refresh_budget=5, request_start_interval_seconds=0,
            max_workers=1, fetcher=fetch_ok, now=stamp,
        )
    payload = json.loads(cache.read_text(encoding="utf-8"))
    history = payload["entries"]["FR0000120271"]["audit73_consensus_history"]
    assert len(history) == 2
    assert history[0]["consensus_sha256"] == history[1]["consensus_sha256"]
    assert history[0]["captured_at_utc"] != history[1]["captured_at_utc"]


def test_failed_live_refresh_does_not_append_history(tmp_path):
    cache = tmp_path / "b.json"
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    collect_selected_action_context_cached(
        rows(), cache, dynamic_ttl_hours=8, deep_ttl_hours=168,
        refresh_budget=5, request_start_interval_seconds=0,
        max_workers=1, fetcher=fetch_ok, now=now,
    )

    def failed(url: str, *, timeout: float):
        return Response("error", 503)

    result = collect_selected_action_context_cached(
        rows(), cache, dynamic_ttl_hours=8, deep_ttl_hours=168,
        refresh_budget=5, request_start_interval_seconds=0,
        max_workers=1, fetcher=failed, now=now + timedelta(hours=9),
    )
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert len(payload["entries"]["FR0000120271"]["audit73_consensus_history"]) == 1
    assert result.metrics["audit73_captures_appended"] == 0


def test_flattened_observation_is_directly_usable_by_strict_pit_study(tmp_path):
    cache = tmp_path / "b.json"
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    collect_selected_action_context_cached(
        rows(), cache, dynamic_ttl_hours=8, deep_ttl_hours=168,
        refresh_budget=5, request_start_interval_seconds=0,
        max_workers=1, fetcher=fetch_ok, now=now,
    )
    obs = load_audit73_pit_observations(cache, symbol_by_isin={"FR0000120271": "TTE.PA"})
    assert len(obs) == 1
    assert obs[0]["symbol"] == "TTE.PA"
    assert obs[0]["available_at"] == "2026-08-22T18:00:00+00:00"
    assert obs[0]["target_median"] == 196.09
    assert obs[0]["consensus"] == "BUY"
    assert obs[0]["n_analysts"] == 23
    assert obs[0]["consensus_delta_4w"] is not None
    assert obs[0]["period_kind"] == "CURRENT"
