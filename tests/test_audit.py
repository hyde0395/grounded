"""audit is best-effort append-only logging — test it directly."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import audit  # noqa: E402


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, ".grounded"))

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self):
        with open(os.path.join(self.root, ".grounded", "audit.jsonl")) as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_appends_one_line_per_event(self):
        audit.record(self.root, [{"decision": "block", "reason": "[grounded G-2] x"},
                                 {"decision": "warn", "reason": "[grounded G-3] y"}])
        rows = self.lines()
        self.assertEqual([r["decision"] for r in rows], ["block", "warn"])
        self.assertIn("G-2", rows[0]["reason"])
        self.assertIsInstance(rows[0]["ts"], int)

    def test_appends_across_calls(self):
        audit.record(self.root, [{"decision": "block", "reason": "a"}])
        audit.record(self.root, [{"decision": "warn", "reason": "b"}])
        self.assertEqual(len(self.lines()), 2)

    def test_no_events_writes_nothing(self):
        audit.record(self.root, [])
        self.assertFalse(os.path.exists(
            os.path.join(self.root, ".grounded", "audit.jsonl")))

    def test_unwritable_root_does_not_raise(self):
        # a path that cannot be created must be swallowed (auditing never crashes)
        audit.record("/proc/nonexistent-xyz", [{"decision": "block", "reason": "z"}])


if __name__ == "__main__":
    unittest.main()
