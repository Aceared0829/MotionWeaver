"""Check copy verification and deletion boundaries using isolated storage fixtures."""

import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    candidates = list((ROOT / "data/tools").rglob(f"{name}.py"))
    path = next(path for path in candidates if path.parent.name != "tools")
    spec = importlib.util.spec_from_file_location(
        name, path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


migration = load_tool("migrate_motion_storage_to_e")
observer = load_tool("migration_progress_lite_server")


class StorageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="motion-storage-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        (self.source / "clip.bin").write_bytes(b"original")
        self.job = migration.Transfer("fixture", self.source, self.destination, True)
        self.files = migration.inventory(self.source)

    def copy(self):
        shutil.copytree(self.source, self.destination)

    def test_real_robocopy_keeps_source_until_verified_cleanup(self):
        if sys.platform != "win32":
            self.skipTest("Robocopy is Windows-only")
        result = migration.robocopy(
            self.source, self.destination, self.root / "copy.log"
        )
        self.assertLess(result, 8)
        self.assertEqual((self.source / "clip.bin").read_bytes(), b"original")
        migration.verify(self.destination, self.files)
        migration.remove_verified_source(self.job, self.files)
        self.assertFalse(self.source.exists())
        self.assertEqual((self.destination / "clip.bin").read_bytes(), b"original")

    def test_same_size_corruption_retains_source(self):
        self.copy()
        (self.destination / "clip.bin").write_bytes(b"corrupt!")
        with self.assertRaises((ValueError, RuntimeError)):
            migration.remove_verified_source(self.job, self.files)
        self.assertEqual((self.source / "clip.bin").read_bytes(), b"original")

    def test_source_change_after_copy_retains_source(self):
        self.copy()
        (self.source / "clip.bin").write_bytes(b"modified")
        with self.assertRaises(RuntimeError):
            migration.remove_verified_source(self.job, self.files)
        self.assertEqual((self.source / "clip.bin").read_bytes(), b"modified")

    def test_shared_destination_collision_is_rejected_before_copy(self):
        second = self.root / "second"
        second.mkdir()
        (second / "clip.bin").write_bytes(b"conflict")
        jobs = [self.job, migration.Transfer("second", second, self.destination, True)]
        with self.assertRaises(ValueError):
            migration.validate_plan(
                jobs, [{"files": self.files}, {"files": migration.inventory(second)}]
            )
        self.assertFalse(self.destination.exists())

    def test_identical_shared_destination_is_allowed(self):
        second = self.root / "second"
        shutil.copytree(self.source, second)
        jobs = [self.job, migration.Transfer("second", second, self.destination, True)]
        migration.validate_plan(
            jobs, [{"files": self.files}, {"files": migration.inventory(second)}]
        )

    def test_conflicting_existing_destination_is_preserved(self):
        self.copy()
        (self.destination / "clip.bin").write_bytes(b"existing")
        with self.assertRaises(ValueError):
            migration.validate_plan([self.job], [{"files": self.files}])
        self.assertEqual((self.destination / "clip.bin").read_bytes(), b"existing")

    def test_overlap_and_parent_escape_are_rejected(self):
        nested = migration.Transfer("nested", self.source, self.source / "nested", True)
        with self.assertRaises(ValueError):
            migration.validate_plan([nested], [{"files": self.files}])
        escaped = dict(self.files[0], relative="../outside.bin")
        with self.assertRaises(ValueError):
            migration.validate_plan([self.job], [{"files": [escaped]}])

    def test_junction_cannot_redirect_cleanup_or_observer(self):
        if sys.platform != "win32":
            self.skipTest("Windows junction fixture")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.bin").write_bytes(b"keep")
        junction = self.source / "link"
        # The paths belong to this temporary fixture; this only creates a junction.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
        check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(junction.rmdir)
        with self.assertRaises(ValueError):
            migration.inventory(self.source)
        self.assertEqual(observer.byte_size(self.source), len(b"original"))
        self.assertEqual((outside / "keep.bin").read_bytes(), b"keep")

    def test_atomic_report_is_readable_and_observer_imports(self):
        report_path = self.root / "report.json"
        migration.write_report(report_path, {"jobs": []})
        self.assertEqual(report_path.read_text(), '{\n  "jobs": []\n}')
        self.assertFalse(report_path.with_suffix(".tmp").exists())
        self.assertEqual(observer.byte_size(self.source), len(b"original"))

    def test_report_junction_cannot_write_outside_report_directory(self):
        if sys.platform != "win32":
            self.skipTest("Windows junction fixture")
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "migration"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
        , check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(link.rmdir)
        with self.assertRaises(ValueError):
            migration.write_report(link / "report.json", {"jobs": []})
        self.assertFalse((outside / "report.json").exists())

    def test_lite_http_server_reports_only_custom_fixture_root(self):
        (self.root / "BONES-SEED").mkdir()
        (self.root / "BONES-SEED/clip.bin").write_bytes(b"tiny")
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = candidate.getsockname()[1]
        server = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "data/tools/ops/migration_progress_lite_server.py"),
                "--root",
                str(self.root),
                "--port",
                str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api", timeout=1) as response:
                        payload = json.load(response)
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        self.fail("Fixture observer did not start")
                    time.sleep(0.05)
            self.assertEqual(payload["total_bytes"], 4)
            with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                self.assertIn(b'getElementById("status")', response.read())
        finally:
            server.terminate()
            server.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
