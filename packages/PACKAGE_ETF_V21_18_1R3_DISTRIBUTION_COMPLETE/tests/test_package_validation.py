from __future__ import annotations

from tools.validate_package import _safe_payload_path, validate


def test_package_manifest_and_checksums_are_consistent():
    assert validate() == []


def test_package_validator_rejects_path_traversal():
    for unsafe in ("../secret", "/absolute", "nested/../../secret"):
        try:
            _safe_payload_path(unsafe)
        except ValueError as exc:
            assert "UNSAFE PATH" in str(exc)
        else:
            raise AssertionError(f"unsafe path accepted: {unsafe}")
