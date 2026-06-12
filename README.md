# grounded

> Make AI coding agents prove it before they act.

**grounded** is a guardrail for [Claude Code](https://claude.com/claude-code) that deterministically stops an agent from acting on things it never verified — editing files it never read, installing packages it never checked, citing URLs it never fetched.

No LLM in the loop. No network calls (for the core rule). Just hooks, a local ledger, and exit codes.

![version](https://img.shields.io/badge/version-0.3.0-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![engine](https://img.shields.io/badge/judgment-deterministic%20%C2%B7%20no%20LLM-brightgreen)
![category](https://img.shields.io/badge/category-grounding%20enforcement-purple)

---

## Why

The most expensive failure of AI coding agents isn't the dangerous command — existing security guardrails catch those. It's the agent **claiming it verified something it never did**: editing from a guess, installing a hallucinated package, citing a dead link. Every ungrounded action stacks the next action on a false premise, and a human pays the bill re-verifying everything.

Writing *"don't guess"* in your CLAUDE.md is a **suggestion** the model interprets at runtime — it can be talked out of it. A hook is **enforcement**: it runs no matter what the model thinks.

| | Security guardrails (many) | grounded |
|---|---|---|
| Blocks | dangerous actions (`rm -rf`, force push, secrets) | **ungrounded actions** (edit-without-read, unverified installs, dead links) |
| Asks | "Is this command dangerous?" | "Does this action have evidence?" |
| Judged by | static command patterns | **session behavior history** (stateful) |

grounded is a **complement** to security guardrails, not a replacement.

## How it works

Hooks are stateless one-shot processes, so the state lives in a local ledger. **PostToolUse accrues evidence; PreToolUse demands it.**

```
  tool ran (Read / Grep / cat) ───────────────┐
                                              ▼
  PostToolUse hook ── accrue ──▶  .grounded/ledger.json
                                  { read_files, verified_urls, known_pkgs }
                                              │ look up
  agent attempts Edit / Write ──▶ PreToolUse hook
                                              │
                       ┌──────────────────────┼──────────────────────┐
                       ▼                      ▼                      ▼
                   grounded               uncertain             ungrounded
                   exit 0 · pass     exit 0 · inject warning   exit 2 · block
                                                               (reason fed back
                                                                to the model)
```

Every rule resolves to one of three verdicts — and the design philosophy is **false positives are worse than misses**. If grounded isn't sure, it warns instead of blocking. It only blocks when the absence of evidence is unambiguous.

| Verdict | When | What happens |
|---|---|---|
| **PASS** | evidence is in the ledger | `exit 0`, silent |
| **WARN** | evidence is ambiguous (403, timeout) | `exit 0` + warning injected to the model |
| **STOP** | evidence is clearly absent | `exit 2`, blocked with an actionable reason |

## Rules

| Rule | What it enforces | Status |
|---|---|---|
| **G-1** Read-before-edit | A file can't be edited unless it was read this session (Read, Grep, `cat`) | ✅ v0.1 |
| **G-1s** Shell-write gating | `sed -i`, `perl -i`, `tee`, `>` on a never-read file → blocked; `>>` (append) → warning only | ✅ v0.2 |
| **G-2** Verify-before-install | A package can't be installed unless it exists on its registry (npm / PyPI / crates.io) | ✅ v0.2 |
| **G-3** Fetch-before-cite | Dead URLs (404/410/DNS failure) are blocked; ambiguous ones (403/5xx/timeout) only warn | ✅ v0.3 |

When a rule blocks, the model receives an actionable reason:

```
[grounded G-1] This command overwrites src/auth.py (truncate) but there is no
record of reading it this session. Read the file first (Read tool or cat), then retry.

[grounded G-2] Package 'reqests' was not found on PyPI. This usually means a
hallucinated or misspelled package name. Search the registry for the correct
name before installing.
```

G-2 and G-3 lookups are cached in the session ledger (positive *and*
negative), use a 2.5s timeout, and **never block on network trouble** — an
unreachable registry is not evidence of hallucination. A cached re-check costs
~50ms. G-3 probes with HEAD only, skips private/localhost hosts (a dev server
that isn't running yet is normal), and skips `curl -X POST`/`--data` calls
(API endpoints legitimately reject HEAD probes).

## Install

### As a project hook (today)

Copy `hooks/` into your project and register in `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/hooks/session_start.py\"" }] }
    ],
    "PostToolUse": [
      { "matcher": "Read|Grep|Edit|Write|MultiEdit|NotebookEdit|Bash|WebFetch",
        "hooks": [{ "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/hooks/post_record.py\"" }] }
    ],
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|WebFetch",
        "hooks": [{ "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/hooks/pre_gate.py\"" }] }
    ]
  }
}
```

Requires Python 3 (stdlib only — zero dependencies).

### As a plugin (coming)

The repo is already plugin-shaped (`.claude-plugin/plugin.json` + `hooks/hooks.json`). Marketplace packaging lands with v0.4.

## Honest limitations

We'd rather tell you up front than have you find out:

- **Text responses are invisible to hooks.** If the agent pastes a link or a claim as plain chat text without using a tool, no hook fires. That's a structural limit of hooks; a bundled prompt rule (roadmap v0.4) only partially mitigates it.
- **Recent Claude Code already covers the simplest G-1 case.** Claude Code's built-in validation rejects `Edit` on a never-read file by itself. grounded's G-1 is defense-in-depth there — its own value is the evidence ledger (it counts `cat`/`grep` as reads, tracks freshness, and powers the rules the built-in check doesn't have: shell-level write bypasses, G-2, G-3).
- **Bot walls cause false signals.** Cloudflare answering `curl` with 403 doesn't mean the link is dead — which is exactly why G-3 warns instead of blocks on 403.
- **What we promise:** grounding enforcement at the tool boundary. **What we don't:** catching every hallucination.

## Development

```bash
python3 -m unittest discover -s tests   # 128 tests, hooks exercised via real stdin/exit-code interface
```

The layout mirrors the architecture: thin entrypoints (`session_start.py`, `post_record.py`, `pre_gate.py`), pure logic (`verdict.py`, `shell_scan.py` — no I/O, no LLM), and side effects at the edges (`ledger_io.py`, `registry.py`, `urlcheck.py`). Network calls take an injectable opener, so the whole suite runs offline.

## Roadmap

| Version | Ships | Kills this failure |
|---|---|---|
| v0.1 ✅ | G-1 read-before-edit + session ledger | editing from a guess |
| v0.2 ✅ | G-2 package-existence check + caching, shell-write gating (`sed -i`, `echo >`, `tee`) | hallucinated installs, Edit-tool bypasses |
| v0.3 ✅ | G-3 URL liveness (block 404·DNS-dead / warn 403·5xx) for WebFetch + curl/wget | citing dead links |
| v0.4 | marketplace plugin packaging, bundled prompt rule for plain-text claims | text blind spot (partial) |
| v0.5 | freshness — detect external edits after read, per-rule on/off | acting on stale evidence |

## License

[MIT](LICENSE)
