from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
import pytest

from v182.audit.provenance import append_records, value_hash
from v182.io.frames import apply_observations
from v182.state.etf_structure_state import (
    STATE_COLUMNS,
    load_replay_observations,
    load_state_config,
    write_structural_state_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return load_state_config(ROOT / "config" / "ETF_STRUCTURE_STATE_V21_15.json")


def _state_row(**overrides) -> dict:
    row = {
        "captured_at_utc": "2026-08-20T10:00:00+00:00",
        "universe": "ETF",
        "isin": "FR0013380607",
        "field": "ter_pct",
        "value": "0.25",
        "source": "issuer",
        "source_url": "https://issuer.example/factsheet.pdf",
        "evidence_level": "A",
        "as_of": "2026-07-31T00:00:00+00:00",
        "validation_status": "ISIN_MATCHED",
        "value_sha256": value_hash("0.25"),
    }
    row.update(overrides)
    return row


def _write_state(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows, columns=STATE_COLUMNS).to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def test_validation_status_contract_cannot_be_widened():
    cfg = _cfg()
    cfg["accepted_validation_statuses"].append("EXACT_ISIN_SOURCE_MATCH")
    with pytest.raises(ValueError, match="VALIDATION_STATUS_CONTRACT_DRIFT"):
        load_replay_observations(cfg, state_path=ROOT / "does-not-exist.csv")


def test_writer_persists_only_value_bound_to_retained_provenance(tmp_path: Path):
    ledger = tmp_path / "provenance.csv"
    append_records(
        [
            {
                "universe": "ETF",
                "isin": "FR0013380607",
                "field": "ter_pct",
                "value": "0.25",
                "source": "issuer",
                "source_url": "https://issuer.example/factsheet.pdf",
                "evidence_level": "A",
                "as_of": "2026-07-31",
                "validation_status": "ISIN_MATCHED",
                "merge_action": "INSERT",
                "merge_reason": "FIRST_OBSERVATION",
            },
            {
                "universe": "ETF",
                "isin": "FR0013380607",
                "field": "fund_total_assets_eur_m",
                "value": "999",
                "source": "issuer",
                "source_url": "https://issuer.example/factsheet.pdf",
                "evidence_level": "A",
                "as_of": "2026-07-31",
                "validation_status": "ISIN_MATCHED",
                "merge_action": "INSERT",
                "merge_reason": "FIRST_OBSERVATION",
            },
        ],
        path=ledger,
    )
    frame = pd.DataFrame(
        [{"isin": "FR0013380607", "ter_pct": "0.25", "fund_total_assets_eur_m": "1000"}]
    )
    state_path = tmp_path / "state.csv"
    summary = write_structural_state_snapshot(
        frame,
        _cfg(),
        root=tmp_path,
        state_path=state_path,
        provenance_path=ledger,
        now="2026-08-20T10:00:00Z",
    )
    state = pd.read_csv(state_path, sep=";", encoding="utf-8-sig", dtype=str)
    assert summary["rows"] == 1
    assert state[["isin", "field", "value"]].to_dict("records") == [
        {"isin": "FR0013380607", "field": "ter_pct", "value": "0.25"}
    ]
    assert summary["skipped"]["PROVENANCE_VALUE_HASH_MISMATCH"] == 1


def test_replay_respects_field_ttl_without_imputation(tmp_path: Path):
    state_path = tmp_path / "state.csv"
    _write_state(
        state_path,
        [
            _state_row(),
            _state_row(
                isin="LU0000000001",
                field="diversification_direct_score",
                value="88.0",
                value_sha256=value_hash("88.0"),
                evidence_level="C",
                validation_status="AUTO_MATCH",
                as_of="2026-07-01T00:00:00+00:00",
            ),
        ],
    )
    observations, diag = load_replay_observations(
        _cfg(), root=tmp_path, state_path=state_path, as_of="2026-08-20T12:00:00Z"
    )
    assert {(row["isin"], row["field"]) for row in observations} == {("FR0013380607", "ter_pct")}
    assert diag["rejected"]["STALE"] == 1
    assert diag["missing_imputation"] is False


def test_future_invalid_status_hash_and_duplicates_fail_closed(tmp_path: Path):
    state_path = tmp_path / "state.csv"
    rows = [
        _state_row(isin="A", as_of="2026-09-01T00:00:00Z"),
        _state_row(isin="B", validation_status="UNEXPECTED"),
        _state_row(isin="C", value_sha256="bad"),
        _state_row(isin="D"),
        _state_row(isin="D", source="duplicate"),
    ]
    _write_state(state_path, rows)
    observations, diag = load_replay_observations(
        _cfg(), root=tmp_path, state_path=state_path, as_of="2026-08-20T12:00:00Z"
    )
    assert observations == []
    assert diag["rejected"]["TIMESTAMP_FUTURE"] == 1
    assert diag["rejected"]["VALIDATION_STATUS_REJECTED"] == 1
    assert diag["rejected"]["VALUE_HASH_MISMATCH"] == 1
    assert diag["rejected"]["DUPLICATE_KEY"] == 2


def test_replayed_observation_uses_normal_merge_and_no_neutral_value(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "state.csv"
    _write_state(state_path, [_state_row()])
    observations, diag = load_replay_observations(
        _cfg(), root=tmp_path, state_path=state_path, as_of="2026-08-20T12:00:00Z"
    )
    monkeypatch.setenv("PEA_PROVENANCE_PATH", str(tmp_path / "replay_provenance.csv"))
    frame = pd.DataFrame([{"isin": "FR0013380607", "ter_pct": pd.NA, "fund_total_assets_eur_m": pd.NA}])
    merged, quarantined = apply_observations(frame, observations)
    assert quarantined == []
    assert merged.loc[0, "ter_pct"] == "0.25"
    assert pd.isna(merged.loc[0, "fund_total_assets_eur_m"])
    assert diag["eligible_rows"] == 1


def test_no_state_is_safe_and_empty(tmp_path: Path):
    observations, diag = load_replay_observations(
        _cfg(), root=tmp_path, state_path=tmp_path / "missing.csv", as_of="2026-08-20"
    )
    assert observations == []
    assert diag["status"] == "NO_STATE"


def test_config_declares_no_daily_scrape_or_model_change():
    raw = json.loads((ROOT / "config" / "ETF_STRUCTURE_STATE_V21_15.json").read_text(encoding="utf-8"))
    governance = raw["governance"]
    assert governance["daily_network_structural_scrape"] is False
    assert governance["new_cron_created"] is False
    assert governance["missing_imputation"] is False
    assert governance["neutral_imputation"] is False
    assert governance["weights_changed"] is False
    assert governance["thresholds_changed"] is False
    assert governance["t1_t2_scope_changed"] is False
    assert governance["holdout_opened"] is False
