# G-2 manifest dependency check — design

**Status:** design (approved in brainstorming 2026-06-26), not yet implemented.
**Scope:** one implementation plan. Extends the existing G-2 rule; adds no new
rule number and no new ledger keys.

## Problem

G-2 today verifies package existence only when a package **name appears on an
install command line** (`pip install reqests`, `npm install lodashh`). It never
sees dependencies that are declared in a **manifest file** and then installed by
a bare, name-less command:

```
# edit requirements.txt to add a hallucinated "reqests"
pip install -r requirements.txt        # G-2 sees no name → no check → installs it
```

This is a real blind spot. Churilov 2026 (arXiv 2605.17062) re-measured frontier
models at 4.62–6.10% package-hallucination rates and found **127 names
hallucinated identically across five models, 53 still registrable by an
attacker** — a model-agnostic surface. The manifest-then-install path is exactly
how those land in a project without ever passing through G-2's command-line gate.

## Goal / non-goals

**Goal:** when a bare install command resolves a manifest, read that manifest,
extract its declared dependency names, and run them through the *existing* G-2
existence check (registry lookup → tri-state → cache → STOP/PASS).

**Non-goals (explicit, with rationale):**

- **Not API/member hallucination.** `from requests import sessionz` (a real
  package, a fabricated symbol) is out of scope: verifying it deterministically
  needs a symbol index or code execution, neither of which fits grounded's
  no-LLM / no-execution constraints. The name-vs-member split is exactly the
  one drawn by Twist, Zhang, Harman & Yannakoudakis (arXiv 2509.22202); we cover
  names, not members. MARIN (FSE 2025, arXiv 2505.05057) is the related work for
  the member case.
- **Not a security/malware scanner.** Existence ≠ safety. We catch hallucinated
  names that **don't exist yet** (fabrications, conflations — ~89% of the
  Spracklen taxonomy). Once an attacker squats a hallucinated name it *exists*,
  so the existence check passes it. Catching the squatted case needs reputation
  / timing signals (snync, Socket) — grounded complements those, it doesn't
  replace them. This is the "근거 강제, 보안 아님" category line.
- **Not transitive dependencies.** Only directly declared deps; the model's
  hallucination lives in direct deps.
- **Not the edit-time surface (for now).** We gate at the install command, not
  at the manifest edit. Edit-time would catch a few seconds earlier but requires
  grounded's first content-based gate and parses Edit's partial `new_string`
  fragment (false-positive prone). The install is where the hallucinated package
  is actually fetched, so install-time fully covers the harm. Edit-time stays a
  future increment.

## Architecture

G-2's pipeline is unchanged downstream; only its **input source** widens.

```
Bash command
  ├─ shell_scan.package_specs(cmd)      → [(eco, name)]   (names ON the command — existing)
  └─ shell_scan.manifest_installs(cmd)  → [(eco, path)]   (bare manifest-resolving install — NEW)
        └─ pre_gate: resolve path (cwd / leading_cd), read file (I/O)
             └─ manifest_scan.deps(eco, content)         → [name]   (NEW, pure)
   ── both name sources merge into the SAME existing loop ──
   for (eco, name): _cache_get(known_pkgs) → registry.check_package (budget/caps)
                    → verdict.gate_package → STOP/PASS  → _emit / _save_caches
```

Three new pieces; everything else (registry, tri-state, `known_pkgs` cache,
`_Budget`, lookup caps, STOP message, fail-open) is reused verbatim.

- **`shell_scan.manifest_installs(command)`** — pure. Detects bare,
  manifest-resolving install commands and returns `[(ecosystem, manifest_path)]`.
  Mutually exclusive with `package_specs`: if positional package names are
  present, that command goes through the existing path, not this one.
- **`manifest_scan.py`** — new pure module. `deps(ecosystem, content) ->
  [name]`, one parser per ecosystem, applying skip rules. Pure string→names,
  mirroring `text_scan` / `shell_scan` (no I/O).
- **`pre_gate.gate_bash`** — orchestration (the only I/O): resolve manifest
  path, read it, call `manifest_scan`, feed names into the existing G-2 loop.

## Components

### Install-command → manifest mapping (`shell_scan.manifest_installs`)

| Command (no positional package names) | Manifest | eco |
|---|---|---|
| `npm/pnpm install`·`i`·`ci`, `yarn`(/`install`), `bun install` | `package.json` | npm |
| `pip install -r <file>`, `uv pip install -r <file>` | `<file>` | pypi |
| `poetry install`, `uv sync` | `pyproject.toml` | pypi |
| `bundle install`, `bundle` | `Gemfile` | rubygems |
| `composer install`, `composer update` | `composer.json` | packagist |
| `cargo build`, `cargo fetch` | `Cargo.toml` | crates |

