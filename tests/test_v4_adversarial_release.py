from pathlib import Path
import json
import shutil

from scripts.build_weekly_v4_package import build
from v182.audit.weekly_v4_governance import run as governance_run


ROOT = Path(__file__).resolve().parents[1]


def test_tampered_weight_vector_fails_governance(tmp_path):
    required = [
        "config/WEEKLY_V4_GOVERNANCE.json",
        "config/WEEKLY_V4_SOURCE_CONTRACT.json",
        "config/V21_ACTIONS_CRITERIA_REGISTRY.json",
        "config/V20_7_1_ETF_CRITERIA_REGISTRY.json",
        "config/CI_ENTRY_CONFIDENCE_V22_2.json",
        "config/FULL_REFERENTIAL_INTEGRITY.json",
    ]
    for relative in required:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    registry_path = tmp_path / "config/V21_ACTIONS_CRITERIA_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    first = next(iter(registry["weights"]["CT"]))
    registry["weights"]["CT"][first] += 0.01
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    audit = governance_run(tmp_path, write=False)
    assert audit["status"] == "FAIL"
    assert "actions.CT.weights_sum" in audit["fatal_failures"]


def test_release_archive_is_byte_reproducible(tmp_path):
    first = build(ROOT, tmp_path / "first" / "WEEKLY_V4_HEBDO", release_commit="test-commit")
    second = build(ROOT, tmp_path / "second" / "WEEKLY_V4_HEBDO", release_commit="test-commit")
    assert first["zip_sha256"] == second["zip_sha256"]
    assert first["manifest_sha256"] == second["manifest_sha256"]


def test_release_target_must_not_overwrite_existing_content(tmp_path):
    target = tmp_path / "WEEKLY_V4_HEBDO"
    target.mkdir()
    (target / "user-file.txt").write_text("keep", encoding="utf-8")
    try:
        build(ROOT, target, release_commit="test-commit")
    except FileExistsError as exc:
        assert "NON_EMPTY_PACKAGE_TARGET" in str(exc)
    else:
        raise AssertionError("non-empty release target overwritten")
