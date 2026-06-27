# The grounded ledger

This document describes the on-disk state that grounded's hooks share:
`.grounded/ledger.json`. It is a reference for anyone curious about how the
evidence store is shaped and why — not a spec to copy verbatim. We describe
what our code actually does and where the trade-offs hurt; **this is one way
to do it, not the way.**

> **Scope.** Everything below is read off the implementation in `hooks/`
> (`ledger_io.py`, `post_record.py`, `pre_gate.py`, `stop_gate.py`,
> `verdict.py`, `shell_scan.py`, `install_scan.py`, `manifest_scan.py`,
> `text_scan.py`). If the code and this doc ever disagree, the code wins.

---

## The schema at a glance

```json
{
  "read_files":    { "<abs-path>": <unix-ts-int> },
  "verified_urls": { "<normalized-url>": <status-int | [status, ts]> },
  "known_pkgs":    { "<ecosystem>:<name>": <bool | [bool, ts]> },
  "warned":        { "<warn-key>": <unix-ts-int> },
  "compacted_at":  <unix-ts-int | 0>
}
```

Four maps plus a scalar, in one JSON object. `read_files` is G-1 evidence
(read-before-write), `verified_urls` is G-3 (link liveness), `known_pkgs` is
G-2 (package existence), `warned` is once-per-session warning dedup, and
`compacted_at` records the last compaction so reads taken before it can be
flagged as possibly evicted from the model's context. Negative cache entries
(404 URLs, absent packages) are wrapped as `[value, ts]` with a TTL; everything
else is a plain value. Each section is documented in full below — but the rest
of this page is mostly *why* it's shaped this way, which matters more than the
field list.

---

## What it is, and what it isn't

The ledger is a **single JSON object**, not a JSONL event log. We keep
**current state keyed by the thing a gate will look up**, not an append-only
history of everything that happened.

That choice is the most important thing on this page, so the trade-off is
stated up front:

| | What we chose (keyed state) | What we didn't (per-event log) |
|---|---|---|
| Shape | one JSON object, a few maps | one append-only record per tool call |
| Gate lookup | `path in read_files` — O(1) exact-key membership | scan/replay events to reconstruct "was this read?" |
| Size | bounded by *distinct* files/URLs/pkgs touched | grows with *every* tool call |
| Write pattern | read-modify-write the whole object under a lock | append (cheap), but compaction needed later |
| Can answer "what happened, in order?" | **No** — last write wins per key, history is lost | Yes |
| Can answer "is there evidence for X *right now*?" | **Yes, directly** | only by replay |

The hooks are stateless one-shot processes (Claude Code spawns a fresh process
per tool call), and the only question a gate ever asks is *"is there evidence
for this exact target right now?"* — `Edit(/abs/foo.py)` asks "is
`/abs/foo.py` in `read_files`?". A keyed map answers that in one lookup with no
replay and no ordering logic. We don't need the timeline, so we don't pay to
store it. The cost we accept: the ledger cannot tell you the order things
happened, and a later write to the same key silently overwrites the earlier
value (this is fine — newer evidence supersedes older for the same target).

---

## File location & layout

- Path: `<root>/.grounded/ledger.json`
- Format: pretty-printed JSON (`indent=2`, `ensure_ascii=False`), written via
  a temp file + atomic `os.replace` so a crashed or concurrent write never
  leaves half-written JSON behind.
- `<root>` is **not** necessarily the tool's cwd. The hook payload's cwd
  follows shell `cd`, so anchoring state to it would orphan the ledger the
  moment the session changes directory (we hit this live: a subdir's empty
  ledger false-blocked an edit of a file recorded in the project-root ledger).
  `resolve_root()` prefers, in order: `$CLAUDE_PROJECT_DIR` (if it's a real
  dir) → the nearest ancestor that already contains a `.grounded/` dir → the
  cwd itself.

### Top-level shape

```json
{
  "read_files":    { "<abs-path>": <unix-ts-int> },
  "verified_urls": { "<normalized-url>": <status> },
  "known_pkgs":    { "<ecosystem>:<name>": <bool-or-[bool,ts]> },
  "warned":        { "<warn-key>": <unix-ts-int> },
  "compacted_at":  <unix-ts-int>
}
```

