from datetime import datetime, timedelta, timezone
from pathlib import Path
import zipfile

import pytest

import urllib.request

from v182.ci.restore_decision_inputs import (
    DECISION_PACKAGE_V3,
    FALLBACK_CONTEXT,
    OPTIONAL,
    REQUIRED,
    _SafeRedirectHandler,
    _extract_required,
    _select_artifact,
    _select_run,
)


def test_extract_required_prefers_compact_v3_decision_package(tmp_path: Path):
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in REQUIRED | OPTIONAL | DECISION_PACKAGE_V3 | FALLBACK_CONTEXT:
            payload = "{}" if name.endswith(".json") else "decision;score\nWATCH;50\n"
            bundle.writestr(name, payload)
        bundle.writestr("state/provenance/VERY_LARGE.csv", "must not be extracted")

    root = tmp_path / "root"
    extracted = set(_extract_required(archive, root))

    assert extracted == REQUIRED | OPTIONAL | DECISION_PACKAGE_V3
    assert not (root / "state/provenance/VERY_LARGE.csv").exists()
    for name in FALLBACK_CONTEXT:
        assert not (root / name).exists()


def test_extract_required_restores_fallback_context_for_legacy_artifact(tmp_path: Path):
    archive = tmp_path / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in REQUIRED | OPTIONAL | FALLBACK_CONTEXT:
            payload = "{}" if name.endswith(".json") else "decision;score\nWATCH;50\n"
            bundle.writestr(name, payload)

    extracted = set(_extract_required(archive, tmp_path / "root"))

    assert extracted == REQUIRED | OPTIONAL | FALLBACK_CONTEXT
    assert not (DECISION_PACKAGE_V3 & extracted)


def test_extract_required_accepts_artifact_before_optional_explainability(tmp_path: Path):
    archive = tmp_path / "oldest.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in REQUIRED:
            bundle.writestr(name, "{}" if name.endswith(".json") else "decision;score\nWATCH;50\n")

    assert set(_extract_required(archive, tmp_path / "root")) == REQUIRED


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


def test_cross_host_redirect_does_not_leak_github_authorization():
    request = urllib.request.Request("https://api.github.com/repos/o/r/actions/artifacts/1/zip", headers={"Authorization": "Bearer secret"})

    redirected = _SafeRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "https://signed-storage.example/artifact.zip"
    )

    assert redirected.get_header("Authorization") is None
