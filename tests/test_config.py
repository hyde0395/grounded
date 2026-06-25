"""Per-rule toggles: .grounded/config.json + GROUNDED_DISABLE env override."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import ledger_io  # noqa: E402

from hook_runner import run_hook  # noqa: E402

ALL_RULES = ("g-1", "g-1s", "g-2", "g-3", "g-4", "freshness", "grep-evidence")


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, data):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_no_config_enables_everything(self):
        cfg = ledger_io.load_config(self.cwd)
        for rule in ALL_RULES:
            self.assertTrue(cfg[rule], rule)

    def test_file_disables_a_rule(self):
        self.write_config({"G-2": False})
        cfg = ledger_io.load_config(self.cwd)
        self.assertFalse(cfg["g-2"])
        self.assertTrue(cfg["g-1"])

    def test_file_keys_are_case_and_underscore_insensitive(self):
        self.write_config({"grep_evidence": False, "G-1S": False})
        cfg = ledger_io.load_config(self.cwd)
        self.assertFalse(cfg["grep-evidence"])
        self.assertFalse(cfg["g-1s"])

    def test_corrupt_config_fails_open(self):
        self.write_config("{not json")
        cfg = ledger_io.load_config(self.cwd)
        for rule in ALL_RULES:
            self.assertTrue(cfg[rule], rule)

    def test_env_disable_overrides_file(self):
        self.write_config({"G-3": True})
        cfg = ledger_io.load_config(self.cwd, env={"GROUNDED_DISABLE": "G-3, Grep_Evidence"})
        self.assertFalse(cfg["g-3"])
        self.assertFalse(cfg["grep-evidence"])
        self.assertTrue(cfg["g-1"])

    def test_unknown_names_are_ignored(self):
        self.write_config({"G-9": False})
        cfg = ledger_io.load_config(self.cwd, env={"GROUNDED_DISABLE": "bogus"})
        for rule in ALL_RULES:
            self.assertTrue(cfg[rule], rule)


class ToggleIntegrationTest(unittest.TestCase):
    """Disabled rules must stop gating / recording end to end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        p = os.path.join(self.cwd, name)
        with open(p, "w") as f:
            f.write("x")
        return p

    def grounded(self, name, data):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            json.dump(data, f)

    def ledger(self, **sections):
        base = {"read_files": {}, "verified_urls": {}, "known_pkgs": {}}
        base.update(sections)
        self.grounded("ledger.json", base)

    def pre(self, tool_name, tool_input, env=None):
        return run_hook("pre_gate.py", {
            "hook_event_name": "PreToolUse", "tool_name": tool_name,
            "tool_input": tool_input, "cwd": self.cwd,
        }, env=env)

    def test_g1_disabled_allows_edit_of_unread_file(self):
        p = self.touch("a.py")
        self.ledger()
        self.grounded("config.json", {"G-1": False})
        r = self.pre("Edit", {"file_path": p})
        self.assertEqual(r.returncode, 0)

    def test_g1_disabled_via_env(self):
        p = self.touch("a.py")
        self.ledger()
        r = self.pre("Edit", {"file_path": p}, env={"GROUNDED_DISABLE": "g-1"})
        self.assertEqual(r.returncode, 0)

    def test_g1s_disabled_allows_sed_on_unread_file(self):
        p = self.touch("a.py")
        self.ledger()
        self.grounded("config.json", {"G-1s": False})
        r = self.pre("Bash", {"command": f"sed -i 's/a/b/' {p}"})
        self.assertEqual(r.returncode, 0)

    def test_g1s_disabled_does_not_disable_g1(self):
        p = self.touch("a.py")
        self.ledger()
        self.grounded("config.json", {"G-1s": False})
        r = self.pre("Edit", {"file_path": p})
        self.assertEqual(r.returncode, 2)

    def test_g2_disabled_allows_cached_absent_package(self):
        self.ledger(known_pkgs={"pypi:reqests": False})
        self.grounded("config.json", {"G-2": False})
        r = self.pre("Bash", {"command": "pip install reqests"})
        self.assertEqual(r.returncode, 0)

    def test_g3_disabled_allows_cached_dead_url(self):
        self.ledger(verified_urls={"https://a.com/dead": 404})
        self.grounded("config.json", {"G-3": False})
        r = self.pre("WebFetch", {"url": "https://a.com/dead", "prompt": "x"})
        self.assertEqual(r.returncode, 0)

    def test_freshness_disabled_silences_stale_warn(self):
        p = self.touch("a.py")
        self.ledger(read_files={p: 1000})  # mtime is "now" → stale
        self.grounded("config.json", {"freshness": False})
        r = self.pre("Edit", {"file_path": p})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_grep_evidence_disabled_stops_recording_grep(self):
        p = self.touch("a.py")
        self.grounded("config.json", {"grep-evidence": False})
        run_hook("post_record.py", {
            "hook_event_name": "PostToolUse", "tool_name": "Grep",
            "tool_input": {"pattern": "x", "path": p}, "tool_response": {},
            "cwd": self.cwd,
        })
        ledger_file = os.path.join(self.cwd, ".grounded", "ledger.json")
        self.assertFalse(os.path.exists(ledger_file))


if __name__ == "__main__":
    unittest.main()
