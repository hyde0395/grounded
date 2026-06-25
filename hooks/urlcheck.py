"""URL liveness lookup (G-3 evidence source).

Result is an HTTP status int, 0 for DNS-resolution failure (the host does
not exist — as dead as a URL gets), or None when we cannot tell (timeout,
connection refused, SSL trouble). Per spec §05/§07, only unambiguous death
(404/410/0) may block; everything uncertain is a warning at most.

Checks use HEAD (with one GET retry on 405/501) so we never download bodies.
Private/loopback hosts are excluded: a dev server that is not running yet is
normal, not evidence of hallucination.
"""
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "grounded/0.3 (+https://github.com/hyde0395/grounded)"

_PRIVATE_HOST = re.compile(
    r"^(localhost|0\.0\.0\.0|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+"
    r"|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|169\.254\.\d+\.\d+"          # IPv4 link-local incl. cloud metadata
    r"|fe[89ab][0-9a-f]:.*"        # IPv6 link-local (fe80::/10)
    r"|f[cd][0-9a-f]{2}:.*"        # IPv6 unique-local (fc00::/7)
    r"|::1|.*\.local)$"
)


def normalize_url(url):
    """Cache key: fragment is client-side only, never part of liveness."""
    return urllib.parse.urldefrag(url)[0]


def is_checkable(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return bool(host) and not _PRIVATE_HOST.match(host)


def _request(url, method, timeout, opener):
    req = urllib.request.Request(url, method=method,
                                 headers={"User-Agent": USER_AGENT})
    resp = opener(req, timeout=timeout)
    status = getattr(resp, "status", 0)
    resp.close()
    return status


def check_url(url, timeout=2.5, opener=None):
    """HTTP status int; 0 if DNS-dead; None if unknown."""
    opener = opener or urllib.request.urlopen
    url = normalize_url(url)
    method = "HEAD"
    for _ in range(2):
        try:
            return _request(url, method, timeout, opener)
        except urllib.error.HTTPError as e:
            if e.code in (405, 501) and method == "HEAD":
                method = "GET"  # endpoint dislikes HEAD; one retry, then give up
                continue
            return e.code
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            return 0 if isinstance(reason, socket.gaierror) else None
        except Exception:
            return None
    return None
