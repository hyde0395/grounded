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


class FreshnessTest(unittest.TestCase):
    """v0.5: a read goes stale when the file changes on disk afterwards."""

    def test_edit_stale_file_is_warn(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 100}, mtime=200)
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("/p/a.py", v.reason)
        self.assertIn("changed", v.reason)

    def test_edit_fresh_file_is_pass(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 100}, mtime=99.5)
        self.assertEqual(v.decision, verdict.PASS)

    def test_mtime_within_slack_is_pass(self):
        # ledger timestamps are second-truncated; one second of slack
        # absorbs that, so mtime == ts + slack must not warn
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 100}, mtime=101)
        self.assertEqual(v.decision, verdict.PASS)

    def test_unknown_mtime_is_pass(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 100}, mtime=None)
        self.assertEqual(v.decision, verdict.PASS)

    def test_non_numeric_ledger_timestamp_fails_open(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": "garbage"}, mtime=200)
        self.assertEqual(v.decision, verdict.PASS)

    def test_shell_write_stale_file_is_warn(self):
        v = verdict.gate_shell_write("/p/a.py", "truncate", file_exists=True,
                                     read_files={"/p/a.py": 100}, mtime=200)
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("changed", v.reason)

    def test_shell_write_fresh_file_is_pass(self):
        v = verdict.gate_shell_write("/p/a.py", "truncate", file_exists=True,
                                     read_files={"/p/a.py": 100}, mtime=100)
        self.assertEqual(v.decision, verdict.PASS)


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


class GateUrlTest(unittest.TestCase):
    def test_alive_statuses_pass(self):
        for status in (200, 204, 301, 308):
            v = verdict.gate_url("https://a.com", status)
            self.assertEqual(v.decision, verdict.PASS, status)

    def test_dead_statuses_stop(self):
        for status in (404, 410, 0):
            v = verdict.gate_url("https://a.com/dead", status)
            self.assertEqual(v.decision, verdict.STOP, status)
            self.assertIn("https://a.com/dead", v.reason)
            self.assertIn("G-3", v.reason)

    def test_ambiguous_statuses_warn(self):
        for status in (401, 403, 429, 500, 503, None):
            v = verdict.gate_url("https://a.com", status)
            self.assertEqual(v.decision, verdict.WARN, status)


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


class CompactionStalenessTest(unittest.TestCase):
    """A file read before a compaction may have been evicted from context,
    so the ledger still says 'read' while the model no longer holds it."""

    def test_read_before_compaction_is_warn(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 100}, compacted_at=150)
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("compact", v.reason)
        self.assertIn("/p/a.py", v.reason)

    def test_read_after_compaction_is_pass(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 200}, compacted_at=150)
        self.assertEqual(v.decision, verdict.PASS)

    def test_no_compaction_is_pass(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 100}, compacted_at=0)
        self.assertEqual(v.decision, verdict.PASS)

    def test_unread_file_still_stops_regardless_of_compaction(self):
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={}, compacted_at=150)
        self.assertEqual(v.decision, verdict.STOP)

    def test_shell_write_read_before_compaction_is_warn(self):
        v = verdict.gate_shell_write("/p/a.py", "truncate", file_exists=True,
                                     read_files={"/p/a.py": 100}, compacted_at=150)
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("compact", v.reason)

    def test_compaction_warn_even_when_mtime_is_fresh(self):
        # mtime says nothing changed on disk, but the read predates the
        # compaction, so the in-context copy may be gone → still warn
        v = verdict.gate_file_action("Edit", "/p/a.py", file_exists=True,
                                     read_files={"/p/a.py": 100}, mtime=100,
                                     compacted_at=150)
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("compact", v.reason)


class PackageAgeTest(unittest.TestCase):
    NOW = 1_000_000_000

    def test_recent_warns(self):
        v = verdict.gate_package_age("freshpkg", self.NOW - 5 * 86400, self.NOW)
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("freshpkg", v.reason)

    def test_old_passes(self):
        v = verdict.gate_package_age("old", self.NOW - 100 * 86400, self.NOW)
        self.assertEqual(v.decision, verdict.PASS)

    def test_unknown_date_passes(self):
        self.assertEqual(
            verdict.gate_package_age("x", None, self.NOW).decision, verdict.PASS)

    def test_threshold_boundary_warns(self):
        v = verdict.gate_package_age("x", self.NOW - 30 * 86400, self.NOW)
        self.assertEqual(v.decision, verdict.WARN)


if __name__ == "__main__":
    unittest.main()
