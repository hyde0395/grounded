import json
import os
import subprocess
import sys
import tempfile
import unittest

from hook_runner import HOOKS_DIR, run_hook


def payload(tool_name, file_path, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": tool_name,
            "tool_input": {"file_path": file_path}, "cwd": cwd}


class PreGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # realpath: macOS /var is a symlink to /private/var
        self.cwd = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        p = os.path.join(self.cwd, name)
        with open(p, "w") as f:
            f.write("x")
        return p

    def write_ledger(self, read_files):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            json.dump({"read_files": read_files, "verified_urls": {}, "known_pkgs": {}}, f)

    def test_edit_unread_existing_file_blocked_exit2(self):
        p = self.touch("a.py")
        self.write_ledger({})
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 2)
        self.assertIn("a.py", r.stderr)
        self.assertIn("grounded", r.stderr)

    def test_edit_read_file_passes_silently(self):
        p = self.touch("a.py")
        self.write_ledger({p: 1718000000})
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_write_new_file_passes(self):
        self.write_ledger({})
        r = run_hook("pre_gate.py", payload("Write", os.path.join(self.cwd, "new.py"), self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_write_existing_unread_file_blocked(self):
        p = self.touch("a.py")
        self.write_ledger({})
        r = run_hook("pre_gate.py", payload("Write", p, self.cwd))
        self.assertEqual(r.returncode, 2)

    def test_relative_path_matches_absolute_ledger_entry(self):
        p = self.touch("a.py")
        self.write_ledger({p: 1718000000})
        r = run_hook("pre_gate.py", payload("Edit", "a.py", self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_missing_ledger_blocks_edit(self):
        p = self.touch("a.py")
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 2)

    def test_corrupt_ledger_fails_open(self):
        p = self.touch("a.py")
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            f.write("{not json")
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_ungated_tool_passes(self):
        r = run_hook("pre_gate.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                     "tool_input": {"command": "ls"}, "cwd": self.cwd})
        self.assertEqual(r.returncode, 0)

    def test_garbage_stdin_fails_open(self):
        r = subprocess.run(
            [sys.executable, os.path.join(HOOKS_DIR, "pre_gate.py")],
            input="not json", capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
