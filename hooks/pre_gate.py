"""PreToolUse hook: gate actions on evidence in the ledger.

Thin entrypoint: stdin JSON -> verdicts -> exit code.
exit 0 = pass, exit 2 = block (stderr is fed back to the model). WARN
verdicts allow the call but inject context the model sees, via the
documented `hookSpecificOutput.additionalContext` JSON output.

Spec §05 — false positives are worse than misses: when state is
unreadable, fail open; block only when absence of evidence is unambiguous.
"""
import json
import os
import sys
import time

import install_scan
import ledger_io
import manifest_scan
import registry
import shell_scan
import urlcheck
import verdict

GATED_FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Generous caps that only bound a pathological arg list; the real latency
# guard is the wall-clock _Budget below (cached answers cost no network, so a
# command installing many already-seen packages stays fast regardless).
MAX_REGISTRY_LOOKUPS = 25
MAX_URL_CHECKS = 10
NETWORK_BUDGET_SECONDS = 5.0

# Injected once after any warning. A warning that repeats on every retry
# reads like an escalating problem and sends the model chasing it; saying
# it is advisory and non-repeating keeps the task on course.
WARN_GUIDANCE = (
    "[grounded] The warning above is advisory and will not be repeated this "
    "session. Do not retry the call or change course just to clear it — "
    "verify the evidence if it matters, then continue the task."
)


class _Budget:
    """Total wall-clock allowance for network lookups in one hook call.

    Each lookup has its own short timeout, but five registry probes plus
    three URL checks could stack toward ~20s of gate latency. Past the
    budget, uncached lookups are skipped (fail open); caches still apply.
    """

    def __init__(self, seconds=NETWORK_BUDGET_SECONDS):
        self.deadline = time.monotonic() + seconds

    def exhausted(self):
        return time.monotonic() >= self.deadline


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _cacheable(status):
    """Only definitive liveness is worth remembering; 403/5xx/None may be
    transient and must self-heal on the next attempt."""
    return status is not None and (200 <= status < 400 or status in (404, 410, 0))


# A negative verdict (404 URL, absent package) can go stale — a package
# published five minutes ago is the canonical stuck false positive.
NEGATIVE_TTL_SECONDS = 600


def _cache_get(section, key):
    """Cached value, honoring the [value, ts] negative-entry form.

    Expired negatives read as a miss so they get re-checked; legacy plain
    negatives (pre-TTL ledgers) stay valid for the session as before.
    """
    entry = section.get(key)
    if isinstance(entry, list) and len(entry) == 2:
        value, ts = entry
        if isinstance(ts, (int, float)) \
                and time.time() - ts > NEGATIVE_TTL_SECONDS:
            return None
        return value
    return entry


def _cache_put(section, key, value, negative):
    section[key] = [value, int(time.time())] if negative else value


def _gate_urls(urls, ledger, budget):
    """Verdicts for fetch targets; returns (stops, warn_pairs, dirty) where
    warn_pairs are (dedup_key, reason)."""
    stops, warns, dirty = [], [], False
    for url in urls[:MAX_URL_CHECKS]:
        if not urlcheck.is_checkable(url):
            continue
        key = urlcheck.normalize_url(url)
        status = _cache_get(ledger["verified_urls"], key)
        if status is None:
            if budget.exhausted():
                continue  # unchecked ≠ dead — skip silently, fail open
            status = urlcheck.check_url(key)
            if _cacheable(status):
                _cache_put(ledger["verified_urls"], key, status,
                           negative=status in (404, 410, 0))
                dirty = True
        v = verdict.gate_url(url, status)
        if v.decision == verdict.STOP:
            stops.append(v.reason)
        elif v.decision == verdict.WARN:
            warns.append((f"g3:{key}", v.reason))
    return stops, warns, dirty


