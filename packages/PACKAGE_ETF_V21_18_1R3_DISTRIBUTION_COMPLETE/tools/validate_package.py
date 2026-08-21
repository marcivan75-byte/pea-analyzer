from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def _safe_payload_path(name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"UNSAFE PATH {name}")
    path = (ROOT / Path(*relative.parts)).resolve()
    if ROOT.resolve() not in path.parents:
        raise ValueError(f"UNSAFE PATH {name}")
    return path


def validate() -> list[str]:
    errors: list[str] = []
    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    checksum_rows: dict[str, str] = {}
    for line_number, line in enumerate((ROOT / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            expected, name = line.split("  ", 1)
            _safe_payload_path(name)
        except ValueError as exc:
            errors.append(f"CHECKSUM LINE {line_number}: {exc}")
            continue
        if name in checksum_rows:
            errors.append(f"DUPLICATE CHECKSUM {name}")
        checksum_rows[name] = expected.upper()

    manifest_rows = {row["path"]: row for row in manifest.get("files", [])}
    if len(manifest_rows) != len(manifest.get("files", [])):
        errors.append("DUPLICATE MANIFEST PATH")
    if set(manifest_rows) != set(checksum_rows):
        errors.append("MANIFEST CHECKSUM INVENTORY MISMATCH")

    for name, row in manifest_rows.items():
        try:
            path = _safe_payload_path(name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"MISSING {name}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if actual != str(row.get("sha256", "")).upper() or actual != checksum_rows.get(name):
            errors.append(f"HASH {name}")
        if path.stat().st_size != row.get("size"):
            errors.append(f"SIZE {name}")

    registry = json.loads((ROOT / "reference/criterion_registry.json").read_text(encoding="utf-8"))
    if len(registry.get("criteria", [])) != 268:
        errors.append("REGISTRY COUNT")
    if any(registry.get("governance", {}).values()):
        errors.append("UNSAFE GOVERNANCE FLAG")
    if manifest.get("release") != "V21.18.1R3":
        errors.append("MANIFEST RELEASE")
    if manifest.get("file_count_excluding_manifests") != len(manifest_rows):
        errors.append("MANIFEST FILE COUNT")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        print("PACKAGE VALIDATION FAIL")
        print("\n".join(failures))
        raise SystemExit(1)
    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    print("PACKAGE VALIDATION PASS")
    print(f"files checked: {len(manifest['files'])}; criteria: 268; unsafe flags: OFF")
