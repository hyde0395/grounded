"""registry network code is tested offline via an injected opener."""
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


if __name__ == "__main__":
    unittest.main()