def _claim_warns(ledger, warn_pairs):
    """Once-per-session warnings: drop already-claimed keys, mark the rest.

    Re-injecting the same warning on every retry pollutes the model's
    context and invites loops; one mention is the whole point of a WARN.
    """
    fresh = []
    for key, reason in warn_pairs:
        if key not in ledger["warned"]:
            ledger["warned"][key] = int(time.time())
            fresh.append(reason)
    return fresh


def _warn_key(reason, path, mtime, compacted_at=0):
    if reason.startswith("[grounded freshness]"):
        # Compaction-staleness keys on the compaction, not the mtime: a file
        # untouched on disk but evicted by a *second* compaction must warn
        # anew (otherwise the first mtime-key claim silences it forever).
        if "compact" in reason:
            return f"compaction:{path}:{int(compacted_at or 0)}"
        # on-disk freshness keys to the change itself: a new change warns anew
        return f"freshness:{path}:{int(mtime or 0)}"
    return f"g1s-append:{path}"


def gate_file_tool(payload):
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return 0
    cwd = payload.get("cwd") or "."
    root = ledger_io.resolve_root(cwd)
    cfg = ledger_io.load_config(root)
    if not (cfg["g-1"] or cfg["freshness"]):
        return 0
    path = ledger_io.normalize(raw, cwd)
    ledger = ledger_io.load_ledger(root)
    if ledger is None:
        return 0  # corrupt ledger: fail open rather than false-block
    m = _mtime(path) if cfg["freshness"] else None
    ca = ledger.get("compacted_at", 0) if cfg["freshness"] else 0
    v = verdict.gate_file_action(
        payload.get("tool_name"), path, os.path.exists(path),
        ledger["read_files"], mtime=m, compacted_at=ca,
    )
    if v.decision == verdict.STOP and cfg["g-1"]:
        return _emit([v.reason], [])
    warns = []
    if v.decision == verdict.WARN:
        warns = _claim_warns(ledger, [(_warn_key(v.reason, path, m, ca), v.reason)])
        if warns:
            _save_caches(root, ledger)
    return _emit([], warns)


