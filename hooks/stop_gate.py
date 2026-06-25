"""Stop hook: G-4 speech gate — catch dead links the answer asserts.

Hooks cannot see plain-text output mid-stream, but the Stop event fires when
the assistant finishes a turn and hands us `transcript_path`. We read the
final answer, extract the URLs it cites, and check liveness with the same
machinery as G-3. A definitively dead link (404/410/DNS) blocks the stop once
(`decision: block`) so the model fixes it; an unverifiable one (403/5xx/
timeout) only injects an advisory. Semantic factual claims stay out of scope
— deterministically un-extractable, and grounded runs no LLM.

Conservative by design (spec §05): code spans are masked out by text_scan, so
illustrative URLs never gate; `stop_hook_active` short-circuits to avoid loops
— we block at most once per stop chain.
"""
import json
import sys

import ledger_io
import text_scan
import urlcheck
import verdict
from pre_gate import (MAX_URL_CHECKS, WARN_GUIDANCE, _Budget, _cache_get,
                      _cache_put, _cacheable, _claim_warns, _save_caches)


def _last_assistant_text(path):
    """Text of the final assistant message in the JSONL transcript; '' on any
    trouble (an unreadable transcript must never block a stop)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant":
            continue
        content = (event.get("message") or {}).get("content")
        if isinstance(content, str):
            if content.strip():
                return content
            continue
        if isinstance(content, list):
            # content is a block array; a trailing tool_use-only message has no
            # text — keep scanning back to the real answer.
            text = "\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text")
            if text.strip():
                return text
    return ""


def _classify(urls, ledger, budget):
    """(dead_urls, ambiguous_pairs, dirty): dead are STOP-worthy, ambiguous are
    WARN-worthy as (warn_key, url). Mirrors pre_gate._gate_urls' liveness path."""
    dead, ambiguous, dirty = [], [], False
    for url in urls[:MAX_URL_CHECKS]:
        if not urlcheck.is_checkable(url):
            continue
        key = urlcheck.normalize_url(url)
        status = _cache_get(ledger["verified_urls"], key)
        if status is None:
            if budget.exhausted():
                continue  # unchecked ≠ dead — fail open
            status = urlcheck.check_url(key)
            if _cacheable(status):
                _cache_put(ledger["verified_urls"], key, status,
                           negative=status in (404, 410, 0))
                dirty = True
        v = verdict.gate_url(url, status)
        if v.decision == verdict.STOP:
            dead.append(url)
        elif v.decision == verdict.WARN:
            ambiguous.append((f"g4:{key}", url))
    return dead, ambiguous, dirty


def _block_reason(dead):
    bullets = "\n".join(f"  - {u}" for u in dead)
    return (
        "[grounded G-4] Your response cites links that appear dead:\n"
        f"{bullets}\n"
        "These point the user to a hallucinated or moved source. Verify each "
        "(e.g. via search) and fix or remove the dead links before answering.")


def _warn_reason(urls):
    listed = ", ".join(urls)
    return (
        f"[grounded G-4] Could not positively verify some links in your "
        f"response ({listed}) — possibly bot protection or a transient error. "
        "Treat them with care and prefer a source you can confirm.")


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    # If we are firing again after our own block, let the stop proceed: block
    # at most once per stop chain (Stop hook loop-safety contract).
    if payload.get("stop_hook_active"):
        return 0
    transcript = payload.get("transcript_path")
    if not transcript:
        return 0
    cwd = payload.get("cwd") or "."
    root = ledger_io.resolve_root(cwd)
    if not ledger_io.load_config(root)["g-4"]:
        return 0
    ledger = ledger_io.load_ledger(root)
    if ledger is None:
        return 0  # corrupt ledger: fail open rather than false-block
    urls = text_scan.answer_urls(_last_assistant_text(transcript))
    if not urls:
        return 0

    dead, ambiguous, dirty = _classify(urls, ledger, _Budget())
    if dead:
        if dirty:
            _save_caches(root, ledger)
        # A blocked stop delivers no advisory — don't claim ambiguous warns yet.
        print(json.dumps({"decision": "block", "reason": _block_reason(dead)}))
        return 0

    fresh = _claim_warns(ledger, ambiguous)  # returns urls of unwarned entries
    if fresh or dirty:
        _save_caches(root, ledger)
    if fresh:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": "\n".join([_warn_reason(fresh), WARN_GUIDANCE]),
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
