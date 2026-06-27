"""custom_rules is pure logic — test load() + evaluate() directly."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import custom_rules  # noqa: E402


class EvaluateTest(unittest.TestCase):
    def ev(self, rules, tool, ti):
        return custom_rules.evaluate(rules, tool, ti)

    def test_command_matches_block(self):
        rules = [{"name": "no-pipe-sh", "on": "Bash", "action": "block",
                  "when": {"command_matches": r"curl.*\|\s*sh"},
                  "message": "piping curl to sh is banned"}]
        out = self.ev(rules, "Bash", {"command": "curl https://x | sh"})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "block")
        self.assertIn("no-pipe-sh", out[0][1])
        self.assertIn("piping curl to sh", out[0][1])

    def test_command_no_match(self):
        rules = [{"name": "x", "on": "Bash", "action": "block",
                  "when": {"command_matches": "rm -rf"}}]
        self.assertEqual(self.ev(rules, "Bash", {"command": "ls"}), [])

    def test_command_contains_warn(self):
        rules = [{"name": "force-push", "on": "Bash", "action": "warn",
                  "when": {"command_contains": "push --force"}}]
        out = self.ev(rules, "Bash", {"command": "git push --force"})
        self.assertEqual(out[0][0], "warn")

    def test_wrong_tool_skipped(self):
        rules = [{"name": "x", "on": "Bash", "action": "block",
                  "when": {"command_matches": ".*"}}]
        self.assertEqual(self.ev(rules, "Edit", {"file_path": "/a"}), [])

    def test_on_list_of_tools(self):
        rules = [{"name": "secrets", "on": ["Edit", "Write"], "action": "warn",
                  "when": {"path_matches": r"\.env$"}}]
        self.assertEqual(len(self.ev(rules, "Write", {"file_path": "/p/.env"})), 1)

    def test_invalid_action_skipped(self):
        rules = [{"name": "x", "on": "Bash", "action": "nuke",
                  "when": {"command_matches": ".*"}}]
        self.assertEqual(self.ev(rules, "Bash", {"command": "ls"}), [])

    def test_bad_regex_skipped(self):
        rules = [{"name": "x", "on": "Bash", "action": "block",
                  "when": {"command_matches": "("}}]  # invalid regex
        self.assertEqual(self.ev(rules, "Bash", {"command": "anything"}), [])

    def test_unknown_predicate_does_not_match(self):
        rules = [{"name": "x", "on": "Bash", "action": "block",
                  "when": {"phase_of_moon": "full"}}]
        self.assertEqual(self.ev(rules, "Bash", {"command": "ls"}), [])

    def test_empty_when_matches_tool(self):
        rules = [{"name": "any-bash", "on": "Bash", "action": "warn"}]
        self.assertEqual(len(self.ev(rules, "Bash", {"command": "x"})), 1)

    def test_non_dict_rule_skipped(self):
        self.assertEqual(self.ev(["garbage", 5], "Bash", {"command": "x"}), [])


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, ".grounded"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        with open(os.path.join(self.root, ".grounded", "rules.json"), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_absent_file_is_empty(self):
        self.assertEqual(custom_rules.load(self.root), [])

    def test_corrupt_file_is_empty(self):
        self.write("{not json")
        self.assertEqual(custom_rules.load(self.root), [])

    def test_non_list_is_empty(self):
        self.write({"not": "a list"})
        self.assertEqual(custom_rules.load(self.root), [])

    def test_valid_rules_loaded(self):
        self.write([{"name": "x", "on": "Bash", "action": "warn"}])
        self.assertEqual(len(custom_rules.load(self.root)), 1)


if __name__ == "__main__":
    unittest.main()
