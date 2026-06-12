import json
import os
import subprocess
import sys
import tempfile
import unittest

from hook_runner import HOOKS_DIR, run_hook


def payload(cwd, source):
    return {"hook_event_name": "SessionStart", "cwd": cwd, "source": source}


class SessionStartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # realpath: macOS /var is a symlink to /private/var
        self.cwd = os.path.realpath(self.tmp.name)
        self.ledger = os.path.join(self.cwd, ".grounded", "ledger.json")

    def tearDown(self):
        self.tmp.cleanup()

    def read_ledger(self):
        with open(self.ledger) as f:
            return json.load(f)

    def seed_ledger(self, read_files):
        os.makedirs(os.path.dirname(self.ledger), exist_ok=True)
        with open(self.ledger, "w") as f:
            json.dump({"read_files": read_files, "verified_urls": {}, "known_pkgs": {}}, f)

    def test_startup_creates_empty_ledger(self):
        r = run_hook("session_start.py", payload(self.cwd, "startup"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.read_ledger(), {"read_files": {}, "verified_urls": {},
                                              "known_pkgs": {}, "warned": {}})

    def test_startup_resets_existing_ledger(self):
        self.seed_ledger({"/old/file.py": 1})
        run_hook("session_start.py", payload(self.cwd, "startup"))
        self.assertEqual(self.read_ledger()["read_files"], {})

    def test_clear_resets_existing_ledger(self):
        self.seed_ledger({"/old/file.py": 1})
        run_hook("session_start.py", payload(self.cwd, "clear"))
        self.assertEqual(self.read_ledger()["read_files"], {})

    def test_resume_keeps_existing_ledger(self):
        self.seed_ledger({"/kept/file.py": 1})
        run_hook("session_start.py", payload(self.cwd, "resume"))
        self.assertEqual(self.read_ledger()["read_files"], {"/kept/file.py": 1})

    def test_resume_with_corrupt_ledger_heals_to_empty(self):
        os.makedirs(os.path.dirname(self.ledger), exist_ok=True)
        with open(self.ledger, "w") as f:
            f.write("{not json")
        r = run_hook("session_start.py", payload(self.cwd, "resume"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.read_ledger()["read_files"], {})

    def test_injects_prompt_rule_as_additional_context(self):
        r = run_hook("session_start.py", payload(self.cwd, "startup"))
        out = json.loads(r.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("[grounded]", ctx)
        self.assertIn("verified", ctx.lower())

    def test_garbage_stdin_exits_zero(self):
        r = subprocess.run(
            [sys.executable, os.path.join(HOOKS_DIR, "session_start.py")],
            input="not json", capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