`default_ledger()` produces all five keys (maps empty, `compacted_at` 0). When
loading, each section is defensively merged onto that default — dict sections by
key, the scalar `compacted_at` if numeric — so an older ledger missing `warned`
(added in v0.5) or `compacted_at` (added in v0.6.2) still loads, with the
missing piece reading as empty/0.

### `read_files` — G-1 evidence (read-before-write)

```json
"read_files": {
  "/Users/me/project/app.py": 1782092207,
  "/Users/me/project/util.py": 1782092212
}
```

- **Key**: an absolute, `~`-expanded, symlink-resolved, case-normalized path
  (`normalize()`). This is what lets `Read("./app.py")` ground a later
  `Edit("/Users/me/project/app.py")` — both collapse to the same key.
- **Value**: the unix timestamp (integer seconds) when the read was recorded.
  The timestamp is not just bookkeeping — it powers the **freshness** check:
  if a file's on-disk mtime later exceeds its recorded read-time (plus 1s of
  slack for second-truncation), the gate warns that the remembered content may
  be stale.

What counts as a read (recorded by PostToolUse — see below): `Read`,
`Edit`/`Write`/`MultiEdit`/`NotebookEdit` (you authored the content),
`Grep` on a single file or in content-mode, `cat`, a truncating shell write
(`>` / `tee`), and post-image paths in `git diff`/`git show` output.

### `verified_urls` — G-3 evidence (link liveness)

```json
"verified_urls": {
  "https://docs.python.org/3/": 200,
  "https://example.com/gone": [404, 1782092207]
}
```

- **Key**: the URL with its fragment stripped (`#section` is client-side only
  and never part of liveness), so `…/page` and `…/page#x` share one entry.
- **Value**: an HTTP status integer — or `0` for DNS-resolution failure, the
  deadest a URL gets. A successful `WebFetch` records `200`.
- **Negative entries carry a TTL.** A dead verdict (404/410/0) is stored as
  `[status, recorded_ts]` and treated as expired after 600s, so a URL that
  comes back to life isn't blocked for the whole session. Live entries
  (2xx/3xx) are stored as a **plain int** and held for the session.
- Only *definitive* liveness is cached (2xx–3xx, or 404/410/0). Ambiguous
  answers (403 bot-walls, 5xx, timeouts) are **never cached** — they may be
  transient and must self-heal on the next attempt.

### `known_pkgs` — G-2 evidence (package existence)

```json
"known_pkgs": {
  "pypi:requests": true,
  "pypi:reqests": [false, 1782092207],
  "npm:@types/node": true
}
```

- **Key**: `"<ecosystem>:<name>"`, where ecosystem is `npm`, `pypi`,
  `crates`, `rubygems`, or `packagist`.
- **Value**: `true` (registry confirms it exists) or `false` (registry says
  404/410). As with URLs, a negative is wrapped as `[false, ts]` with the same
  600s TTL — a package published five minutes ago is the canonical stuck false
  positive, and the TTL is how it un-sticks.
- **`None` is never written.** A network hiccup or rate-limit is *not* evidence
  that a package is hallucinated, so an inconclusive lookup leaves no entry and
  the gate passes.

### `warned` — once-per-session warning dedup

```json
"warned": {
  "freshness:/Users/me/project/app.py:1782092300": 1782092301,
  "g1s-append:/Users/me/project/log.txt": 1782092350,
  "g3:https://example.com/maybe": 1782092360
}
```

Not evidence — bookkeeping. A WARN verdict injects advisory context the model
sees; re-injecting the same warning on every retry pollutes context and sends
the model chasing a non-problem, so each warning fires **once per session**.
The key encodes the warning so the dedup is precise:

- `freshness:<path>:<int(mtime)>` — keyed to the *specific* on-disk change, so
  a genuinely new change to the same file warns again.
- `compaction:<path>:<compacted_at>` — read evicted by a context compaction.
  Keyed to the compaction, not the mtime, so a *second* compaction re-warns a
  file that is unchanged on disk (its content may have been dropped again).
- `g1s-append:<path>` — blind append to an unread file.
- `g1s-batch:<hint>` — batch in-place write whose targets are dynamic
  (`xargs sed -i`, `find -exec sed -i`).
- `g3:<normalized-url>` — an ambiguous (un-verifiable) URL.

