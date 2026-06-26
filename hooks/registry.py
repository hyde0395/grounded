"""Package registry existence lookup (G-2 evidence source).

Returns tri-state: True (exists), False (definitively absent), None (unknown —
network trouble, rate limit, unsupported ecosystem). Per spec §10, unknown
must never block: lookups are best-effort with a short timeout, and callers
treat None as PASS.
"""
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "grounded/0.5 (+https://github.com/hyde0395/grounded)"

# (url template, safe chars to keep unencoded). Packagist names are
# vendor/name, so its slash must survive quoting.
REGISTRIES = {
    "npm": ("https://registry.npmjs.org/{name}", ""),
    "pypi": ("https://pypi.org/simple/{name}/", ""),
    "crates": ("https://crates.io/api/v1/crates/{name}", ""),
    "rubygems": ("https://rubygems.org/api/v1/gems/{name}.json", ""),
    "packagist": ("https://repo.packagist.org/p2/{name}.json", "/"),
}


def check_package(ecosystem, name, timeout=2.5, opener=None):
    """True if the package exists, False if the registry says 404/410,
    None if we cannot tell."""
    if ecosystem not in REGISTRIES:
        return None
    opener = opener or urllib.request.urlopen
    template, safe = REGISTRIES[ecosystem]
    url = template.format(name=urllib.parse.quote(name, safe=safe))
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


# Only npm and crates expose a first-publish date on the same endpoint used for
# the existence check, so the recency signal is free (no extra round trip) for
# them and unavailable for the rest (their existence endpoints carry no date).
_CREATED_FIELD = {
    "npm": lambda d: (d.get("time") or {}).get("created"),
    "crates": lambda d: (d.get("crate") or {}).get("created_at"),
}


def _parse_day(value):
    """Unix ts (UTC midnight) of an ISO-8601 date's day part, or None.

    Day granularity sidesteps timezone/fractional-second parsing differences
    across Python versions — plenty for a 'recently published' heuristic."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        dt = datetime.datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


def package_created_ts(ecosystem, name, timeout=2.5, opener=None):
    """Unix ts the package was first published (day granularity), or None for
    an unsupported ecosystem / network trouble / unparseable response."""
    field = _CREATED_FIELD.get(ecosystem)
    if field is None:
        return None
    opener = opener or urllib.request.urlopen
    template, safe = REGISTRIES[ecosystem]
    url = template.format(name=urllib.parse.quote(name, safe=safe))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = opener(req, timeout=timeout)
        body = resp.read()
        resp.close()
        return _parse_day(field(json.loads(body)))
    except Exception:
        return None
