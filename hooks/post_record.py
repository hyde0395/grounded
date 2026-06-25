"""PostToolUse hook: record evidence into the ledger.

Observe-only — never blocks, always exit 0 (spec §03: post-record accrues,
pre-gate inspects). Evidence sources for G-1: Read, Grep on a single file,
Edit/Write (you know the content you just wrote), Bash read-only viewers
(`cat`/`less`/`more`/`bat`/`view`/`head`/`tail`, `sed -n` print mode).
"""
import json
import os
import re
import shlex
import sys
import time

import ledger_io
import shell_scan
import urlcheck

RECORDING_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
# Pure readers — they print a file's content and never modify it, so seeing
# their target is read evidence just like `cat`. A segment ends at pipes,
# separators, or redirections. Partial views (`head -5`, `tail -20`) still
# count: the hook cannot correlate the viewed range with an Edit's target
# (Edit matches a string, not a line number), and the project favours avoiding
# false STOPs over catching a partial read (§02 "오탐 < 누락"). The `isfile()`
# guard in read_targets neutralizes flag values like the `5` in `head -n 5`.
READER_SEGMENT = re.compile(
    r"(?:^|[|;&]\s*)(?:cat|less|more|bat|view|head|tail)\b([^|;&><]*)")
# `sed -n ...p file` prints (reads) the file. `sed -i` rewrites it (a write
# vector, never read evidence — G-1s), so credit only the `-n` print form with
# no in-place flag.
SED_SEGMENT = re.compile(r"(?:^|[|;&]\s*)sed\b([^|;&><]*)")
# `git diff`/`git show` print the post-image path in the diff header; seeing
# the full diff of a file is read evidence for it.
GIT_SEGMENT = re.compile(r"(?:^|[|;&]\s*)git\b")
GIT_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(.+)$", re.MULTILINE)
# Grep content-mode lines look like `path:lineno:match`.
GREP_CONTENT_LINE = re.compile(r"^(.+?):\d+:")
MAX_RESPONSE_LINES = 2000


def _response_text(tool_response):
    """Best-effort plain text of a tool_response across payload shapes."""
    if isinstance(tool_response, str):
        return tool_response
    if not isinstance(tool_response, dict):
        return ""
    return "\n".join(v for k in ("stdout", "output", "content")
                     for v in [tool_response.get(k)] if isinstance(v, str))


def _existing_file_args(arg_str, cwd):
    """Non-flag tokens of a command segment that exist on disk."""
    found = []
    try:
        tokens = shlex.split(arg_str)
    except ValueError:
        return found
    for tok in tokens:
        if tok.startswith("-"):
            continue
        if os.path.isfile(ledger_io.normalize(tok, cwd)):
            found.append(tok)
    return found


def read_targets(command, cwd):
    """File args of read-only commands (cat/less/head/tail/…, sed -n) on disk."""
    found = []
    for match in READER_SEGMENT.finditer(command):
        found.extend(_existing_file_args(match.group(1), cwd))
    for match in SED_SEGMENT.finditer(command):
        try:
            tokens = shlex.split(match.group(1))
        except ValueError:
            continue
        in_place = any(t == "-i" or t.startswith(("-i", "--in-place"))
                       for t in tokens)
        prints = any(t == "-n" or t == "--quiet" or t == "--silent"
                     for t in tokens)
        if prints and not in_place:
            found.extend(_existing_file_args(match.group(1), cwd))
    return found


def extract_paths(tool_name, tool_input, tool_response, cwd, grep_evidence=True):
    raw = []
    if tool_name in RECORDING_TOOLS:
        p = tool_input.get("file_path") or tool_input.get("notebook_path")
        if p:
            raw.append(p)
    elif tool_name == "Grep" and grep_evidence:
        p = tool_input.get("path")
        if p and os.path.isfile(ledger_io.normalize(p, cwd)):
            raw.append(p)
        elif tool_input.get("output_mode") == "content":
            # content mode shows the matched lines themselves — credit the
            # files they came from (a bare filename listing proves nothing)
            lines = _response_text(tool_response).splitlines()
            for line in lines[:MAX_RESPONSE_LINES]:
                m = GREP_CONTENT_LINE.match(line)
                if m and os.path.isfile(ledger_io.normalize(m.group(1), cwd)):
                    raw.append(m.group(1))
    elif tool_name == "Bash":
        return _bash_paths(tool_input, tool_response, cwd)
    return [ledger_io.normalize(p, cwd) for p in raw]


def _effective_cwd(command, cwd):
    """cwd adjusted for a leading `cd <dir>`, so `cd sub && cat a` reads sub/a."""
    cd = shell_scan.leading_cd(command)
    if not cd:
        return cwd
    cd = os.path.expanduser(cd)
    return cd if os.path.isabs(cd) else os.path.join(cwd, cd)


def _git_root(start):
    """Nearest ancestor (start included) holding a .git, else start.

    git diff/show print paths relative to the repo root, which is not the cwd
    when the command runs from a subdirectory."""
    probe = os.path.realpath(start)
    while True:
        if os.path.isdir(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return start
        probe = parent


def _bash_paths(tool_input, tool_response, cwd):
    command = tool_input.get("command") or ""
    base = _effective_cwd(command, cwd)
    out = [ledger_io.normalize(t, base) for t in read_targets(command, base)]
    # A truncating write (`>`/tee) succeeded: the model authored the file's
    # entire current content. Appends and sed -i still leave content unseen.
    out.extend(ledger_io.normalize(t, base)
               for t, mode in shell_scan.write_targets(command)
               if mode == shell_scan.TRUNCATE)
    if GIT_SEGMENT.search(command):
        git_base = _git_root(base)
        for m in GIT_DIFF_HEADER.finditer(_response_text(tool_response)):
            p = ledger_io.normalize(m.group(1), git_base)
            if os.path.isfile(p):  # resolving may still miss; never mis-accrue
                out.append(p)
    return out


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    cwd = payload.get("cwd") or "."
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    root = ledger_io.resolve_root(cwd)
    cfg = ledger_io.load_config(root)
    paths = extract_paths(tool_name, tool_input, payload.get("tool_response"),
                          cwd, grep_evidence=cfg["grep-evidence"])
    url = tool_input.get("url") if tool_name == "WebFetch" else None
    if not paths and not url:
        return 0
    now = int(time.time())

    def record(ledger):
        for p in paths:
            ledger["read_files"][p] = now
        if url:
            # PostToolUse only fires on success, so the fetch went through
            ledger["verified_urls"][urlcheck.normalize_url(url)] = 200

    ledger_io.update_ledger(root, record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
