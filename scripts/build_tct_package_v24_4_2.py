from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path, PurePosixPath
import json
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "TCT_RELEASE_MANIFEST_V24_4_2.json"
FIXED_TIMESTAMP = (2026, 8, 21, 0, 0, 0)
FORBIDDEN_PREFIXES = ("state/", "outputs/", "data/cache/")
FORBIDDEN_NAMES = {".env", ".env.local", ".env.production"}


def _validated_source(relative: str) -> Path:
    if not relative or "\\" in relative:
        raise ValueError(f"TCT_PACKAGE_INVALID_MANIFEST_PATH:{relative!r}")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"TCT_PACKAGE_INVALID_MANIFEST_PATH:{relative!r}")
    if relative.startswith(FORBIDDEN_PREFIXES) or path.name in FORBIDDEN_NAMES:
        raise ValueError(f"TCT_PACKAGE_FORBIDDEN_MANIFEST_PATH:{relative}")

    source = ROOT.joinpath(*path.parts)
    try:
        source.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise ValueError(f"TCT_PACKAGE_PATH_OUTSIDE_ROOT:{relative}") from exc
    if source.is_symlink():
        raise ValueError(f"TCT_PACKAGE_SYMLINK_FORBIDDEN:{relative}")
    if not source.is_file():
        raise FileNotFoundError(f"TCT_PACKAGE_MISSING_FILE:{relative}")
    return source


def _manifest_files() -> tuple[dict, list[tuple[str, Path]]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files or not all(isinstance(path, str) for path in raw_files):
        raise ValueError("TCT_PACKAGE_INVALID_MANIFEST_FILES")
    if len(raw_files) != len(set(raw_files)):
        raise ValueError("TCT_PACKAGE_DUPLICATE_MANIFEST_PATH")
    return manifest, [(relative, _validated_source(relative)) for relative in raw_files]


def build(output: Path) -> dict:
    manifest, files = _manifest_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    file_sha256: dict[str, str] = {}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, source in sorted(files):
            payload = source.read_bytes()
            file_sha256[relative] = sha256(payload).hexdigest()
            info = zipfile.ZipInfo(relative)
            info.date_time = FIXED_TIMESTAMP
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)

    digest = sha256(output.read_bytes()).hexdigest()
    summary = {
        "release": manifest["release"],
        "version": manifest["version"],
        "status": manifest["status"],
        "validation_epoch": manifest["validation_epoch"],
        "production_baseline": manifest["production_baseline"],
        "file_count": len(files),
        "files_sha256": file_sha256,
        "bytes": output.stat().st_size,
        "sha256": digest,
        "output": str(output),
        "runtime_state_included": False,
        "promotion_authority": False,
    }
    summary_path = output.with_suffix(".sha256.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic complete TCT V24.4.2 SHADOW release kit")
    parser.add_argument(
        "--output",
        default="dist/TCT_V24_4_2_COMPLETE.zip",
        help="ZIP output path relative to repository root unless absolute",
    )
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    print(json.dumps(build(output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

