"""Package registry existence lookup (G-2 evidence source).

Returns tri-state: True (exists), False (definitively absent), None (unknown —
network trouble, rate limit, unsupported ecosystem). Per spec §10, unknown
must never block: lookups are best-effort with a short timeout, and callers
treat None as PASS.
"""
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "grounded/0.5 (+https://github.com/hyde0395/grounded)"

REGISTRIES = {
    "npm": "https://registry.npmjs.org/{name}",
    "pypi": "https://pypi.org/simple/{name}/",
    "crates": "https://crates.io/api/v1/crates/{name}",
}


def check_package(ecosystem, name, timeout=2.5, opener=None):
    """True if the package exists, False if the registry says 404/410,
    None if we cannot tell."""
    if ecosystem not in REGISTRIES:
        return None
    opener = opener or urllib.request.urlopen
    url = REGISTRIES[ecosystem].format(name=urllib.parse.quote(name, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = opener(req, timeout=timeout)
        ok = 200 <= getattr(resp, "status", 0) < 300
        resp.close()
        return True if ok else None
    except urllib.error.HTTPError as e:
        return False if e.code in (404, 410) else None
    except Exception:
        return None
