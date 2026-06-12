"""Ledger anchoring: the hook payload cwd follows shell `cd`, so the
ledger's home must not — otherwise one `cd subdir && ...` makes every
prior read invisible (observed live: a fresh empty ledger in a subdir
false-blocked an edit of a file recorded in the project root ledger).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import ledger_io  # noqa: E402

from hook_runner import run_hook  # noqa: E402


class ResolveRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.sub = os.path.join(self.root, "a", "b")
        os.makedirs(self.sub)

    def tearDown(self):
        self.tmp.cleanup()

    def test_env_project_dir_wins(self):
        got = ledger_io.resolve_root(self.sub, env={"CLAUDE_PROJECT_DIR": self.root})
        self.assertEqual(got, self.root)

    def test_walks_up_to_existing_grounded_dir(self):
        os.makedirs(os.path.join(self.root, ".grounded"))
        got = ledger_io.resolve_root(self.sub, env={})
        self.assertEqual(got, self.root)

    def test_no_anchor_falls_back_to_cwd(self):
        got = ledger_io.resolve_root(self.sub, env={})
        self.assertEqual(got, self.sub)

    def test_bogus_env_dir_is_ignored(self):
        os.makedirs(os.path.join(self.root, ".grounded"))
        got = ledger_io.resolve_root(self.sub, env={"CLAUDE_PROJECT_DIR": "/no/such/dir"})
        self.assertEqual(got, self.root)


class RootIntegrationTest(unittest.TestCase):
    """A cd into a subdir must not orphan the project-root ledger."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.sub = os.path.join(self.root, "pkg")
        os.makedirs(self.sub)
        os.makedirs(os.path.join(self.root, ".grounded"))

    def tearDown(self):
        self.tmp.cleanup()

    def write_ledger(self, read_files):
        with open(os.path.join(self.root, ".grounded", "ledger.json"), "w") as f:
            json.dump({"read_files": read_files, "verified_urls": {},
                       "known_pkgs": {}, "warned": {}}, f)

    def touch(self, *parts):
        p = os.path.join(*parts)
        with open(p, "w") as f:
            f.write("x")
        return p

    def test_edit_from_subdir_cwd_sees_root_ledger(self):
        p = self.touch(self.root, "main.py")
        self.write_ledger({p: 1})
        r = run_hook("pre_gate.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": p}, "cwd": self.sub,
        })
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_read_from_subdir_cwd_accrues_into_root_ledger(self):
        p = self.touch(self.sub, "util.py")
        self.write_ledger({})
        run_hook("post_record.py", {
            "hook_event_name": "PostToolUse", "tool_name": "Read",
            "tool_input": {"file_path": p}, "tool_response": {}, "cwd": self.sub,
        })
        with open(os.path.join(self.root, ".grounded", "ledger.json")) as f:
            self.assertIn(p, json.load(f)["read_files"])
        self.assertFalse(os.path.exists(os.path.join(self.sub, ".grounded")))

    def test_relative_paths_still_resolve_against_real_cwd(self):
        p = self.touch(self.sub, "util.py")
        self.write_ledger({p: 1})
        r = run_hook("pre_gate.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": "util.py"}, "cwd": self.sub,
        })
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
