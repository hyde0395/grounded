"""Total network deadline: one hook call may not stack lookups past budget."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import pre_gate  # noqa: E402
import urlcheck  # noqa: E402


def ledger(verified_urls=None):
    return {"read_files": {}, "verified_urls": verified_urls or {},
            "known_pkgs": {}}


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self._check_url = urlcheck.check_url

    def tearDown(self):
        urlcheck.check_url = self._check_url

    def test_exhausted_budget_skips_uncached_lookup(self):
        def boom(url):
            raise AssertionError("network touched after deadline")
        urlcheck.check_url = boom
        stops, warns, dirty = pre_gate._gate_urls(
            ["https://a.com/x"], ledger(), pre_gate._Budget(seconds=0))
        self.assertEqual((stops, warns, dirty), ([], [], False))

    def test_cached_dead_url_still_blocks_after_deadline(self):
        urlcheck.check_url = lambda url: self.fail("cache should answer")
        stops, warns, dirty = pre_gate._gate_urls(
            ["https://a.com/dead"], ledger({"https://a.com/dead": 404}),
            pre_gate._Budget(seconds=0))
        self.assertEqual(len(stops), 1)
        self.assertFalse(dirty)

    def test_fresh_budget_performs_lookup(self):
        urlcheck.check_url = lambda url: 404
        stops, warns, dirty = pre_gate._gate_urls(
            ["https://a.com/x"], ledger(), pre_gate._Budget())
        self.assertEqual(len(stops), 1)
        self.assertTrue(dirty)


if __name__ == "__main__":
    unittest.main()
