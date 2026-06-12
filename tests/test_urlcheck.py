"""urlcheck network code is tested offline via an injected opener."""
import os
import socket
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import urlcheck  # noqa: E402


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def close(self):
        pass


def opener_returning(status):
    def opener(req, timeout=None):
        opener.calls = getattr(opener, "calls", 0) + 1
        opener.last_url = req.full_url
        opener.last_method = req.get_method()
        return FakeResponse(status)
    return opener


def opener_raising(exc):
    def opener(req, timeout=None):
        raise exc
    return opener


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "msg", {}, None)


class CheckUrlTest(unittest.TestCase):
    def test_200_returns_200(self):
        self.assertEqual(urlcheck.check_url("https://a.com/x",
                                            opener=opener_returning(200)), 200)

    def test_404_returns_404(self):
        self.assertEqual(urlcheck.check_url("https://a.com/x",
                                            opener=opener_raising(http_error(404))), 404)

    def test_410_returns_410(self):
        self.assertEqual(urlcheck.check_url("https://a.com/x",
                                            opener=opener_raising(http_error(410))), 410)

    def test_403_returns_403(self):
        self.assertEqual(urlcheck.check_url("https://a.com/x",
                                            opener=opener_raising(http_error(403))), 403)

    def test_dns_failure_returns_zero(self):
        exc = urllib.error.URLError(socket.gaierror(8, "nodename nor servname"))
        self.assertEqual(urlcheck.check_url("https://no.example",
                                            opener=opener_raising(exc)), 0)

    def test_connection_refused_returns_none(self):
        exc = urllib.error.URLError(ConnectionRefusedError())
        self.assertIsNone(urlcheck.check_url("https://a.com",
                                             opener=opener_raising(exc)))

    def test_timeout_returns_none(self):
        self.assertIsNone(urlcheck.check_url("https://a.com",
                                             opener=opener_raising(TimeoutError())))

    def test_head_405_retries_with_get(self):
        calls = []

        def opener(req, timeout=None):
            calls.append(req.get_method())
            if req.get_method() == "HEAD":
                raise http_error(405)
            return FakeResponse(200)

        self.assertEqual(urlcheck.check_url("https://a.com", opener=opener), 200)
        self.assertEqual(calls, ["HEAD", "GET"])

    def test_uses_head_by_default(self):
        op = opener_returning(200)
        urlcheck.check_url("https://a.com", opener=op)
        self.assertEqual(op.last_method, "HEAD")

    def test_fragment_stripped_from_request(self):
        op = opener_returning(200)
        urlcheck.check_url("https://a.com/page#section", opener=op)
        self.assertEqual(op.last_url, "https://a.com/page")


class IsCheckableTest(unittest.TestCase):
    def test_public_https_is_checkable(self):
        self.assertTrue(urlcheck.is_checkable("https://docs.python.org/3/"))

    def test_localhost_and_loopback_are_not(self):
        for url in ("http://localhost:3000/", "http://127.0.0.1:8000/x",
                    "http://[::1]:5173/", "http://0.0.0.0:80/"):
            self.assertFalse(urlcheck.is_checkable(url), url)

    def test_private_ranges_are_not(self):
        for url in ("http://10.0.0.5/", "http://192.168.1.10/",
                    "http://172.20.3.4/", "http://myhost.local/"):
            self.assertFalse(urlcheck.is_checkable(url), url)

    def test_non_http_schemes_are_not(self):
        self.assertFalse(urlcheck.is_checkable("ftp://a.com/f"))
        self.assertFalse(urlcheck.is_checkable("file:///etc/hosts"))


class NormalizeUrlTest(unittest.TestCase):
    def test_strips_fragment_keeps_query(self):
        self.assertEqual(urlcheck.normalize_url("https://a.com/p?q=1#frag"),
                         "https://a.com/p?q=1")


if __name__ == "__main__":
    unittest.main()
