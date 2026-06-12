"""SessionStart hook: initialize .grounded/ledger.json.

Reset on startup/clear. Keep on resume/compact — the conversation still
remembers those reads; wiping them would cause false blocks (spec §05).
Never blocks: always exit 0.

Also injects a grounding prompt rule into the session context. Hooks only
see tool calls; plain-text claims are a structural blind spot (spec §07),
so the rule asks the model to verify before asserting in text.
"""
import json
import sys

import ledger_io

PROMPT_RULE = (
    "[grounded] This session enforces grounding on tool actions (edits, "
    "installs, fetches). Hooks cannot check plain-text output, so apply the "
    "same standard yourself: do not present URLs, package names, or claims "
    "about file/API contents that you have not verified with a tool this "
    "session. If something is unverified, say so explicitly."
)


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    root = ledger_io.resolve_root(payload.get("cwd") or ".")
    source = payload.get("source") or "startup"
    ledger = None
    if source in ("resume", "compact"):
        ledger = ledger_io.load_ledger(root)  # None if corrupt
    if ledger is None:
        ledger = ledger_io.default_ledger()
    ledger_io.save_ledger(root, ledger)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": PROMPT_RULE,
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
