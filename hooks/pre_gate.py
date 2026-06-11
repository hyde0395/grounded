"""PreToolUse hook: gate actions on evidence in the ledger.

Thin entrypoint: stdin JSON -> verdict -> exit code.
exit 0 = pass, exit 2 = block (stderr is fed back to the model).
Spec §05 — false positives are worse than misses: when state is
unreadable, fail open; block only when absence of evidence is unambiguous.
"""
import json
import os
import sys

import ledger_io
import verdict

GATED_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    tool_name = payload.get("tool_name") or ""
    if tool_name not in GATED_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return 0
    cwd = payload.get("cwd") or "."
    path = ledger_io.normalize(raw, cwd)
    ledger = ledger_io.load_ledger(cwd)
    if ledger is None:
        return 0  # corrupt ledger: fail open rather than false-block
    v = verdict.gate_file_action(
        tool_name, path, os.path.exists(path), ledger["read_files"]
    )
    if v.decision == verdict.STOP:
        sys.stderr.write(v.reason + "\n")
        return 2
    if v.decision == verdict.WARN:
        sys.stderr.write(v.reason + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
