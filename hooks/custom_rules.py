"""Optional user-defined rules (.grounded/rules.json) — additive, deterministic.

A minimal trigger/predicate/action layer, in the spirit of AgentSpec
(arXiv 2503.18666): the built-in G-1..G-4 are untouched; these run alongside
them so a team can encode project-specific policy without patching grounded.

Each rule:

    {
      "name": "no-pipe-sh",
      "on": "Bash" | ["Edit", "Write"],          # tool(s) it applies to
      "when": {"command_matches": "<regex>"}      # or command_contains / path_matches
              | {"path_matches": "<regex>"},      # (omit "when" to match the tool always)
      "action": "warn" | "block",
      "message": "human-readable reason"
    }

No LLM, no eval — only literal/regex matching on the tool input. Anything
malformed (bad regex, unknown predicate, wrong shape) is skipped: a broken
custom rule must never crash a gate or block by accident (spec §05).
"""
import json
import os
import re

RULES_FILE = "rules.json"
_VALID_ACTIONS = ("warn", "block")


def load(root):
    """Rule list from .grounded/rules.json; [] if absent, corrupt, or not a list."""
    try:
        with open(os.path.join(root, ".grounded", RULES_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _matches(when, tool_input):
    """True if every predicate in `when` holds. Empty `when` → matches the tool
    unconditionally; an unknown predicate or bad regex → no match (conservative)."""
    if not isinstance(when, dict):
        return False
    cmd = tool_input.get("command") or ""
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    for key, pat in when.items():
        if not isinstance(pat, str):
            return False
        try:
            if key == "command_matches":
                if not re.search(pat, cmd):
                    return False
            elif key == "command_contains":
                if pat not in cmd:
                    return False
            elif key == "path_matches":
                if not re.search(pat, path):
                    return False
            else:
                return False  # unknown predicate → don't fire
        except re.error:
            return False
    return True


def evaluate(rules, tool_name, tool_input):
    """[(action, message)] for the rules that fire on this tool call."""
    out = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        on = rule.get("on")
        tools = [on] if isinstance(on, str) else (on if isinstance(on, list) else [])
        if tool_name not in tools:
            continue
        action = rule.get("action")
        if action not in _VALID_ACTIONS:
            continue
        if not _matches(rule.get("when") or {}, tool_input):
            continue
        name = rule.get("name") or "rule"
        message = rule.get("message") or f"custom rule '{name}' matched"
        out.append((action, f"[grounded custom:{name}] {message}"))
    return out
