# grounded

> Make AI coding agents prove it before they act.

**grounded** is a guardrail for [Claude Code](https://claude.com/claude-code) that deterministically stops an agent from acting on things it never verified — editing files it never read, installing packages it never checked, citing URLs it never fetched.

No LLM in the loop. No network calls (for the core rule). Just hooks, a local ledger, and exit codes.

![version](https://img.shields.io/badge/version-0.6.2-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![engine](https://img.shields.io/badge/judgment-deterministic%20%C2%B7%20no%20LLM-brightgreen)
![category](https://img.shields.io/badge/category-grounding%20enforcement-purple)

![grounded blocking a hallucinated package install and a blind sed -i, then passing a proper read-then-edit](demo/demo.gif)

*A real session: a hallucinated `pip install` blocked by G-2, a blind `sed -i` blocked by G-1, and a proper read-then-edit passing — ending with the file actually changed.*

---

## Why

The most expensive failure of AI coding agents isn't the dangerous command — existing security guardrails catch those. It's the agent **claiming it verified something it never did**: editing from a guess, installing a hallucinated package, citing a dead link. Every ungrounded action stacks the next action on a false premise, and a human pays the bill re-verifying everything.

This isn't hypothetical. A [USENIX Security 2025 study](https://arxiv.org/abs/2406.10279) of 576,000 generated code samples found that **5.2–21.7% of LLM-recommended packages don't exist** (205,474 unique hallucinated names) — a supply-chain attack surface now known as [*slopsquatting*](https://en.wikipedia.org/wiki/Slopsquatting). G-2 closes it at the moment of `install`.

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

Warnings are **idempotent**: each (rule, target) pair warns at most once per
session (tracked in the ledger's `warned` section), and every injection ends
with an explicit *"advisory — will not repeat, don't retry to clear it"*
note. Injected context is itself a side effect; without this, a warning that
reappears on every retry reads like an escalating problem and can send the
agent chasing the warning instead of the task.

## Rules

| Rule | What it enforces | Status |
|---|---|---|
| **G-1** Read-before-edit | A file can't be edited unless it was read this session (Read, Grep, shell viewers `cat`/`less`/`head`/`tail`/`bat`/`view`/`sed -n`, `git diff`/`git show` output) | ✅ v0.1 |
| **G-1s** Shell-write gating | `sed -i`, `perl -i`, `awk -i inplace`, `tee`, `>`, `>\|`, `dd of=`, `truncate`, `cp`/`mv` onto a never-read file → blocked; `>>` (append) and batch writes with run-time targets (`find -exec sed -i`, `xargs sed -i`) → warning only | ✅ v0.2 |
| **G-2** Verify-before-install | A package can't be installed unless it exists on its registry — npm (npm/pnpm/yarn/bun), PyPI (pip/uv/poetry), crates.io (cargo), RubyGems (gem/bundler), Packagist (composer). Also checks **dependencies declared in a manifest** when a bare install resolves it (`npm install` → `package.json`, `pip install -r`, `poetry/bundle/composer install`, `cargo build`); deps already in a lockfile/installed are trusted as-is | ✅ v0.2 |
| **G-3** Fetch-before-cite | Dead URLs (404/410/DNS failure) are blocked; ambiguous ones (403/5xx/timeout) only warn | ✅ v0.3 |
| **G-4** Live-links-in-answers | On `Stop`, the final answer text is scanned for the URLs it cites: a dead one (404/410/DNS) blocks the turn **once** so the model fixes it; ambiguous ones (403/5xx) warn. Links inside code blocks are treated as illustrative and skipped | ✅ v0.8 |
| **freshness** Stale-read detection | A file that changed on disk *after* it was read → warning to re-read before relying on it | ✅ v0.5 |

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
unreachable registry is not evidence of hallucination. Negative results
expire after 10 minutes and get re-checked, so the package you published
five minutes ago doesn't stay blocked for the whole session. A cached re-check costs
~50ms, and one command's lookups share a 5s total budget — past it, uncached
targets are skipped (fail open) rather than stalling your tool call. G-3
probes with HEAD only, skips private/localhost hosts (a dev server that isn't
running yet is normal), and skips `curl -X POST`/`--data` calls (API
endpoints legitimately reject HEAD probes).

## Configuration

Every rule can be toggled individually — in `.grounded/config.json` in the
project, or with the `GROUNDED_DISABLE` environment variable (comma-separated,
case-insensitive; env wins over file):

```json
{ "G-1": true, "G-1s": true, "G-2": true, "G-3": false, "G-4": true, "freshness": true, "grep-evidence": false }
```

```bash
GROUNDED_DISABLE="g-3,grep_evidence"
```

`grep-evidence: false` is strict mode: a Grep match no longer counts as
having read the file — only a full read does. A missing or corrupt config
enables everything (the toggles exist to opt out, so failing to read them
must not change default behavior).

One rule is **opt-in** and ships off: `g-2-recent`. When enabled
(`{ "g-2-recent": true }`), G-2 additionally *warns* (never blocks) if an
existing package was first published very recently — a weak tell for a
hallucinated name an attacker has already squatted. It is off by default
because legitimate new packages share the trait, so it would otherwise warn on
them. Only npm and crates.io expose a publish date on the existence-check
endpoint, so the signal is free there and unavailable for the rest.

## Install

### As a plugin (recommended)

```
/plugin marketplace add hyde0395/grounded
/plugin install grounded@grounded
```

### As a project hook

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

**Requirements:** Python 3 on PATH as `python3` or `python` (stdlib only — zero
dependencies). On Windows, hooks run under Git Bash (installed with Git); if
Python is missing entirely, grounded fails open — your tools keep working,
just unguarded.

### Verify it yourself in 60 seconds

Every claim above is reproducible in any project with grounded installed:

1. Ask Claude to run `pip install reqests` (the classic typo) → blocked
   before execution: `[grounded G-2] Package 'reqests' was not found on PyPI…`
2. Ask it to run `sed -i 's/a/b/' <a file it hasn't opened>` → blocked:
   `[grounded G-1] … no record of reading it this session.`
3. Ask it to read that file first, then edit it → passes silently, and the
   change lands.

The GIF at the top is exactly this, recorded unscripted against a live
session (`demo/demo.tape` reproduces it).

## Network access & privacy

No telemetry, no analytics, no calls home. The complete list of network
traffic grounded can generate:

- **G-2** — one existence lookup against the public registry
  (registry.npmjs.org / pypi.org / crates.io / rubygems.org /
  repo.packagist.org) the first time a session installs a given package;
  the answer is cached in the local ledger. Installs that name a custom
  index/registry (`--index-url`, `--registry`, `--source`, …) are skipped —
  grounded can't query a private registry, so it won't second-guess one.
- **G-3** — one HEAD probe (GET fallback) against a URL the agent is about
  to fetch or cite; private, loopback, and link-local hosts (incl. cloud
  metadata `169.254.169.254`) are never probed.

Both rules can be turned off (`{"G-2": false, "G-3": false}` in
`.grounded/config.json`), every lookup is capped at 2.5s with a 5s
per-command budget, and G-1/G-1s/freshness work entirely offline. All state
lives in `.grounded/ledger.json` inside your project.

## Honest limitations

We'd rather tell you up front than have you find out:

- **Text responses are only partly covered.** Hooks can't see the model mid-sentence, but the `Stop` event hands over the finished answer — so G-4 *can* check one kind of plain-text claim: the links it cites (a dead URL in the answer is blocked even though no fetch tool ran). That works because a URL is a token you can extract and verify deterministically. The harder half — free-form factual claims like "function X returns Y" — stays a structural wall: identifying and verifying an arbitrary claim needs an LLM and a source of truth, which is outside what a deterministic hook can do. A bundled prompt rule only nudges the model there.
- **G-1's read-before-edit overlaps the built-in; its real value is elsewhere.** Claude Code's built-in validation already rejects `Edit` on a never-read file, so that part of G-1 is just defense-in-depth (and a fallback for agents without it). G-1's distinct value is what the built-in *doesn't* do: gating shell-level writes (`sed -i`/`tee`/`cp`/redirects) the built-in ignores entirely, counting `cat`/`grep`/`git diff` as evidence, and the freshness/compaction staleness checks. Read the rule as "grounding for the write paths and time-axis the built-in skips," not "read-before-edit."
- **Bot walls cause false signals.** Cloudflare answering `curl` with 403 doesn't mean the link is dead — which is exactly why G-3 warns instead of blocks on 403.
- **grounded is not an adversarial boundary.** Shell-write gating catches the common idioms (`sed -i`, `tee`, redirections) — the lazy path, not the evasive one. A model that deliberately hides a write behind `python -c` or base64 isn't being sloppy, it's evading a guardrail; that's a security problem, and the answer is sandboxing and permissions — the layer grounded explicitly complements, not replaces.
- **What we promise:** grounding enforcement at the tool boundary. **What we don't:** catching every hallucination.

## Related work

grounded independently converges on two ideas the research community has
recently formalized:

- **[AgentSpec](https://arxiv.org/abs/2503.18666)** (ICSE 2026) frames agent
  safety as *runtime enforcement*: trigger → predicate → enforcement rules
  living outside the model. grounded is that model specialized to Claude
  Code's hook boundary — matcher → ledger-backed verdict → exit code, ~30ms
  per invocation (measured on an M-series MacBook), no LLM in the judgment
  path.
- **[CaMeL](https://arxiv.org/abs/2503.18813)** (Google DeepMind) treats the
  LLM as an untrusted component and puts a deterministic layer in control.
  CaMeL applies that stance to prompt injection; grounded applies it to
  ungrounded action.
- The package-hallucination problem G-2 targets is quantified in
  [*"We Have a Package for You!"*](https://arxiv.org/abs/2406.10279)
  (USENIX Security 2025).

## Development

```bash
python3 -m unittest discover -s tests   # 336 tests, hooks exercised via real stdin/exit-code interface
```

The layout mirrors the architecture: thin entrypoints (`session_start.py`, `post_record.py`, `pre_gate.py`), pure logic (`verdict.py`, `shell_scan.py` — no I/O, no LLM), and side effects at the edges (`ledger_io.py`, `registry.py`, `urlcheck.py`). Network calls take an injectable opener, so the whole suite runs offline.

## Roadmap

| Version | Ships | Kills this failure |
|---|---|---|
| v0.1 ✅ | G-1 read-before-edit + session ledger | editing from a guess |
| v0.2 ✅ | G-2 package-existence check + caching, shell-write gating (`sed -i`, `echo >`, `tee`) | hallucinated installs, Edit-tool bypasses |
| v0.3 ✅ | G-3 URL liveness (block 404·DNS-dead / warn 403·5xx) for WebFetch + curl/wget | citing dead links |
| v0.4 ✅ | marketplace plugin packaging, Windows support (`python3`/`python` launcher), bundled prompt rule for plain-text claims | install friction, text blind spot (partial) |
| v0.5 ✅ | freshness — detect external edits after read, per-rule on/off config, more evidence sources (`git diff`/`git show`, Grep content output), hardening (heredoc-aware parsing, ledger lock, 5s network budget) | acting on stale evidence, false positives on indirect reads |
| v0.6.0 ✅ | batch-write warnings (`find -exec`/`xargs sed -i`), `cp`/`mv` overwrite gating, negative-cache 10-min TTL (re-check published packages), Windows ledger lock (`msvcrt`) | unverified batch writes, stale negative cache, lost accruals on Windows |
| v0.6.1 ✅ | ledger anchored to project root (cwd-drift fix), demo GIF, network-access disclosure + 60-second verification | mis-anchored ledger when cwd ≠ project root |
| v0.6.2 ✅ | compaction-aware staleness — warn before editing a file whose read predates a context compaction, plus a published ledger-schema reference | acting on reads evicted from context by compaction |
| v0.6.3 ✅ | wider G-1 read evidence — shell viewers `less`/`head`/`tail`/`bat`/`view`/`sed -n` count as reads (not just `cat`) | false STOPs after viewing a file with a non-`cat` pager |
| v0.6.4 ✅ | wider G-2 ecosystems — `poetry`/`bun` reuse PyPI/npm; `gem`/`bundler` (RubyGems) and `composer` (Packagist) registries added | hallucinated installs in Ruby/PHP/Poetry/Bun projects |
| v0.6.5 ✅ | wider G-1s shell-write vectors — `>\|` (force-clobber), `dd of=`, `truncate`, `awk -i inplace` join `sed -i`/`tee`/redirects | blind overwrites via less-common write idioms |
| v0.6.6 ✅ | skip G-2 when a custom index/registry is named (`--index-url`/`--registry`/`--source`); exclude link-local & cloud-metadata hosts from G-3 probes | false STOP on legitimate private-registry installs |
| v0.6.7 ✅ | raise per-command lookup caps (wall-clock budget is the real guard); retry a `403` HEAD with GET | unchecked 6th+ package, false WARN on HEAD-hostile-but-live links |
| v0.6.8 ✅ | re-warn on a second compaction (dedup keyed to the compaction); resolve `cd x && …` reads and `git diff` paths against the right directory | lost re-read prompt after re-compaction, missed reads under `cd`/subdir-git |
| v0.7.0 ✅ | G-2 also verifies dependencies declared in a **manifest** (`package.json`, `requirements.txt`/`pyproject.toml`, `Cargo.toml`, `Gemfile`, `composer.json`) on a bare install; lockfile/installed deps trusted, custom-source/private-index manifests skipped | hallucinated dep added to a manifest then installed by a name-less command |
| v0.8.0 ✅ | G-4 speech gate — on `Stop`, scan the finished answer for dead links it cites (block once / warn ambiguous), the first check of plain-text output (code blocks excluded, blocks at most once per turn) | citing a dead link in the answer with no fetch tool ever run |
| v0.9.0 ✅ | opt-in `g-2-recent` — warn (never block) when an *existing* npm/crates package was first published very recently, a weak tell for an already-squatted hallucinated name; off by default (legitimate new packages share the trait) | hallucinated name an attacker registered before you installed it |

## License

[MIT](LICENSE)
