"""Negative-cache TTL: a 404/absent-package verdict must not outlive 10min.

A freshly published package or a revived URL would otherwise stay blocked
for the whole session — a stuck false positive, the worst failure mode.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import pre_gate  # noqa: E402
import urlcheck  # noqa: E402


class CacheEntryTest(unittest.TestCase):
    def test_fresh_negative_entry_is_served(self):
        section = {"k": [404, int(time.time())]}
        self.assertEqual(pre_gate._cache_get(section, "k"), 404)

    def test_expired_negative_entry_is_a_miss(self):
        section = {"k": [404, int(time.time()) - pre_gate.NEGATIVE_TTL_SECONDS - 1]}
        self.assertIsNone(pre_gate._cache_get(section, "k"))

    def test_legacy_plain_negative_is_served_without_ttl(self):
        self.assertEqual(pre_gate._cache_get({"k": 404}, "k"), 404)
        self.assertIs(pre_gate._cache_get({"k": False}, "k"), False)

    def test_positive_entries_are_plain_and_served(self):
        self.assertEqual(pre_gate._cache_get({"k": 200}, "k"), 200)
        self.assertIs(pre_gate._cache_get({"k": True}, "k"), True)

    def test_put_wraps_negatives_and_not_positives(self):
        section = {}
        pre_gate._cache_put(section, "dead", 404, negative=True)
        pre_gate._cache_put(section, "ok", 200, negative=False)
        self.assertIsInstance(section["dead"], list)
        self.assertEqual(section["dead"][0], 404)
        self.assertEqual(section["ok"], 200)


class TtlIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.path.realpath(self.tmp.name)
        self._check_url = urlcheck.check_url

    def tearDown(self):
        urlcheck.check_url = self._check_url
        self.tmp.cleanup()

    def write_ledger(self, verified_urls):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            json.dump({"read_files": {}, "verified_urls": verified_urls,
                       "known_pkgs": {}, "warned": {}}, f)

    def webfetch_payload(self, url):
        return {"hook_event_name": "PreToolUse", "tool_name": "WebFetch",
                "tool_input": {"url": url, "prompt": "x"}, "cwd": self.cwd}

    def test_fresh_negative_blocks_without_network(self):
        self.write_ledger({"https://a.com/dead": [404, int(time.time())]})
        urlcheck.check_url = lambda url: self.fail("cache should answer")
        rc = pre_gate.gate_webfetch(self.webfetch_payload("https://a.com/dead"))
        self.assertEqual(rc, 2)

    def test_expired_negative_rechecks_and_unblocks(self):
        old = int(time.time()) - pre_gate.NEGATIVE_TTL_SECONDS - 1
        self.write_ledger({"https://a.com/dead": [404, old]})
        urlcheck.check_url = lambda url: 200  # the URL came back to life
        rc = pre_gate.gate_webfetch(self.webfetch_payload("https://a.com/dead"))
        self.assertEqual(rc, 0)
        with open(os.path.join(self.cwd, ".grounded", "ledger.json")) as f:
            urls = json.load(f)["verified_urls"]
        self.assertEqual(urls["https://a.com/dead"], 200)


if __name__ == "__main__":
    unittest.main()
