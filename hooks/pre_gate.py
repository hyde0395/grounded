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

import ledger_io
import registry
import shell_scan
import verdict

GATED_FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAX_REGISTRY_LOOKUPS = 5


def gate_file_tool(payload):
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
        payload.get("tool_name"), path, os.path.exists(path), ledger["read_files"]
    )
    if v.decision == verdict.STOP:
        sys.stderr.write(v.reason + "\n")
        return 2
    return 0


def gate_bash(payload):
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0
    cwd = payload.get("cwd") or "."
    ledger = ledger_io.load_ledger(cwd)
    if ledger is None:
        return 0  # corrupt ledger: fail open rather than false-block
    stops, warns = [], []

    for raw, mode in shell_scan.write_targets(command):
        path = ledger_io.normalize(raw, cwd)
        v = verdict.gate_shell_write(path, mode, os.path.exists(path),
                                     ledger["read_files"])
        if v.decision == verdict.STOP:
            stops.append(v.reason)
        elif v.decision == verdict.WARN:
            warns.append(v.reason)

    dirty = False
    for ecosystem, name in shell_scan.package_specs(command)[:MAX_REGISTRY_LOOKUPS]:
        key = f"{ecosystem}:{name}"
        exists = ledger["known_pkgs"].get(key)
        if exists is None:
            exists = registry.check_package(ecosystem, name)
            if exists is not None:  # only cache definitive answers
                ledger["known_pkgs"][key] = exists
                dirty = True
        v = verdict.gate_package(ecosystem, name, exists)
        if v.decision == verdict.STOP:
            stops.append(v.reason)

    if dirty:
        ledger_io.save_ledger(cwd, ledger)
    if stops:
        sys.stderr.write("\n".join(stops) + "\n")
        return 2
    if warns:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": "\n".join(warns),
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
    if tool_name in GATED_FILE_TOOLS:
        return gate_file_tool(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
