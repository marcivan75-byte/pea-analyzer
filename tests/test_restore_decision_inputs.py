from datetime import datetime, timedelta, timezone
from pathlib import Path
import zipfile

import pytest

from v182.ci.restore_decision_inputs import REQUIRED, _extract_required, _select_artifact, _select_run


def test_extract_required_only_restores_decision_inputs(tmp_path: Path):
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in REQUIRED:
            bundle.writestr(name, "{}" if name.endswith(".json") else "decision;score\nWATCH;50\n")
        bundle.writestr("state/provenance/VERY_LARGE.csv", "must not be extracted")

    root = tmp_path / "root"
    extracted = _extract_required(archive, root)

    assert set(extracted) == REQUIRED
    assert not (root / "state/provenance/VERY_LARGE.csv").exists()


def test_select_run_fails_closed_when_latest_success_is_stale():
    stale = (datetime.now(timezone.utc) - timedelta(hours=193)).isoformat().replace("+00:00", "Z")
    payload = {"workflow_runs": [{"id": 10, "status": "completed", "conclusion": "success", "updated_at": stale}]}

    with pytest.raises(RuntimeError, match="stale"):
        _select_run(payload, current_run_id=11, max_age_hours=192)


def test_select_artifact_accepts_legacy_and_current_global_names():
    payload = {
        "artifacts": [
            {"id": 1, "name": "committee-master-v21-8-1-100", "expired": False},
            {"id": 2, "name": "committee-weekly-v21-8-1-101", "expired": False},
        ]
    }

    assert _select_artifact(payload)["id"] == 2

