"""Exercise mirror writes, WhatIf, exclusions, and junction boundaries in a fake repo."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(
    sys.platform == "win32" and POWERSHELL, "Windows PowerShell fixture"
)
class MotionWeaverSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="motionweaver-sync-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "unreal-sample/AIMotionSample/Plugins/MotionWeaver"
        self.mirror = self.root / "unreal-script/MotionWeaver"
        self.script = self.root / "unreal-script/Tools/Sync-MotionWeaver.ps1"
        self.script.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "unreal-script/Tools/Sync-MotionWeaver.ps1", self.script)
        self.source.mkdir(parents=True)
        (self.source / "MotionWeaver.uplugin").write_text('{"FileVersion":3}')
        (self.source / "Source").mkdir()
        (self.source / "Source/module.cpp").write_text("source")

    def run_sync(self, *arguments):
        assert POWERSHELL is not None
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-File", str(self.script), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        check=False)

    def require_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sync_removes_stale_files_and_preserves_excluded_content(self):
        self.mirror.mkdir()
        (self.mirror / "stale.cpp").write_text("stale")
        for name in ("Content", "Binaries", "Intermediate", "Saved"):
            (self.source / name).mkdir()
            (self.source / name / "source.bin").write_bytes(b"local")
            (self.mirror / name).mkdir()
            (self.mirror / name / "keep.bin").write_bytes(b"keep")
        self.require_success(self.run_sync())
        self.require_success(self.run_sync("-Check"))
        self.assertEqual((self.mirror / "Source/module.cpp").read_text(), "source")
        self.assertFalse((self.mirror / "stale.cpp").exists())
        for name in ("Content", "Binaries", "Intermediate", "Saved"):
            self.assertEqual((self.mirror / name / "keep.bin").read_bytes(), b"keep")
            self.assertFalse((self.mirror / name / "source.bin").exists())

    def test_whatif_and_check_never_create_or_repair_files(self):
        self.require_success(self.run_sync("-WhatIf"))
        self.assertFalse(self.mirror.exists())
        self.assertNotEqual(self.run_sync("-Check").returncode, 0)
        self.assertFalse(self.mirror.exists())
        self.require_success(self.run_sync())
        file = self.mirror / "Source/module.cpp"
        file.write_text("drift")
        self.assertNotEqual(self.run_sync("-Check").returncode, 0)
        self.assertEqual(file.read_text(), "drift")

    def make_junction(self, link, target):
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True
        , check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(link.rmdir)

    def test_mirror_junction_does_not_change_outside_files(self):
        self.mirror.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "module.cpp").write_text("keep")
        self.make_junction(self.mirror / "Source", outside)
        self.assertNotEqual(self.run_sync().returncode, 0)
        self.assertEqual((outside / "module.cpp").read_text(), "keep")
        self.assertFalse((self.mirror / "MotionWeaver.uplugin").exists())

    def test_source_junction_is_rejected_before_copy(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        self.make_junction(self.source / "Resources", outside)
        self.assertNotEqual(self.run_sync().returncode, 0)
        self.assertFalse(self.mirror.exists())
        self.assertEqual((outside / "keep.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