Reuses existing helpers: `_strip_prefixes` (env/sudo), `leading_cd` (so
`cd sub && npm install` resolves `sub/package.json`).

### Manifest parsers (`manifest_scan.deps`)

| Format | Dep location | Skip (non-registry) |
|---|---|---|
| `package.json` | `dependencies`/`devDependencies`/`peerDependencies`/`optionalDependencies` keys | values `file:`/`link:`/`git+`/`http(s):`/`workspace:` |
| `requirements.txt` | per-line name (reuse G-2 `_pip_names` extraction) | `-r`/`-c`/`-e`/`git+`/`http`/`file:`/`.`/`/`/`~` lines; **`--index-url`/`-i`/`--extra-index-url` present → skip whole file** |
| `pyproject.toml` | `[project].dependencies` array + `[tool.poetry.dependencies]` table keys | `python` key; path/git/url deps; **`[[tool.poetry.source]]` present → skip whole file** |
| `Cargo.toml` | `[dependencies]`/`[dev-dependencies]`/`[build-dependencies]` keys | deps with `path=`/`git=`; **`[registries]` / dep `registry=` present → skip** |
| `Gemfile` | `gem 'name'` (regex) | `path:`/`git:`/`github:` options; **`source` to a custom host → skip** |
| `composer.json` | `require`/`require-dev` keys | `php`, `ext-*`, `lib-*`, keys without `/`; **`repositories` present → skip whole file** (reuse `_composer_names` slash filter) |

### Lockfile / installed state as positive evidence

Before checking a declared dep against the registry, treat it as **already
grounded (PASS, no lookup)** if it is present in the ecosystem's lockfile or
installed tree:

| eco | positive-evidence source |
|---|---|
| npm | `package-lock.json` / `yarn.lock` / `pnpm-lock.yaml`, or `node_modules/<name>` |
| pypi | `poetry.lock` / `uv.lock`, or an installed dist |
| crates | `Cargo.lock` |
| rubygems | `Gemfile.lock` |
| packagist | `composer.lock` |

Rationale: a dep that is already locked/installed is real regardless of name (it
resolved once). This (a) cuts latency on the hot `npm install` path to only
newly-added deps, and (b) eliminates false STOPs on private/internal packages
that are locked but absent from the public registry — collapsing most of the
custom-registry blind spot. Reading the lockfile is best-effort; if absent or
unparseable, fall through to the registry check (fail toward checking, which is
still safe because unknown→PASS).

## Data flow (worked example)

`npm install` in a project whose `package.json` declares a hallucinated `lodashh`:

1. PreToolUse fires on Bash `npm install` (`cfg["g-2"]` on).
2. `package_specs` → `[]` (no positional names).
3. `manifest_installs` → `[("npm", "package.json")]`.
4. Resolve + read `package.json` (OSError → skip, fail open).
5. Custom-source check (adjacent `.npmrc` registry) → none → proceed.
6. `manifest_scan.deps("npm", content)` → `["react", "lodashh"]` (skips
   `file:`/`workspace:` entries).
7. Drop deps present in `package-lock.json` / `node_modules` (positive evidence).
8. Remaining names enter the **existing** loop: `_cache_get` → `check_package`
   (budget/caps) → `gate_package`. `lodashh` → 404 → STOP naming `lodashh`.
9. `_emit` exit 2 + stderr; `_save_caches` persists the negative.

Merged with any command-line specs and deduped by `(eco, name)` (existing
dedup). The 25-lookup cap and 5s budget apply to the union; a huge manifest's
first run checks until the budget/cap is hit and skips the rest (fail open),
with the `known_pkgs` cache covering more on subsequent runs.

## Error handling / skips

| Situation | Behavior | Why |
|---|---|---|
| Manifest missing / unreadable | skip | bare install, nothing to verify — fail open |
| Corrupt JSON/TOML/Gemfile | parser returns `[]`, never raises | fail open |
| Custom source in manifest (poetry source / composer repositories / Cargo registries / requirements `--index-url`) | skip that file | can't know which deps are private — avoid false STOP |
| Custom registry in adjacent `.npmrc` / `pip.conf` | skip that ecosystem's manifest | same |
| Non-registry dep entry (git/path/url/workspace/link/php-ext) | skip per entry | private/local |
| Dep already in lockfile / installed | PASS without lookup | positive evidence |
| TOML on Python < 3.11 (no `tomllib`) | conservative table-aware regex extractor; skip ambiguous lines | under-read is OK ("오탐 < 누락"); no external dependency |
| Registry unreachable / rate-limited | None → PASS | existing G-2 tri-state |
| Budget / cap exhausted | skip uncached deps | fail open; cache heals over runs |
| Dep resolves to 404/410 (and not skipped/locked) | STOP, naming the dep | declared name = registry name → same confidence as command-line G-2 |

