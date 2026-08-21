from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import argparse
import json


FILES = (
    ".github/workflows/action_mt_v1_tests.yml",
    "config/ACTION_MT_V1_0_0_SHADOW.json",
    "docs/ACTION_MT_V1_0_0_REFERENTIEL.md",
    "docs/ACTION_MT_CACHE_POLICY_V1.md",
    "packages/ACTION_MT_V1_0_0/README.md",
    "packages/ACTION_MT_V1_0_0/schemas/ACTION_MT_RUN_REPORT.schema.json",
    "scripts/build_action_mt_package_v1.py",
    "scripts/validate_action_mt_ci.py",
    "src/v182/decision/action_mt_decision_v1.py",
    "src/v182/features/action_mt_v1.py",
    "src/v182/reporting/action_mt_shadow_run_v1.py",
    "src/v182/sources/action_mt_cache_v1.py",
    "tests/test_action_mt_v1.py",
    "tests/test_action_mt_runtime_v1.py",
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def build(root: Path, output: Path) -> dict:
    missing = [relative for relative in FILES if not (root / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"package files missing: {missing}")
    manifest = {
        "package": "ACTION_MT_V1.0.0_SHADOW",
        "status": "SHADOW_RESEARCH_ONLY",
        "file_count": len(FILES),
        "files": [{"path": relative, "sha256": digest(root / relative), "size": (root / relative).stat().st_size} for relative in FILES],
        "empty_directories": ["data/cache/actions", "outputs/action_mt_v1"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in FILES:
            archive.write(root / relative, relative)
        archive.writestr("MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        archive.writestr("data/cache/actions/.gitkeep", "")
        archive.writestr("outputs/action_mt_v1/.gitkeep", "")
    manifest["archive_sha256"] = digest(output)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root.resolve(), args.output.resolve()), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

