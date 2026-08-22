from __future__ import annotations

import json

import pytest

from v182.reporting import etf_structure_state_replay as replay


def _config() -> dict:
    return {
        "version":"TEST_STATE",
        "audit_replay_path":"outputs/audit/REPLAY.json",
        "fields":{"ter_pct":{"max_age_days":186}},
        "governance":{"weights_changed":False},
    }


def _write_refresh(root, *, run_id: str, attempt: str, status: str = "SUCCESS") -> None:
    path=root/replay.REFRESH_AUDIT_RELATIVE
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({
        "status":status,
        "version":"REFRESH_TEST",
        "generated_at_utc":"2026-08-22T18:00:00+00:00",
        "github_run_id":run_id,
        "github_run_attempt":attempt,
        "changed_cells":12,
        "coverage_pct":{"ter_pct":99.0},
    }),encoding="utf-8")


def test_same_run_successful_refresh_skips_replay_before_master_read(tmp_path,monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID","12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT","2")
    monkeypatch.setattr(replay,"load_state_config",lambda path:_config())
    monkeypatch.setattr(
        replay.pd,
        "read_csv",
        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("master must not be reread")),
    )
    monkeypatch.setattr(
        replay,
        "load_replay_observations",
        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("state must not be replayed")),
    )
    _write_refresh(tmp_path,run_id="12345",attempt="2")

    result=replay.run(tmp_path)

    assert result["status"] == "SUCCESS"
    assert result["execution_mode"] == "SKIPPED_ALREADY_APPLIED_CURRENT_GITHUB_RUN"
    assert result["replay_observations"] == 0
    assert result["merge_quarantined"] == 0
    assert result["coverage_before_pct"] == result["coverage_after_pct"] == {"ter_pct":99.0}
    assert (tmp_path/"outputs/audit/REPLAY.json").exists()


@pytest.mark.parametrize(
    ("current_run","current_attempt","audit_run","audit_attempt","audit_status"),
    [
        ("12345","2","99999","2","SUCCESS"),
        ("12345","2","12345","1","SUCCESS"),
        ("12345","2","12345","2","FAILED"),
    ],
)
def test_mismatch_or_failed_refresh_keeps_historical_replay_path(
    tmp_path,monkeypatch,current_run,current_attempt,audit_run,audit_attempt,audit_status
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID",current_run)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT",current_attempt)
    monkeypatch.setattr(replay,"load_state_config",lambda path:_config())
    _write_refresh(tmp_path,run_id=audit_run,attempt=audit_attempt,status=audit_status)

    with pytest.raises(FileNotFoundError,match="ETF_STRUCTURE_STATE_REPLAY_REQUIRES_CURRENT_ENRICHED_MASTER"):
        replay.run(tmp_path)


def test_local_execution_never_skips_from_stale_refresh_audit(tmp_path,monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_RUN_ID",raising=False)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT",raising=False)
    monkeypatch.setattr(replay,"load_state_config",lambda path:_config())
    _write_refresh(tmp_path,run_id="12345",attempt="2")

    with pytest.raises(FileNotFoundError,match="ETF_STRUCTURE_STATE_REPLAY_REQUIRES_CURRENT_ENRICHED_MASTER"):
        replay.run(tmp_path)
