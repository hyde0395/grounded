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


if __name__ == "__main__":
    unittest.main()
