"""SessionStart hook: initialize .grounded/ledger.json.

Reset on startup/clear. Keep on resume/compact — the conversation still
remembers those reads; wiping them would cause false blocks (spec §05).
Never blocks: always exit 0.
"""
import json
import sys

import ledger_io


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    cwd = payload.get("cwd") or "."
    source = payload.get("source") or "startup"
    ledger = None
    if source in ("resume", "compact"):
        ledger = ledger_io.load_ledger(cwd)  # None if corrupt
    if ledger is None:
        ledger = ledger_io.default_ledger()
    ledger_io.save_ledger(cwd, ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
