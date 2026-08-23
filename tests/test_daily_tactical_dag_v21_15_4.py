from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from v182.reporting import daily_tactical_super_runner_v21_15_4 as dag


ROOT = Path(__file__).resolve().parents[1]


def _core() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"asset_class": "ACTION", "horizon": "TCT", "isin": "A1", "score": 81.0, "decision": "WATCH"},
            {"asset_class": "ACTION", "horizon": "CT", "isin": "A2", "score": 84.0, "decision": "BUY_CANDIDATE"},
            {"asset_class": "ETF", "horizon": "CT", "isin": "E1", "score": 76.0, "decision": "WATCH"},
        ]
    )


def test_selected_context_may_add_fields_but_not_mutate_authority() -> None:
    core = _core()
    enriched = core.copy()
    enriched["boursorama_consensus"] = ["BUY", "HOLD", ""]
    enriched["investing_daily_signal"] = ["BUY", "NEUTRAL", "SELL"]
    dag._assert_non_authoritative_enrichment(core, enriched)


def test_selected_context_score_mutation_fails_closed() -> None:
    core = _core()
    enriched = core.copy()
    enriched.loc[0, "score"] = 99.0
    with pytest.raises(RuntimeError, match="SELECTED_SOURCE_SCORE_MUTATION_FORBIDDEN"):
        dag._assert_non_authoritative_enrichment(core, enriched)


def test_selected_context_decision_mutation_fails_closed() -> None:
    core = _core()
    enriched = core.copy()
    enriched.loc[1, "decision"] = "REJECT"
    with pytest.raises(RuntimeError, match="SELECTED_SOURCE_DECISION_MUTATION_FORBIDDEN"):
        dag._assert_non_authoritative_enrichment(core, enriched)


def test_selected_context_key_or_row_mutation_fails_closed() -> None:
    core = _core()
    with pytest.raises(RuntimeError):
        dag._assert_non_authoritative_enrichment(core, core.iloc[:-1].copy())
    duplicate = pd.concat([core, core.iloc[[0]]], ignore_index=True)
    with pytest.raises(RuntimeError, match="DAILY_ENRICHED_DUPLICATE_KEYS"):
        dag._assert_non_authoritative_enrichment(core, duplicate)


def test_dag_source_declares_non_authoritative_context_and_tct_release() -> None:
    source = (ROOT / "src" / "v182" / "reporting" / "daily_tactical_super_runner_v21_15_4.py").read_text(encoding="utf-8")
    assert "selected_source_decision_influence" in source
    assert '"selected_source_score_influence": 0.0' in source
    assert '"postmarket_released_on_tct_completion": True' in source
    assert '"postmarket_waits_for_action_ct": False' in source
    assert '"final_decision_file_written_after_tct_reader_completed": True' in source
    assert source.index("_write(decisions, outdir / \"DAILY_TCT_CT_DECISIONS.csv\")") < source.index("enrich_selected_rows(core[\"source_input\"]")


def test_tactical_bundle_releases_tct_before_action_join() -> None:
    source = (ROOT / "src" / "v182" / "reporting" / "tactical_shadow_bundle_run.py").read_text(encoding="utf-8")
    assert source.index("tct, tct_error = tct_future.result()") < source.index("action_ct, action_ct_error = action_future.result()")
    assert "tct_complete_callback(tct, tct_error)" in source
    assert '"tct_completion_released_before_action_ct_join": True' in source
