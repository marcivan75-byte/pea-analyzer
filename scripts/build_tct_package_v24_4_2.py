from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import json
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "TCT_RELEASE_MANIFEST_V24_4_2.json"
FIXED_TIMESTAMP = (2026, 8, 21, 0, 0, 0)


def _manifest_files() -> tuple[dict, list[str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = [str(path) for path in manifest["files"]]
    if len(files) != len(set(files)):
        raise ValueError("TCT_PACKAGE_DUPLICATE_MANIFEST_PATH")
    forbidden_prefixes = ("state/", "outputs/", "data/cache/")
    forbidden = [path for path in files if path.startswith(forbidden_prefixes)]
    if forbidden:
        raise ValueError(f"TCT_PACKAGE_RUNTIME_STATE_FORBIDDEN:{forbidden}")
    missing = [path for path in files if not (ROOT / path).is_file()]
    if missing:
        raise FileNotFoundError(f"TCT_PACKAGE_MISSING_FILES:{missing}")
    return manifest, files


def build(output: Path) -> dict:
    manifest, files = _manifest_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(files):
            source = ROOT / relative
            info = zipfile.ZipInfo(relative)
            info.date_time = FIXED_TIMESTAMP
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())

    digest = sha256(output.read_bytes()).hexdigest()
    summary = {
        "release": manifest["release"],
        "version": manifest["version"],
        "status": manifest["status"],
        "validation_epoch": manifest["validation_epoch"],
        "production_baseline": manifest["production_baseline"],
        "file_count": len(files),
        "bytes": output.stat().st_size,
        "sha256": digest,
        "output": str(output),
        "runtime_state_included": False,
        "promotion_authority": False,
    }
    summary_path = output.with_suffix(".sha256.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
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
