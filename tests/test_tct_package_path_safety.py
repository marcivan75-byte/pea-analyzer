from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts" / "build_tct_package_v24_4_2.py"))
validate = MODULE["_validated_source"]


class ReleasePackagePathSafetyTests(unittest.TestCase):
    def test_release_builder_rejects_paths_outside_release_scope(self):
        paths = ["../.env", "/etc/passwd", r"..\\.env", "state/runtime.json", "outputs/result.csv", "data/cache/prices.csv", ".env"]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate(path)

    def test_release_builder_accepts_a_regular_repository_file(self):
        source = validate("scripts/build_tct_package_v24_4_2.py")
        self.assertTrue(source.is_file())
        self.assertEqual(source.relative_to(ROOT), Path("scripts/build_tct_package_v24_4_2.py"))


if __name__ == "__main__":
    unittest.main()

