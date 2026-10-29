"""Tests for the file writing used by the MK2 Activator (Windows only: the module imports winreg)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from mk2_patcher import write_atomic
except ImportError:  # winreg only exists on Windows
    raise unittest.SkipTest("mk2_patcher is Windows only")


class WriteAtomicTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "Maschine 3.exe")
        with open(self.path, "wb") as f:
            f.write(b"old")

    def tearDown(self):
        self.dir.cleanup()

    def test_replaces_content(self):
        write_atomic(self.path, b"new")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"new")
        self.assertFalse(os.path.exists(self.path + ".mk2tmp"))

    def test_failure_keeps_original_and_removes_temp(self):
        with mock.patch("os.replace", side_effect=PermissionError("in use")):
            with self.assertRaises(PermissionError):
                write_atomic(self.path, b"new")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"old")
        self.assertFalse(os.path.exists(self.path + ".mk2tmp"))


if __name__ == "__main__":
    unittest.main()
