"""Pure verdict logic — no I/O, no LLM (spec §03 'verdict' component, §05 model).

Every rule converges to one of three decisions. WARN is reserved for rules
where evidence is ambiguous (G-2/G-3); G-1 evidence is binary, so it only
emits PASS or STOP.
"""
from collections import namedtuple

PASS = "pass"
WARN = "warn"
STOP = "stop"

Verdict = namedtuple("Verdict", ["decision", "reason"])


def gate_file_action(tool_name, path, file_exists, read_files):
    """G-1: a file may only be edited if it was read this session.

    `path` must already be normalized; `read_files` is the ledger section
    mapping normalized paths to read timestamps.
    """
    if tool_name == "Write" and not file_exists:
        return Verdict(PASS, "creating a new file needs no prior read")
    if path in read_files:
        return Verdict(PASS, "file was read this session")
    return Verdict(
        STOP,
        f"[grounded G-1] No record of reading {path} in this session. "
        "Do not edit from guesswork — read the file with the Read tool "
        "first, then retry.",
    )
