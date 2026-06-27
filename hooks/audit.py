"""Optional append-only audit trail of gate decisions (opt-in `audit` rule).

grounded's ledger is keyed current-state, not an event log (see ledger-schema's
"What it is, and what it isn't"). When a team needs accountability — *what did
grounded block or warn on, and why* — this writes one JSON line per surfaced
decision to `.grounded/audit.jsonl`. It is the event-log axis grounded
deliberately keeps OUT of the ledger, offered separately and off by default so
it never bloats the hot-path state. Pattern after Open Agent Passport
(arXiv 2603.20953): deterministic decision + durable record.

Best-effort: auditing must never crash or block a gate, so every failure is
swallowed.
"""
import json
import os
import time

AUDIT_FILE = "audit.jsonl"


def record(root, events):
    """Append `events` ([{decision, reason}]) as timestamped JSONL. No-op on
    empty input or any I/O trouble."""
    if not events:
        return
    path = os.path.join(root, ".grounded", AUDIT_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = int(time.time())
        with open(path, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(
                    {"ts": ts, "decision": e["decision"], "reason": e["reason"]},
                    ensure_ascii=False) + "\n")
    except OSError:
        pass
