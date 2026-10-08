"""Exercise pip's hash boundary using a tiny local wheel, without network."""
import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


class DependencyHashTests(unittest.TestCase):
    def test_changed_wheel_is_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "graphland_lock_probe-0.0.1-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("graphland_lock_probe.py", "VALUE = 1\n")
                archive.writestr("graphland_lock_probe-0.0.1.dist-info/METADATA", "Metadata-Version: 2.1\nName: graphland-lock-probe\nVersion: 0.0.1\n")
                archive.writestr("graphland_lock_probe-0.0.1.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: graphland-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr("graphland_lock_probe-0.0.1.dist-info/RECORD", "")
            expected = hashlib.sha256(wheel.read_bytes()).hexdigest()
            requirements = root / "locked.txt"
            requirements.write_text(str(wheel) + " --hash=sha256:" + expected + "\n")
            command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--no-deps", "--no-cache-dir", "--require-hashes", "-r", str(requirements)]
            good = subprocess.run(command + ["--target", str(root / "good")], capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertTrue((root / "good/graphland_lock_probe.py").exists())
            with wheel.open("ab") as handle:
                handle.write(b"changed after locking")
            bad = subprocess.run(command + ["--target", str(root / "bad")], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("HASHES", bad.stderr.upper())
            self.assertFalse((root / "bad/graphland_lock_probe.py").exists())
