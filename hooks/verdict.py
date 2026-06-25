"""Pure verdict logic — no I/O, no LLM (spec §03 'verdict' component, §05 model).

Every rule converges to one of three decisions. WARN is reserved for rules
where evidence is ambiguous (G-2/G-3); G-1 evidence is binary, so it only
emits PASS or STOP.
"""
from collections import namedtuple

PASS = "pass"
WARN = "warn"
STOP = "stop"

# Ledger timestamps are second-truncated and some filesystems keep coarse
# mtimes, so a fresh read can trail its own mtime by just under a second.
FRESHNESS_SLACK = 1

Verdict = namedtuple("Verdict", ["decision", "reason"])

# Human-facing registry names for G-2 messages (lookup itself lives in
# registry.py; this module stays free of I/O imports).
REGISTRY_LABELS = {"npm": "npm registry", "pypi": "PyPI", "crates": "crates.io",
                   "rubygems": "RubyGems", "packagist": "Packagist"}


def _stale_verdict(path, read_files, mtime, compacted_at=0):
    """WARN if the recorded read can no longer be trusted; None otherwise.

    Two independent causes, both advisory (never block):
    - the read predates a compaction, so the content may have been evicted
      from context even though the ledger still records it;
    - the file changed on disk after it was read.
    """
    ts = read_files.get(path)
    if not isinstance(ts, (int, float)):
        return None  # unknown state is not evidence of staleness — fail open
    if compacted_at and ts < compacted_at:
        return Verdict(
            WARN,
            f"[grounded freshness] {path} was read before this session was "
            "compacted, so the content may have dropped out of context. "
            "Proceeding, but re-read the file before relying on it.",
        )
    if mtime is None or mtime <= ts + FRESHNESS_SLACK:
        return None
    return Verdict(
        WARN,
        f"[grounded freshness] {path} has changed on disk since it was read "
        "this session. Proceeding, but the content you remember may be stale "
        "— re-read the file before relying on it.",
    )


def gate_file_action(tool_name, path, file_exists, read_files, mtime=None,
                     compacted_at=0):
    """G-1: a file may only be edited if it was read this session.

    `path` must already be normalized; `read_files` is the ledger section
    mapping normalized paths to read timestamps. `mtime` (optional) enables
    the on-disk freshness check; `compacted_at` (optional) enables the
    compaction-staleness check — both only warn, never block.
    """
    if tool_name == "Write" and not file_exists:
        return Verdict(PASS, "creating a new file needs no prior read")
    if path in read_files:
        return _stale_verdict(path, read_files, mtime, compacted_at) \
            or Verdict(PASS, "file was read this session")
    return Verdict(
        STOP,
        f"[grounded G-1] No record of reading {path} in this session. "
        "Do not edit from guesswork — read the file with the Read tool "
        "first, then retry.",
    )


def gate_shell_write(path, mode, file_exists, read_files, mtime=None,
                     compacted_at=0):
    """G-1 for shell-mediated writes (sed -i, tee, redirections).

    Truncating or rewriting a file never seen destroys content blindly →
    STOP. Appending preserves what's there, so it only warns.
    """
    if not file_exists:
        return Verdict(PASS, "creating a new file needs no prior read")
    if path in read_files:
        return _stale_verdict(path, read_files, mtime, compacted_at) \
            or Verdict(PASS, "file was read this session")
    if mode == "append":
        return Verdict(
            WARN,
            f"[grounded G-1] This command appends to {path}, which was never "
            "read this session. The append will proceed, but consider reading "
            "the file first to make sure the addition fits what is already there.",
        )
    return Verdict(
        STOP,
        f"[grounded G-1] This command overwrites {path} ({mode}) but there is "
        "no record of reading it this session. Read the file first (Read tool "
        "or cat), then retry.",
    )


def gate_url(url, status):
    """G-3: only an unambiguously dead URL (404/410/DNS) may block.

    `status` is from urlcheck.check_url: HTTP int, 0 for DNS-dead, None for
    unknown. Bot walls answer 403, flaky servers answer 5xx — those warn,
    never block (spec §07).
    """
    if status is not None and 200 <= status < 400:
        return Verdict(PASS, "URL verified alive")
    if status in (404, 410, 0):
        detail = "DNS resolution failed" if status == 0 else f"HTTP {status}"
        return Verdict(
            STOP,
            f"[grounded G-3] {url} is dead ({detail}). Fetching or citing it "
            "would build on a hallucinated source. Find a live URL (e.g. via "
            "search) before retrying.",
        )
    detail = "no response (timeout or connection trouble)" if status is None \
        else f"HTTP {status}"
    return Verdict(
        WARN,
        f"[grounded G-3] Could not positively verify {url} ({detail} — "
        "possibly bot protection or a transient error). Proceeding, but treat "
        "the result with care and prefer a source you can verify.",
    )


def gate_package(ecosystem, name, exists):
    """G-2: a package may only be installed if the registry confirms it exists.

    `exists` is tri-state from registry.check_package; None (unreachable,
    rate-limited) must pass — a network hiccup is not evidence of hallucination.
    """
    if exists is False:
        return Verdict(
            STOP,
            f"[grounded G-2] Package '{name}' was not found on "
            f"{REGISTRY_LABELS.get(ecosystem, ecosystem)}. This usually means a "
            "hallucinated or misspelled package name. Search the registry for "
            "the correct name before installing.",
        )
    return Verdict(PASS, "package exists" if exists else "registry unknown — not blocking")
