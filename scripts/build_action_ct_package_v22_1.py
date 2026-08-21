from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import json
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packages" / "ACTION_CT_V22_1_0" / "MANIFEST.json"


def build(output: Path) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = [str(path) for path in manifest["files"]]
    missing = [path for path in files if not (ROOT / path).is_file()]
    if missing:
        raise FileNotFoundError(f"ACTION_CT_PACKAGE_MISSING_FILES:{missing}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(files):
            source = ROOT / relative
            info = zipfile.ZipInfo(relative)
            info.date_time = (2026, 8, 21, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())

    digest = sha256(output.read_bytes()).hexdigest()
    summary = {
        "package": manifest["package"],
        "decision_version": manifest["decision_version"],
        "runtime_patch_version": manifest["runtime_patch_version"],
        "status": manifest["status"],
        "file_count": len(files),
        "bytes": output.stat().st_size,
        "sha256": digest,
        "output": str(output),
    }
    summary_path = output.with_suffix(".sha256.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build complete Action CT V22.1.1 runtime-patch package")
    parser.add_argument(
        "--output",
        default="dist/ACTION_CT_V22_1_1_COMPLETE.zip",
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
