"""Release metadata must agree before any packaging or publication starts."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "desktop/scripts/version.py"


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chaoxing-version-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("pyproject.toml", '[project]\nname = "chaoxing"\nversion = "1.1.1"\n')
        self.write("desktop/src-tauri/Cargo.toml", '[package]\nname = "chaoxing-desktop"\nversion = "1.1.1"\n\n[dependencies]\nserde = "1"\n')
        self.write("desktop/src-tauri/Cargo.lock", 'version = 4\n\n[[package]]\nname = "another"\nversion = "9.8.7"\n\n[[package]]\nname = "chaoxing-desktop"\nversion = "1.1.1"\ndependencies = ["another"]\n')
        self.write_json("desktop/src-tauri/tauri.conf.json", {"version": "1.1.1", "identifier": "com.chaoxing.gui"})
        for project in ("web", "desktop"):
            self.write_json(f"{project}/package.json", {"name": project, "version": "1.1.1"})
            self.write_json(f"{project}/package-lock.json", {
                "name": project, "version": "1.1.1", "lockfileVersion": 3,
                "packages": {"": {"name": project, "version": "1.1.1"},
                             "node_modules/another": {"version": "9.8.7"}},
            })

    def write(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")

    def write_json(self, name, value):
        self.write(name, json.dumps(value, indent=2) + "\n")

    def run_check(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root),
                                 "--json", *args], capture_output=True, text=True,
                                encoding="utf-8", timeout=10)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_matching_metadata_and_tag_are_accepted_without_mutation(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = json.loads(self.run_check("--check", "--tag", "v1.1.1").stdout)
        self.assertEqual(output["version"], "1.1.1")
        self.assertTrue(output["success"])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_drift_in_each_metadata_file_is_rejected(self):
        for name in ("desktop/src-tauri/Cargo.toml", "desktop/src-tauri/Cargo.lock",
                     "desktop/src-tauri/tauri.conf.json", "web/package.json",
                     "web/package-lock.json", "desktop/package.json", "desktop/package-lock.json"):
            with self.subTest(path=name):
                path = self.root / name
                original = path.read_text(encoding="utf-8")
                path.write_text(original.replace('"1.1.1"', '"1.0.0"'), encoding="utf-8")
                result = self.run_check("--check", success=False)
                self.assertIn(name, result.stdout + result.stderr)
                path.write_text(original, encoding="utf-8")

    def test_sync_uses_pyproject_and_preserves_dependency_versions(self):
        canonical = '[project]\nname = "chaoxing"\nversion = "2.3.4"\n'
        self.write("pyproject.toml", canonical)
        self.run_check("--sync")
        self.assertEqual((self.root / "pyproject.toml").read_text(), canonical)
        self.run_check("--check", "--tag", "refs/tags/v2.3.4")
        self.assertIn('version = "9.8.7"', (self.root / "desktop/src-tauri/Cargo.lock").read_text())
        for project in ("web", "desktop"):
            lock = json.loads((self.root / project / "package-lock.json").read_text())
            self.assertEqual(lock["packages"]["node_modules/another"]["version"], "9.8.7")

    def test_tag_mismatch_blocks_release(self):
        result = self.run_check("--check", "--tag", "v1.1.2", success=False)
        self.assertIn("tag", result.stdout.lower() + result.stderr.lower())

    def test_npm_lock_root_package_version_is_checked(self):
        name = "desktop/package-lock.json"
        lock = json.loads((self.root / name).read_text())
        lock["packages"][""]["version"] = "1.0.0"
        self.write_json(name, lock)
        self.run_check("--check", success=False)

    def test_missing_cargo_package_is_not_fabricated_by_sync(self):
        self.write("desktop/src-tauri/Cargo.lock", 'version = 4\n[[package]]\nname = "other"\nversion = "1.1.1"\n')
        self.run_check("--sync", success=False)

    def test_artifact_versions_must_match_the_source(self):
        self.write("artifacts/chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe", "test")
        self.write("artifacts/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip", "test")
        self.run_check("--check", "--artifacts", str(self.root / "artifacts"))
        self.write("artifacts/chaoxing-gui-tauri-setup-1.0.0-windows-x64.exe", "stale")
        self.run_check("--check", "--artifacts", str(self.root / "artifacts"), success=False)

    def test_invalid_source_version_is_rejected_before_writes(self):
        self.write("pyproject.toml", '[project]\nversion = "../escape"\n')
        before = (self.root / "desktop/package.json").read_bytes()
        self.run_check("--sync", success=False)
        self.assertEqual((self.root / "desktop/package.json").read_bytes(), before)

    def test_artifact_kind_requires_its_matching_extension(self):
        for name in ("chaoxing-gui-tauri-setup-1.1.1-windows-x64.zip",
                     "chaoxing-gui-tauri-portable-1.1.1-windows-x64.exe"):
            with self.subTest(name=name):
                self.write(f"artifacts/{name}", "test")
                self.run_check("--check", "--artifacts", str(self.root / "artifacts"), success=False)
                (self.root / "artifacts" / name).unlink()


if __name__ == "__main__":
    unittest.main()