def _manifest_specs(command, cwd, existing):
    """(ecosystem, name) specs declared in manifests that a bare install command
    resolves. Reads each manifest, drops deps already locked/installed (positive
    evidence) and any already in `existing`, and returns the rest for the G-2
    existence check. Unreadable manifests are skipped (fail open)."""
    specs, seen = [], set(existing)
    for eco, rel in install_scan.manifest_installs(command):
        mpath = ledger_io.normalize(rel, cwd)
        try:
            with open(mpath, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue  # no manifest → nothing to verify (fail open)
        grounded = manifest_scan.grounded_names(os.path.dirname(mpath), eco)
        for name in manifest_scan.deps(eco, content):
            spec = (eco, name)
            if name not in grounded and spec not in seen:
                seen.add(spec)
                specs.append(spec)
    return specs


def gate_bash(payload):
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0
    cwd = payload.get("cwd") or "."
    root = ledger_io.resolve_root(cwd)
    cfg = ledger_io.load_config(root)
    ledger = ledger_io.load_ledger(root)
    if ledger is None:
        return 0  # corrupt ledger: fail open rather than false-block
    stops, warn_pairs, dirty = [], [], False
    budget = _Budget()

    ca = ledger.get("compacted_at", 0) if cfg["freshness"] else 0
    write_targets = shell_scan.write_targets(command) if cfg["g-1s"] else []
    for raw, mode in write_targets:
        path = ledger_io.normalize(raw, cwd)
        if os.path.isdir(path):
            continue  # cp/mv into a directory: actual file target unresolved
        m = _mtime(path) if cfg["freshness"] else None
        v = verdict.gate_shell_write(path, mode, os.path.exists(path),
                                     ledger["read_files"], mtime=m,
                                     compacted_at=ca)
        if v.decision == verdict.STOP:
            stops.append(v.reason)
        elif v.decision == verdict.WARN:
            warn_pairs.append((_warn_key(v.reason, path, m, ca), v.reason))

    if cfg["g-1s"]:
        for hint in shell_scan.batch_write_hints(command):
            warn_pairs.append((f"g1s-batch:{hint}", (
                f"[grounded G-1] This command performs a batch in-place write "
                f"({hint}) whose targets are resolved at run time — grounded "
                "cannot verify they were read. Make sure you have actually "
                "seen the files this will modify."
            )))

    if cfg["g-3"]:
        url_stops, url_warns, dirty = _gate_urls(shell_scan.fetch_urls(command),
                                                 ledger, budget)
        stops.extend(url_stops)
        warn_pairs.extend(url_warns)

    package_specs = install_scan.package_specs(command) if cfg["g-2"] else []
    if cfg["g-2"]:
        package_specs = list(package_specs)
        package_specs.extend(_manifest_specs(command, cwd, package_specs))
    for ecosystem, name in package_specs[:MAX_REGISTRY_LOOKUPS]:
        key = f"{ecosystem}:{name}"
        exists = _cache_get(ledger["known_pkgs"], key)
        if exists is None:
            if budget.exhausted():
                continue  # unchecked ≠ hallucinated — skip, fail open
            exists = registry.check_package(ecosystem, name)
            if exists is not None:  # only cache definitive answers
                _cache_put(ledger["known_pkgs"], key, exists,
                           negative=exists is False)
                dirty = True
        v = verdict.gate_package(ecosystem, name, exists)
        if v.decision == verdict.STOP:
            stops.append(v.reason)
        elif exists is True and cfg["g-2-recent"] and not budget.exhausted():
            # opt-in recency tell: an existing-but-freshly-published package may
            # be a squatted hallucination. Advisory only (WARN), never blocks.
            created = registry.package_created_ts(ecosystem, name)
            av = verdict.gate_package_age(name, created, time.time())
            if av.decision == verdict.WARN:
                warn_pairs.append((f"g2recent:{key}", av.reason))

    warns = []
    if not stops:  # a blocked call delivers no warns — don't claim them yet
        warns = _claim_warns(ledger, warn_pairs)
        dirty = dirty or bool(warns)
    if dirty:
        _save_caches(root, ledger)
    return _emit(stops, warns)


def _save_caches(cwd, ledger):
    """Persist lookup caches and claimed warns without clobbering
    concurrent read accruals."""
    ledger_io.update_ledger(cwd, lambda fresh: (
        fresh["verified_urls"].update(ledger["verified_urls"]),
        fresh["known_pkgs"].update(ledger["known_pkgs"]),
        fresh["warned"].update(ledger["warned"]),
    ))


def gate_webfetch(payload):
    url = (payload.get("tool_input") or {}).get("url") or ""
    if not url:
        return 0
    root = ledger_io.resolve_root(payload.get("cwd") or ".")
    if not ledger_io.load_config(root)["g-3"]:
        return 0
    ledger = ledger_io.load_ledger(root)
    if ledger is None:
        return 0  # corrupt ledger: fail open rather than false-block
    stops, warn_pairs, dirty = _gate_urls([url], ledger, _Budget())
    warns = []
    if not stops:
        warns = _claim_warns(ledger, warn_pairs)
        dirty = dirty or bool(warns)
    if dirty:
        _save_caches(root, ledger)
    return _emit(stops, warns)


def _emit(stops, warns):
    if stops:
        sys.stderr.write("\n".join(stops) + "\n")
        return 2
    if warns:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": "\n".join(warns + [WARN_GUIDANCE]),
        }}))
    return 0


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    tool_name = payload.get("tool_name") or ""
    if tool_name == "Bash":
        return gate_bash(payload)
    if tool_name == "WebFetch":
        return gate_webfetch(payload)
    if tool_name in GATED_FILE_TOOLS:
        return gate_file_tool(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