### `compacted_at` — compaction-staleness marker

```json
"compacted_at": 1782092400
```

A scalar, not a map: the unix timestamp of the last compaction (`0` = none).
Set only when SessionStart fires with `source == "compact"` — the one lifecycle
event that summarizes the transcript, so file content read earlier may no longer
be in the model's context even though `read_files` still records it. When a
later edit targets a file whose recorded read predates `compacted_at`, the gate
emits a WARN (re-read advisory), never a block. `resume` is deliberately left
untouched: it restores the conversation, so the content is presumed intact. This
is a partial mitigation only — hooks cannot see the context window, so it keys
off the *event*, not the actual eviction.

---

## Who writes what: PostToolUse vs PreToolUse

The core design is two hooks over one file: **PostToolUse accrues evidence,
PreToolUse demands it.** They never call each other; the file is the only
channel between them. A third gate, the **Stop hook** (`stop_gate.py`, G-4),
reads the same file — it scans the finished answer for dead links it cites,
reusing the `verified_urls` cache and `warned` dedup, and blocks the turn once
if a link is dead. It writes only those two cache sections, never `read_files`.

### PostToolUse (`post_record.py`) — observe-only, never blocks

Runs after a tool succeeds. Always exits 0. It extracts evidence from the tool
and its response and records it:

