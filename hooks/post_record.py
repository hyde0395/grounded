"""PostToolUse hook: record evidence into the ledger.

Observe-only — never blocks, always exit 0 (spec §03: post-record accrues,
pre-gate inspects). Evidence sources for G-1: Read, Grep on a single file,
Edit/Write (you know the content you just wrote), Bash `cat`.
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
# A `cat` segment ends at pipes, separators, or redirections.
CAT_SEGMENT = re.compile(r"(?:^|[|;&]\s*)cat\b([^|;&><]*)")
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


def cat_targets(command, cwd):
    """File args of `cat` invocations that actually exist on disk."""
    found = []
    for match in CAT_SEGMENT.finditer(command):
        try:
            tokens = shlex.split(match.group(1))
        except ValueError:
            continue
        for tok in tokens:
            if tok.startswith("-"):
                continue
            if os.path.isfile(ledger_io.normalize(tok, cwd)):
                found.append(tok)
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
        command = tool_input.get("command") or ""
        raw.extend(cat_targets(command, cwd))
        # A truncating write (`>`/tee) succeeded: the model authored the file's
        # entire current content. Appends and sed -i still leave content unseen.
        raw.extend(t for t, mode in shell_scan.write_targets(command)
                   if mode == shell_scan.TRUNCATE)
        if GIT_SEGMENT.search(command):
            # git prints paths relative to the repo root; resolving against
            # cwd is only right when they coincide, so isfile() must confirm
            for m in GIT_DIFF_HEADER.finditer(_response_text(tool_response)):
                if os.path.isfile(ledger_io.normalize(m.group(1), cwd)):
                    raw.append(m.group(1))
    return [ledger_io.normalize(p, cwd) for p in raw]


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
