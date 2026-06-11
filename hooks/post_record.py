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

RECORDING_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
# A `cat` segment ends at pipes, separators, or redirections.
CAT_SEGMENT = re.compile(r"(?:^|[|;&]\s*)cat\b([^|;&><]*)")


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


def extract_paths(tool_name, tool_input, cwd):
    raw = []
    if tool_name in RECORDING_TOOLS:
        p = tool_input.get("file_path") or tool_input.get("notebook_path")
        if p:
            raw.append(p)
    elif tool_name == "Grep":
        p = tool_input.get("path")
        if p and os.path.isfile(ledger_io.normalize(p, cwd)):
            raw.append(p)
    elif tool_name == "Bash":
        raw.extend(cat_targets(tool_input.get("command") or "", cwd))
    return [ledger_io.normalize(p, cwd) for p in raw]


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    cwd = payload.get("cwd") or "."
    tool_input = payload.get("tool_input") or {}
    paths = extract_paths(payload.get("tool_name") or "", tool_input, cwd)
    if not paths:
        return 0
    ledger = ledger_io.load_ledger(cwd)
    if ledger is None:  # corrupt: heal with a fresh ledger
        ledger = ledger_io.default_ledger()
    now = int(time.time())
    for p in paths:
        ledger["read_files"][p] = now
    ledger_io.save_ledger(cwd, ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
