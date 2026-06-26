"""registry network code is tested offline via an injected opener."""
import datetime
import json as _json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import registry  # noqa: E402


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def close(self):
        pass


def opener_returning(status):
    def opener(req, timeout=None):
        opener.last_url = req.full_url
        return FakeResponse(status)
    return opener


def opener_raising(exc):
    def opener(req, timeout=None):
        raise exc
    return opener


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "msg", {}, None)


class CheckPackageTest(unittest.TestCase):
    def test_200_means_exists(self):
        self.assertIs(registry.check_package("pypi", "requests",
                                             opener=opener_returning(200)), True)

    def test_404_means_absent(self):
        self.assertIs(registry.check_package("pypi", "nope",
                                             opener=opener_raising(http_error(404))), False)

    def test_410_means_absent(self):
        self.assertIs(registry.check_package("npm", "gone",
                                             opener=opener_raising(http_error(410))), False)

    def test_403_means_unknown(self):
        self.assertIsNone(registry.check_package("crates", "blocked",
                                                 opener=opener_raising(http_error(403))))

    def test_network_error_means_unknown(self):
        self.assertIsNone(registry.check_package("pypi", "x",
                                                 opener=opener_raising(urllib.error.URLError("down"))))

    def test_timeout_means_unknown(self):
        self.assertIsNone(registry.check_package("pypi", "x",
                                                 opener=opener_raising(TimeoutError())))

    def test_scoped_npm_name_is_url_quoted(self):
        op = opener_returning(200)
        registry.check_package("npm", "@types/node", opener=op)
        self.assertIn("%40types%2Fnode", op.last_url)

    def test_unknown_ecosystem_is_unknown(self):
        self.assertIsNone(registry.check_package("brew", "jq",
                                                 opener=opener_returning(200)))

    def test_rubygems_200_means_exists(self):
        self.assertIs(registry.check_package("rubygems", "rails",
                                             opener=opener_returning(200)), True)

    def test_rubygems_404_means_absent(self):
        self.assertIs(registry.check_package("rubygems", "nope",
                                             opener=opener_raising(http_error(404))), False)

    def test_packagist_keeps_vendor_slash_in_url(self):
        # Packagist paths are /p2/vendor/name.json — the slash must survive
        op = opener_returning(200)
        registry.check_package("packagist", "monolog/monolog", opener=op)
        self.assertIn("/p2/monolog/monolog.json", op.last_url)

    def test_packagist_404_means_absent(self):
        self.assertIs(registry.check_package("packagist", "no/such",
                                             opener=opener_raising(http_error(404))), False)


class FakeBodyResponse:
    def __init__(self, body, status=200):
        self._body = body.encode() if isinstance(body, str) else body
        self.status = status

    def read(self):
        return self._body

    def close(self):
        pass


def body_opener(body, status=200):
    def opener(req, timeout=None):
        return FakeBodyResponse(body, status)
    return opener


class PackageCreatedTest(unittest.TestCase):
    def ts(self, eco, name, body):
        return registry.package_created_ts(eco, name, opener=body_opener(body))

    def test_npm_created_date(self):
        body = _json.dumps({"time": {"created": "2011-10-27T18:12:23.342Z"}})
        ts = self.ts("npm", "react", body)
        self.assertEqual(datetime.datetime.utcfromtimestamp(ts).date(),
                         datetime.date(2011, 10, 27))

    def test_crates_created_date(self):
        body = _json.dumps({"crate": {"created_at": "2014-11-10T21:00:00Z"}})
        ts = self.ts("crates", "serde", body)
        self.assertEqual(datetime.datetime.utcfromtimestamp(ts).date(),
                         datetime.date(2014, 11, 10))

    def test_pypi_unsupported_returns_none(self):
        self.assertIsNone(self.ts("pypi", "x", "{}"))

    def test_missing_field_returns_none(self):
        self.assertIsNone(self.ts("npm", "x", _json.dumps({"time": {}})))

    def test_bad_json_returns_none(self):
        self.assertIsNone(self.ts("npm", "x", "not json"))

    def test_network_error_returns_none(self):
        self.assertIsNone(registry.package_created_ts(
            "npm", "x", opener=opener_raising(urllib.error.URLError("down"))))


if __name__ == "__main__":
    unittest.main()