No new ledger keys: reuses `known_pkgs` (positive plain / negative `[val, ts]`
TTL). This path is STOP-only, so `warned` is not involved.

## TOML without a dependency

grounded ships zero dependencies. For `pyproject.toml` / `Cargo.toml`:
prefer `tomllib` when importable (Python ≥ 3.11); otherwise a small
table-aware regex extractor that tracks the current `[table]` header and, inside
a target dependency table, captures `^(\w[\w-]*)\s*=` keys (and quoted strings
inside a PEP-621 `dependencies = [...]` array). It deliberately under-extracts on
anything ambiguous (multi-line inline tables, odd quoting) — missing a dep is
acceptable; a false STOP is not.

## Testing strategy

All offline; network faked by seeding `known_pkgs` (existing G-2 test pattern).

- **`test_shell_scan.py` (extend):** `manifest_installs` detects each
  ecosystem's bare install, excludes commands carrying positional names,
  handles `-r <file>`, `cd x && …`, and env/sudo prefixes.
- **`test_manifest_scan.py` (new):** per format — extract names from valid
  content; skip git/path/url/workspace/platform deps; skip whole file on
  custom-source; corrupt content → `[]`; TOML with and without `tomllib`
  (force the fallback); lockfile/installed deps excluded.
- **`test_pre_gate.py` (extend, E2E subprocess):** `npm install` +
  `package.json` with a cached-absent dep → STOP naming it; cached-present →
  pass; manifest missing → pass; custom-source manifest → skipped; dep in
  lockfile → not checked.
- **Live verification (post-merge, manual):** declare a genuinely absent dep in
  a temp `package.json`, run `npm install` under the hook, confirm a real
  registry 404 → STOP (mirrors the G-4 live check).

## Limitations (stated honestly)

- **Existence ≠ safety.** Catches not-yet-registered hallucinations; a squatted
  name that already exists passes. Complements snync/Socket/Packj, doesn't
  replace them.
- **Members/APIs uncovered.** Only declared package *names*, never symbols.
- **Env-only / global private indexes.** A private registry configured purely in
  environment variables or a home/global config grounded doesn't read can still
  cause a false STOP for a not-yet-locked private dep — the same residual G-2
  already documents for `PIP_INDEX_URL`. The lockfile positive-evidence path
  removes most of this in practice.
- **Static detection is partial by nature.** Miranda-Pena et al. (arXiv
  2604.07755, 2026) measured static methods catching 14–85% of library
  hallucinations and frame them as "a cheap method for addressing some forms" —
  grounded's value and its ceiling, quantified.

## Out of scope / future increments

- **Edit-time arm** — gate manifest edits (Edit/Write content) for the earliest
  catch; deferred (new content surface, fragment-parsing false positives).
- **Timing signal** — flag deps whose registry creation date is suspiciously
  recent (snync-style) to reach some squatted names; heuristic, not
  deterministic — separate increment.
- **API/member checking** — out of grounded's deterministic/no-execution
  envelope; tracked only as related work (MARIN, arXiv 2505.05057).

## References (verified this session)

- Spracklen et al., *We Have a Package for You!*, USENIX Security 2025 — code/data: https://github.com/Spracks/PackageHallucination (MIT; master hallucinated-name list withheld for safety).
- Churilov, *The Range Shrinks, the Threat Remains*, arXiv 2605.17062 (2026).
- Twist, Zhang, Harman, Yannakoudakis, *Library Hallucinations in LLM-Generated Code*, arXiv 2509.22202 (2025).
- Liu et al., *Beyond Functional Correctness*, IEEE TSE (peer-reviewed), arXiv 2404.00971.
- Chen et al., *Towards Mitigating API Hallucination … (MARIN)*, FSE 2025 Industry, arXiv 2505.05057.
- Miranda-Pena et al., *An Empirical Analysis of Static Analysis Methods for Detection and Mitigation of Code Library Hallucinations*, arXiv 2604.07755 (2026).
- Prior-art tools (repos real; behavior from search): snync https://github.com/snyk-labs/snync, Loki https://github.com/Xh4H/Loki, Packj https://github.com/ossillate-inc/packj.
