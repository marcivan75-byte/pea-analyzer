from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import zipfile

from scripts.build_tct_package_v24_4_2 import build


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "TCT_RELEASE_MANIFEST_V24_4_2.json"


def test_release_manifest_is_complete_and_excludes_runtime_observations():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = manifest["files"]
    assert manifest["release"] == "TCT_V24.4.2_AUDIT_HARDENED_SHADOW"
    assert manifest["production_baseline"] == "V21.8.1_UNCHANGED"
    assert manifest["validation_epoch"] == "V24.4.2_ONLY_NO_MIX_WITH_PRIOR_EPOCHS"
    assert len(files) == len(set(files))
    assert not [path for path in files if path.startswith(("state/", "outputs/", "data/cache/"))]
    assert all((ROOT / path).is_file() for path in files)
    required = {
        "config/TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json",
        "config/TCT_V24_4_2_VALIDATION_GATES.json",
        "src/v182/reporting/tct_next_session_catalyst_run_v24_4_2.py",
        "src/v182/reporting/tct_pit_ohlc_ledger_v24_4_2.py",
        "src/v182/reporting/tct_v24_4_2_pit_lineage.py",
        "src/v182/reporting/tct_v24_4_2_pit_validator.py",
        "src/v182/sources/tct_catalyst_news_v24_4_2.py",
        "tests/fixtures/tct_v24_4_2_catalyst_golden_set.json",
        "docs/TCT_CDC_V24_4_2_FINAL.md",
        "docs/TCT_REFERENTIEL_V24_4_2_FINAL.md",
    }
    assert required <= set(files)


def test_release_zip_is_deterministic_and_sha256_is_published(tmp_path: Path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    summary1 = build(first)
    summary2 = build(second)
    assert summary1["sha256"] == summary2["sha256"]
    assert sha256(first.read_bytes()).hexdigest() == summary1["sha256"]
    assert summary1["runtime_state_included"] is False
    assert summary1["promotion_authority"] is False
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert not [name for name in names if name.startswith(("state/", "outputs/", "data/cache/"))]
        assert "docs/TCT_RELEASE_MANIFEST_V24_4_2.json" in names
        assert "scripts/build_tct_package_v24_4_2.py" in names
        assert "tests/test_tct_v24_4_2_release_package.py" in names
    sidecar = first.with_suffix(".sha256.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["sha256"] == summary1["sha256"]