- File-reading tools → add the path to `read_files` with `now`.
- `WebFetch` success → record the URL as `200` in `verified_urls` (it fetched,
  so it's alive).
- `Bash` → parse the command (`shell_scan`) for `cat` targets, truncating
  writes, and `git diff`/`git show` post-image paths; record those that exist
  on disk.

It deliberately does **not** record evidence for actions that don't prove the
content was seen: a blind `>>` append, a `cp`/`mv` destination (it holds the
*source's* bytes, unseen), or a `sed -i` rewrite all leave the file
unrecorded.

### PreToolUse (`pre_gate.py`) — the gate

Runs before `Edit`/`Write`/`MultiEdit`/`NotebookEdit`, `Bash`, and `WebFetch`.
Loads the ledger, asks `verdict.py` for a decision per target, and turns it
into an exit code:

| Verdict | Trigger | Effect |
|---|---|---|
| **PASS** | evidence present in the ledger | `exit 0`, silent |
| **WARN** | evidence ambiguous (stale read, blind append, 403/timeout URL) | `exit 0` + inject `additionalContext` the model reads |
| **STOP** | evidence unambiguously absent (unread file, dead URL, absent package) | `exit 2` + reason on stderr, fed back to the model |

The lookup is the simple part — `path in ledger["read_files"]`, `key in
ledger["verified_urls"]`, etc. For G-2/G-3, a cache miss may trigger a live
registry/HTTP check, subject to a per-call **5-second total network budget**
(past it, uncached lookups are skipped → fail open). Any fresh check result is
written back so the next gate is a cache hit.

> **Design philosophy: false positives are worse than misses.** A wrongly
> blocked legitimate action makes a user rip the tool out immediately; a missed
> hallucination is the status quo they already live with. So the gate only
> STOPs when absence of evidence is unambiguous, and degrades to fail-open
> everywhere it's unsure: corrupt ledger → pass, missing section → empty,
> network budget exhausted → skip, unresolvable shell target → skip.

---

## Concurrency: the part that actually bit us

Claude Code runs tool calls in parallel, which means **parallel hook
processes** racing on one file. An unsynchronized read-modify-write loses
writes: process A and B both load `{}`, each adds its own entry, and whoever
saves last erases the other's. This isn't theoretical — the project's own log
records **3 of 4 parallel `Read`s going unrecorded** during development before
the fix (see the comment in `tests/test_post_record.py`). The regression test
that locks the behavior down spawns 12 concurrent recorders and asserts none
are dropped.

The fixes, all in `ledger_io.py`:

- **`update_ledger(root, mutate)`** does the load → mutate → save as one step
  under an **exclusive advisory lock** (`flock` on POSIX, an `msvcrt` region
  lock on Windows). If neither lock primitive exists, it degrades to running
  unlocked rather than crashing.
- **Atomic save**: write to a temp file in the same dir, then `os.replace` —
  readers never see a partial file.
- **Scoped merges**: when PreToolUse persists fresh caches, it merges only
  `verified_urls`/`known_pkgs`/`warned` into the current on-disk ledger
  (`_save_caches`), so it can't clobber a `read_files` entry a concurrent
  PostToolUse just accrued.

---

## Lifecycle

`session_start.py` resets the ledger to empty on a fresh `startup` or `clear`.
On `resume`/`compact` it **keeps** the existing ledger — the conversation still
remembers those reads, and wiping them would cause false blocks. A corrupt
ledger on resume heals to empty rather than crashing.

Corruption handling differs by reader, and on purpose:

- PreToolUse on corrupt ledger → **fail open** (exit 0). A broken state file
  must never become a wall of false blocks.
- PostToolUse on corrupt ledger → **heal to a fresh ledger** and record. Losing
  prior evidence is acceptable; crashing the recorder is not.

---

## Test matrix

378 test methods, all offline (network code is exercised through injected fake
openers, never the real internet — several methods loop over multiple cases, so
the asserted-case count is higher). Run with
`python3 -m unittest discover -s tests` → `Ran 378 tests … OK`. By area:

| File | Count | What it pins down |
|---|--:|---|
| `test_verdict.py` | 32 | Pure decisions: unread→STOP, read→PASS, new-file Write→PASS, freshness slack/stale→WARN, compaction-staleness (read before vs after compaction)→WARN/PASS, shell truncate/inplace→STOP vs append→WARN, URL alive/dead/ambiguous, package exists/absent/unknown. |
| `test_shell_scan.py` | 66 | Shell lexing + write/fetch extraction: `sed -i`/`perl -i`/`awk -i inplace`/`tee`/redirect (`>`,`>\|`)/`dd of=`/`truncate` targets & modes, `cp`/`mv` overwrite (+ `-n`/`-t` bail-outs), batch hints (`xargs`/`find -exec`), leading-`cd` resolution, fetch-URL extraction (GET only, POST skipped), heredoc/quote masking, segment splitting & dedup. |
| `test_install_scan.py` | 42 | Install/dependency parsing (split out of shell_scan): install specs across pip/uv/poetry/npm/pnpm/yarn/bun/cargo/gem/bundler/composer (+ custom-index/registry skip), **bare manifest-install detection** (`npm install`→package.json, `pip install -r`, `poetry/bundle/composer install`, `cargo build`), name extraction & version/extras stripping, sudo/env-prefix, dedup. |
| `test_pre_gate.py` | 48 | The gate end-to-end: edit/Write of unread vs read file (exit 2 vs 0), missing ledger blocks, corrupt ledger fails open, garbage stdin fails open, stale→WARN via `additionalContext` (+ dedup), read-before-compaction→WARN (re-warns on a *second* compaction), shell `sed -i`/truncate→block, append→warn, `cp` onto existing→block, cached absent-package→block & alive→pass without network, **`npm install` with a hallucinated manifest dep→block, real dep→pass, missing manifest→pass, lockfile dep not re-checked**, `WebFetch`/`curl` to cached dead URL→block, localhost not checked. |
| `test_config.py` | 25 | Per-rule toggles via `.grounded/config.json` + `GROUNDED_DISABLE` env (case/underscore-insensitive, env overrides file, unknown names ignored, corrupt config fails open), opt-in defaults (g-2-recent/audit off, custom-rules on), and end-to-end that disabled rules stop gating/recording plus the audit-log and custom-rule integrations. |
| `test_post_record.py` | 34 | Accrual: Read/Grep-single-file/Write record; shell viewers (`cat`/`less`/`head`/`tail`/`bat`/`view`/`sed -n`) record, leading-`cd` resolves relative reads; Grep-on-dir, blind append, `cp` dest, `sed -i` record nothing; truncate write & `git diff`/`git show` post-image paths (resolved against repo root) record; **parallel 12-recorder no-loss test**; lock-free degradation; corrupt-ledger heal. |
| `test_urlcheck.py` | 18 | Liveness: 200/404/410/403 statuses, DNS→0, refused/timeout→None, HEAD→GET retry on 405/403, private/loopback/link-local/metadata hosts not checkable, fragment stripping. |
| `test_registry.py` | 18 | Existence tri-state across npm/PyPI/crates/RubyGems/Packagist: 200→True, 404/410→False, 403/network/timeout→None, scoped-npm URL-quoting, Packagist vendor-slash preserved, unknown ecosystem→None. |
| `test_cache.py` | 7 | Negative-cache TTL: fresh negative served, expired negative is a miss & re-checks (URL revival un-blocks), legacy plain negatives served, positives stored plain. |
| `test_root.py` | 7 | Root anchoring: `$CLAUDE_PROJECT_DIR` wins, walk-up to `.grounded/`, fallback to cwd, bogus env ignored; edits/reads from a subdir cwd still see/accrue into the root ledger. |
| `test_session_start.py` | 9 | Lifecycle: startup/clear reset, resume keeps, resume+corrupt heals to empty, compact marks `compacted_at` while keeping reads, resume does not mark it, prompt-rule injection, garbage stdin exits 0. |
| `test_budget.py` | 3 | Network budget: exhausted budget skips uncached lookups, cached dead URL still blocks after deadline, fresh budget performs the lookup. |
| `test_audit.py` | 4 | Opt-in audit log (`.grounded/audit.jsonl`, separate from the ledger): one JSONL line per surfaced decision (`{ts, decision, reason}`), appends across calls, no events → no file, unwritable path swallowed (auditing never crashes). |
| `test_custom_rules.py` | 19 | User rules (`.grounded/rules.json`): `command_matches`/`command_contains`/`path_matches` predicates, `on` tool match (str or list), block/warn actions, empty-`when` matches tool, and the conservative skips (invalid action, bad regex, unknown predicate, non-dict rule, corrupt/non-list file → no fire). |
| `test_manifest_scan.py` | 25 | Manifest dependency parsing (G-2 input): package.json/requirements.txt/pyproject.toml/Cargo.toml/Gemfile/composer.json name extraction; git/path/url/workspace/platform deps skipped; custom-source/private-index → whole file skipped; corrupt content → `[]`; TOML via `tomllib` or regex fallback; `grounded_names` reads package-lock/node_modules/composer.lock as positive evidence. |
| `test_text_scan.py` | 13 | Pure URL extraction from answer prose: plain/http+https, order-preserving dedup, non-http schemes ignored, code-fence & inline-code masking (illustrative URLs skipped), markdown-link/autolink/paren/quote unwrapping, trailing-punctuation stripping. |
| `test_stop_gate.py` | 13 | G-4 speech gate end-to-end: dead (404/DNS) cited link→`decision:block`, alive→silent, ambiguous(403)→`additionalContext` warn (once per session), code-fence link not gated, `stop_hook_active`→silent (block once per turn), trailing tool-use event ignored, no-transcript/no-URL/disabled(env+file)/corrupt-ledger→fail open. |

---

## Known limitations

Stated honestly, because the keyed-state design has real edges:

- **No history / no ordering.** The ledger is current-state-per-key. It can
  tell you a file was read and roughly when, but not the sequence of actions or
  how many times. If you wanted an audit trail, this is the wrong shape.
- **Last write wins per key.** Re-recording a key overwrites the prior value.
  That's intentional (newer evidence supersedes), but it means you cannot
  reconstruct what an earlier value was.
- **Caches and evidence share one file.** `verified_urls`/`known_pkgs` are
  really lookup caches living alongside genuine evidence (`read_files`). It
  keeps everything in one place, but it does mean the gate writes to the
  "evidence" file even when it's only caching a network result — hence the
  scoped-merge care around concurrency.
- **Statically-unresolvable targets are invisible.** Anything `shell_scan`
  can't resolve without executing — variables (`> $OUT`), command substitution,
  `xargs`/`find -exec` dynamic target lists — is skipped (warn at most, never
  block). A miss, by deliberate choice over a false block.
- **Relative-path corner cases.** `cd /elsewhere && cat foo.py` and `git diff`
  headers when cwd ≠ repo root can fail to accrue. We confirm `isfile()` before
  recording, so this causes *misses*, never false *records*.
- **The whole text blind spot.** None of this touches plain-text output. If the
  model asserts a URL or package in chat without a tool call, no hook sees it.
  That's a structural limit of hook-based enforcement, only partially mitigated
  by a session-start prompt rule — not by the ledger.
