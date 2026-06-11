"""Verdict is pure logic — test it directly, no subprocess needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import verdict  # noqa: E402


class GateFileActionTest(unittest.TestCase):
    def test_edit_unread_file_is_stop(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True, read_files={})
        self.assertEqual(v.decision, verdict.STOP)
        self.assertIn("/p/a.py", v.reason)

    def test_edit_read_file_is_pass(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 1718000000})
        self.assertEqual(v.decision, verdict.PASS)

    def test_write_new_file_is_pass(self):
        v = verdict.gate_file_action("Write", "/p/new.py", file_exists=False, read_files={})
        self.assertEqual(v.decision, verdict.PASS)

    def test_write_existing_unread_file_is_stop(self):
        v = verdict.gate_file_action("Write", "/p/a.py", file_exists=True, read_files={})
        self.assertEqual(v.decision, verdict.STOP)


class GateShellWriteTest(unittest.TestCase):
    def test_truncate_unread_existing_is_stop(self):
        v = verdict.gate_shell_write("/p/a.py", "truncate", file_exists=True, read_files={})
        self.assertEqual(v.decision, verdict.STOP)
        self.assertIn("/p/a.py", v.reason)

    def test_inplace_unread_existing_is_stop(self):
        v = verdict.gate_shell_write("/p/a.py", "inplace", file_exists=True, read_files={})
        self.assertEqual(v.decision, verdict.STOP)

    def test_append_unread_existing_is_warn(self):
        v = verdict.gate_shell_write("/p/a.py", "append", file_exists=True, read_files={})
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("/p/a.py", v.reason)

    def test_read_file_is_pass(self):
        v = verdict.gate_shell_write("/p/a.py", "truncate", file_exists=True,
                                     read_files={"/p/a.py": 1})
        self.assertEqual(v.decision, verdict.PASS)

    def test_new_file_is_pass(self):
        v = verdict.gate_shell_write("/p/new.py", "truncate", file_exists=False, read_files={})
        self.assertEqual(v.decision, verdict.PASS)


class GatePackageTest(unittest.TestCase):
    def test_exists_is_pass(self):
        v = verdict.gate_package("pypi", "requests", exists=True)
        self.assertEqual(v.decision, verdict.PASS)

    def test_absent_is_stop_naming_registry(self):
        v = verdict.gate_package("pypi", "reqests", exists=False)
        self.assertEqual(v.decision, verdict.STOP)
        self.assertIn("reqests", v.reason)
        self.assertIn("PyPI", v.reason)

    def test_unknown_is_pass(self):
        v = verdict.gate_package("npm", "foo", exists=None)
        self.assertEqual(v.decision, verdict.PASS)


if __name__ == "__main__":
    unittest.main()
