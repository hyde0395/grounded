# G-2 Manifest Dependency Check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend G-2 so a bare install command that resolves a manifest (e.g. `npm install`, `pip install -r req.txt`) has its declared dependencies verified for existence, closing the manifest-then-install blind spot.

**Architecture:** Two new pure modules feed the existing G-2 check. `shell_scan.manifest_installs(command)` detects bare manifest-resolving installs → `(ecosystem, manifest_path)`. `manifest_scan.deps(ecosystem, content)` parses a manifest's declared dependency names. `pre_gate.gate_bash` reads the manifest, drops deps already locked/installed (positive evidence), and feeds the rest into the unchanged G-2 registry/cache/verdict loop.

**Tech Stack:** Python 3 stdlib only (json, re; `tomllib` when available ≥3.11 with a conservative regex fallback). Tests via `unittest`, offline (network faked by seeding `known_pkgs`).

## Global Constraints

- No external dependencies — stdlib only. `tomllib` is optional (3.11+); a regex fallback covers older Pythons.
- No new ledger keys — reuse `known_pkgs` (positive plain / negative `[bool, ts]` TTL).
- No new rule number/toggle — this is G-2; respects the existing `g-2` toggle.
- Fail open everywhere: unreadable/corrupt/ambiguous → PASS, never a false STOP.
- Code, comments, and messages in English.
- Commits: no `Co-Authored-By`/Claude trailers.
- Manifest-derived specs are mutually exclusive with command-line specs: a command carrying positional package names goes through the existing `package_specs`, never `manifest_installs`.

---

### Task 1: `manifest_scan` module + npm (package.json) parser

**Files:**
- Create: `hooks/manifest_scan.py`
- Test: `tests/test_manifest_scan.py`

**Interfaces:**
- Produces: `deps(ecosystem, content) -> list[str]` (order-preserving dedup; `[]` on any trouble). Ecosystem ids: `npm`/`pypi`/`crates`/`rubygems`/`packagist`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manifest_scan.py
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import manifest_scan  # noqa: E402


class NpmTest(unittest.TestCase):
    def deps(self, content):
        return manifest_scan.deps("npm", content)

    def test_dependencies_and_dev(self):
        c = '{"dependencies":{"react":"^18"},"devDependencies":{"jest":"^29"}}'
        self.assertEqual(self.deps(c), ["react", "jest"])

    def test_scoped_name_kept(self):
        self.assertEqual(self.deps('{"dependencies":{"@types/node":"^20"}}'),
                         ["@types/node"])

    def test_non_registry_specs_skipped(self):
        c = ('{"dependencies":{"a":"file:../a","b":"git+https://x/b.git",'
             '"c":"workspace:*","d":"github:o/d","ok":"^1"}}')
        self.assertEqual(self.deps(c), ["ok"])

    def test_corrupt_json_returns_empty(self):
        self.assertEqual(self.deps("{not json"), [])

    def test_unknown_ecosystem_returns_empty(self):
        self.assertEqual(manifest_scan.deps("maven", '{"x":1}'), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manifest_scan'`

- [ ] **Step 3: Write minimal implementation**

```python
# hooks/manifest_scan.py
"""Pure parsing of dependency manifests — no I/O, no LLM (G-2 input source).

Given a manifest's text content, return the registry-package names it declares,
so pre_gate can run them through the same existence check G-2 already applies to
install-command package names. Conservative (spec §05): anything non-registry
(git/path/url/workspace/private-source) or unparseable is skipped — a missed dep
is cheaper than a false STOP.
"""
import json
import re

# Version specifiers that point somewhere other than the public registry.
_NPM_NON_REGISTRY = ("file:", "link:", "git+", "git:", "http:", "https:",
                     "github:", "workspace:", "npm:", ".", "/", "~")
_NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies",
                 "optionalDependencies")


def _dedup(names):
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _npm(content):
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    names = []
    for section in _NPM_SECTIONS:
        deps = data.get(section)
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            if isinstance(spec, str) and spec.startswith(_NPM_NON_REGISTRY):
                continue
            names.append(name)
    return _dedup(names)


_PARSERS = {"npm": _npm}


def deps(ecosystem, content):
    """[name] declared in a manifest of `ecosystem`; [] on any trouble."""
    parser = _PARSERS.get(ecosystem)
    return parser(content) if parser else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add hooks/manifest_scan.py tests/test_manifest_scan.py
git commit -m "feat: manifest_scan with npm (package.json) parser (G-2 manifest)"
```

---

### Task 2: pypi parsers — requirements.txt + pyproject.toml (+ TOML helper)

**Files:**
- Modify: `hooks/manifest_scan.py`
- Test: `tests/test_manifest_scan.py`

**Interfaces:**
- Consumes: `_dedup`, `_PARSERS` from Task 1.
- Produces: `deps("pypi", content)` handles both requirements.txt-style and pyproject.toml-style content. Internal `_toml_loads(content) -> dict | None` (None ⇒ no `tomllib`, use regex).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_manifest_scan.py
class PypiRequirementsTest(unittest.TestCase):
    def deps(self, content):
        return manifest_scan.deps("pypi", content)

    def test_basic_names_and_versions(self):
        self.assertEqual(self.deps("requests==2.31\nflask>=3\n"),
                         ["requests", "flask"])

    def test_extras_and_markers_stripped(self):
        self.assertEqual(self.deps("uvicorn[standard]>=0.2 ; python_version>'3'"),
                         ["uvicorn"])

    def test_comments_and_blank_lines(self):
        self.assertEqual(self.deps("# a comment\n\nrequests\n"), ["requests"])

    def test_options_and_vcs_skipped(self):
        c = "-r base.txt\n-e .\ngit+https://x/y.git\n./local\nrequests\n"
        self.assertEqual(self.deps(c), ["requests"])

    def test_custom_index_skips_whole_file(self):
        self.assertEqual(self.deps("--index-url https://pri/\nsecretpkg\n"), [])


class PyprojectTest(unittest.TestCase):
    def deps(self, content):
        return manifest_scan.deps("pypi", content)

    def test_pep621_dependencies(self):
        c = '[project]\ndependencies = ["requests>=2", "flask"]\n'
        self.assertEqual(set(self.deps(c)), {"requests", "flask"})

    def test_poetry_table_skips_python(self):
        c = ('[tool.poetry.dependencies]\npython = "^3.11"\n'
             'requests = "^2.31"\n')
        self.assertEqual(self.deps(c), ["requests"])

    def test_poetry_custom_source_skips_file(self):
        c = ('[[tool.poetry.source]]\nname = "pri"\nurl = "https://pri/"\n'
             '[tool.poetry.dependencies]\nsecret = "^1"\n')
        self.assertEqual(self.deps(c), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: FAIL — pypi returns `[]` (no parser yet)

- [ ] **Step 3: Write minimal implementation**

```python
# add to hooks/manifest_scan.py

# requirements.txt -------------------------------------------------------
_REQ_INDEX = ("--index-url", "-i", "--extra-index-url")
_REQ_SKIP_PREFIX = ("-", "git+", "http:", "https:", "file:", ".", "/", "~")
_PEP508_SPLIT = re.compile(r"[@\[<>=!~;,\s]")


def _req_name(line):
    return _PEP508_SPLIT.split(line, 1)[0].strip()


def _requirements(content):
    lines = [ln.split("#", 1)[0].strip() for ln in content.splitlines()]
    if any(any(ln.startswith(opt) for opt in _REQ_INDEX) for ln in lines):
        return []  # custom index → can't tell which deps are private
    names = []
    for ln in lines:
        if not ln or ln.startswith(_REQ_SKIP_PREFIX):
            continue
        n = _req_name(ln)
        if n:
            names.append(n)
    return _dedup(names)


# pyproject.toml ---------------------------------------------------------
def _toml_loads(content):
    """Parsed dict, {} if corrupt, or None when tomllib is unavailable
    (caller falls back to a conservative regex)."""
    try:
        import tomllib
    except ModuleNotFoundError:
        return None
    try:
        return tomllib.loads(content)
    except Exception:
        return {}


def _pep508_name(spec):
    return _req_name(spec) if isinstance(spec, str) else ""


_TOML_HEADER = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")
_TOML_KEY = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=")


def _toml_table_keys(content, table, skip_keys):
    """Keys directly under [table] (conservative regex fallback)."""
    names, in_table = [], False
    for line in content.splitlines():
        h = _TOML_HEADER.match(line)
        if h:
            in_table = h.group(1).strip() == table
            continue
        if in_table:
            k = _TOML_KEY.match(line)
            if k and k.group(1) not in skip_keys:
                names.append(k.group(1))
    return names


def _toml_array_strings(content, array_key):
    """Quoted strings inside `array_key = [ ... ]` (possibly multi-line)."""
    m = re.search(re.escape(array_key) + r"\s*=\s*\[(.*?)\]", content, re.DOTALL)
    if not m:
        return []
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))


def _pyproject(content):
    data = _toml_loads(content)
    if data is not None:
        poetry = (data.get("tool") or {}).get("poetry") or {}
        if poetry.get("source"):
            return []
        names = [_pep508_name(s) for s in (data.get("project") or {}).get("dependencies", []) or []]
        names += [k for k in (poetry.get("dependencies") or {}) if k != "python"]
        return _dedup(n for n in names if n)
    # regex fallback (no tomllib)
    if "[[tool.poetry.source]]" in content:
        return []
    names = [_pep508_name(s) for s in _toml_array_strings(content, "dependencies")]
    names += _toml_table_keys(content, "tool.poetry.dependencies", {"python"})
    return _dedup(n for n in names if n)


def _pypi(content):
    # pyproject is TOML (has table headers / `=`); requirements is line list.
    if "[tool.poetry" in content or "[project]" in content or "[build-system]" in content:
        return _pyproject(content)
    return _requirements(content)


_PARSERS = {"npm": _npm, "pypi": _pypi}
```

Note: replace the existing `_PARSERS = {"npm": _npm}` line from Task 1 with the new one above.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hooks/manifest_scan.py tests/test_manifest_scan.py
git commit -m "feat: manifest_scan pypi parsers (requirements.txt + pyproject.toml)"
```

---

### Task 3: crates (Cargo.toml) + rubygems (Gemfile) + packagist (composer.json)

**Files:**
- Modify: `hooks/manifest_scan.py`
- Test: `tests/test_manifest_scan.py`

**Interfaces:**
- Consumes: `_dedup`, `_toml_loads`, `_toml_table_keys`, `_PARSERS`.
- Produces: `deps` handles `crates`, `rubygems`, `packagist`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_manifest_scan.py
class CargoTest(unittest.TestCase):
    def deps(self, c):
        return manifest_scan.deps("crates", c)

    def test_dependencies_table(self):
        c = '[dependencies]\nserde = "1.0"\ntokio = { version = "1" }\n'
        self.assertEqual(set(self.deps(c)), {"serde", "tokio"})

    def test_path_and_git_deps_skipped(self):
        c = '[dependencies]\nlocal = { path = "../local" }\nserde = "1"\n'
        self.assertEqual(self.deps(c), ["serde"])


class GemfileTest(unittest.TestCase):
    def deps(self, c):
        return manifest_scan.deps("rubygems", c)

    def test_gem_lines(self):
        c = "source 'https://rubygems.org'\ngem 'rails', '~> 7'\ngem \"pg\"\n"
        self.assertEqual(self.deps(c), ["rails", "pg"])

    def test_local_and_git_gems_skipped(self):
        c = "gem 'a', path: '../a'\ngem 'b', git: 'https://x'\ngem 'pg'\n"
        self.assertEqual(self.deps(c), ["pg"])

    def test_custom_source_skips_file(self):
        c = "source 'https://gems.corp.internal'\ngem 'secret'\n"
        self.assertEqual(self.deps(c), [])


class ComposerTest(unittest.TestCase):
    def deps(self, c):
        return manifest_scan.deps("packagist", c)

    def test_require_vendor_names(self):
        c = '{"require":{"monolog/monolog":"^3","php":">=8"}}'
        self.assertEqual(self.deps(c), ["monolog/monolog"])

    def test_platform_and_ext_skipped(self):
        c = '{"require":{"ext-gd":"*","lib-curl":"*","a/b":"^1"}}'
        self.assertEqual(self.deps(c), ["a/b"])

    def test_repositories_skips_file(self):
        c = '{"repositories":[{"type":"vcs"}],"require":{"a/b":"^1"}}'
        self.assertEqual(self.deps(c), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: FAIL — crates/rubygems/packagist return `[]`

- [ ] **Step 3: Write minimal implementation**

```python
# add to hooks/manifest_scan.py
_CARGO_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


def _cargo(content):
    data = _toml_loads(content)
    if data is not None:
        if data.get("registries"):
            return []
        names = []
        for table in _CARGO_TABLES:
            deps = data.get(table)
            if not isinstance(deps, dict):
                continue
            for name, spec in deps.items():
                if isinstance(spec, dict) and (spec.get("path") or spec.get("git")
                                               or spec.get("registry")):
                    continue
                names.append(name)
        return _dedup(names)
    # regex fallback: emit table keys whose line has no path/git/registry
    names, in_table = [], False
    for line in content.splitlines():
        h = _TOML_HEADER.match(line)
        if h:
            in_table = h.group(1).strip() in _CARGO_TABLES
            continue
        if in_table:
            k = _TOML_KEY.match(line)
            if k and not re.search(r"\b(path|git|registry)\s*=", line):
                names.append(k.group(1))
    return _dedup(names)


_GEM = re.compile(r"^\s*gem\s+['\"]([^'\"]+)['\"]")
_GEM_SOURCE = re.compile(r"^\s*source\s+['\"]([^'\"]+)['\"]")
_GEM_LOCAL = ("path:", "git:", "github:")


def _gemfile(content):
    names = []
    for line in content.splitlines():
        s = _GEM_SOURCE.match(line)
        if s and "rubygems.org" not in s.group(1):
            return []  # custom gem source → can't tell which gems are private
        m = _GEM.match(line)
        if m and not any(opt in line for opt in _GEM_LOCAL):
            names.append(m.group(1))
    return _dedup(names)


def _composer(content):
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict) or data.get("repositories"):
        return []
    names = []
    for section in ("require", "require-dev"):
        req = data.get(section)
        if not isinstance(req, dict):
            continue
        for name in req:
            if "/" not in name or name.startswith(("ext-", "lib-")):
                continue
            names.append(name)
    return _dedup(names)


_PARSERS = {"npm": _npm, "pypi": _pypi, "crates": _cargo,
            "rubygems": _gemfile, "packagist": _composer}
```

Note: replace the Task 2 `_PARSERS` line with the five-entry one above.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hooks/manifest_scan.py tests/test_manifest_scan.py
git commit -m "feat: manifest_scan crates/rubygems/packagist parsers"
```

---

### Task 4: `shell_scan.manifest_installs` — detect bare manifest-resolving installs

**Files:**
- Modify: `hooks/shell_scan.py`
- Test: `tests/test_shell_scan.py`

**Interfaces:**
- Consumes: existing `_mask_heredocs`, `_split_segments`, `_tokens`, `_strip_prefixes`.
- Produces: `manifest_installs(command) -> list[(ecosystem, manifest_path)]`, order-preserving dedup.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_shell_scan.py
class ManifestInstallsTest(unittest.TestCase):
    def mi(self, command):
        return shell_scan.manifest_installs(command)

    def test_npm_install_bare(self):
        self.assertEqual(self.mi("npm install"), [("npm", "package.json")])
        self.assertEqual(self.mi("npm ci"), [("npm", "package.json")])

    def test_npm_install_with_name_is_not_manifest(self):
        self.assertEqual(self.mi("npm install lodash"), [])

    def test_pip_dash_r(self):
        self.assertEqual(self.mi("pip install -r requirements.txt"),
                         [("pypi", "requirements.txt")])

    def test_poetry_and_bundle_and_composer(self):
        self.assertEqual(self.mi("poetry install"), [("pypi", "pyproject.toml")])
        self.assertEqual(self.mi("bundle install"), [("rubygems", "Gemfile")])
        self.assertEqual(self.mi("composer install"),
                         [("packagist", "composer.json")])

    def test_cargo_build(self):
        self.assertEqual(self.mi("cargo build"), [("crates", "Cargo.toml")])

    def test_non_install_command(self):
        self.assertEqual(self.mi("npm run build"), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -p test_shell_scan.py -v`
Expected: FAIL — `AttributeError: module 'shell_scan' has no attribute 'manifest_installs'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to hooks/shell_scan.py (near package_specs)
def _positional_after(rest, verb):
    """True if there is a non-flag token after `verb` (i.e. a package name)."""
    seen_verb = False
    for t in rest:
        if not seen_verb:
            seen_verb = t == verb
            continue
        if t and not t.startswith("-"):
            return True
    return False


def _dash_r_file(rest):
    for i, t in enumerate(rest):
        if t in ("-r", "--requirement") and i + 1 < len(rest):
            return rest[i + 1]
        if t.startswith("--requirement="):
            return t.split("=", 1)[1]
    return None


def _segment_manifest_install(tokens):
    tokens = _strip_prefixes(tokens)
    if not tokens:
        return None
    cmd, rest = os.path.basename(tokens[0]), tokens[1:]
    if cmd in ("npm", "pnpm", "bun"):
        if rest and rest[0] in ("install", "i", "ci") and not _positional_after(rest, rest[0]):
            return ("npm", "package.json")
    if cmd == "yarn":
        if (not rest or rest[0] == "install") and not _positional_after(rest, "install"):
            return ("npm", "package.json")
    if cmd in ("pip", "pip2", "pip3") and rest and rest[0] == "install":
        f = _dash_r_file(rest)
        return ("pypi", f) if f else None
    if cmd in ("python", "python2", "python3") and rest[:3] == ["-m", "pip", "install"]:
        f = _dash_r_file(rest[3:])
        return ("pypi", f) if f else None
    if cmd == "uv":
        if rest[:1] == ["sync"]:
            return ("pypi", "pyproject.toml")
        if rest[:2] == ["pip", "install"]:
            f = _dash_r_file(rest[2:])
            return ("pypi", f) if f else None
    if cmd == "poetry" and rest[:1] == ["install"]:
        return ("pypi", "pyproject.toml")
    if cmd == "bundle" and (not rest or rest[0] == "install"):
        return ("rubygems", "Gemfile")
    if cmd == "composer" and rest and rest[0] in ("install", "update") \
            and not _positional_after(rest, rest[0]):
        return ("packagist", "composer.json")
    if cmd == "cargo" and rest and rest[0] in ("build", "fetch"):
        return ("crates", "Cargo.toml")
    return None


def manifest_installs(command):
    """[(ecosystem, manifest_path)] for bare, manifest-resolving install
    commands (no positional package names). Statically conservative."""
    command = _mask_heredocs(command)
    found = []
    for segment in _split_segments(command):
        hit = _segment_manifest_install(_tokens(segment))
        if hit and hit not in found:
            found.append(hit)
    return found
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -p test_shell_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hooks/shell_scan.py tests/test_shell_scan.py
git commit -m "feat: shell_scan.manifest_installs detects bare manifest installs"
```

---

### Task 5: lockfile / installed positive-evidence helper

**Files:**
- Modify: `hooks/manifest_scan.py`
- Test: `tests/test_manifest_scan.py`

**Interfaces:**
- Produces: `grounded_names(project_dir, ecosystem) -> set[str]` — names known-real because they are locked or installed. Best-effort; `set()` when nothing readable. JSON lockfiles (`package-lock.json` v2/v3, `composer.lock`) and the npm `node_modules/` tree are honored; other lockfile formats fall through (registry still checks them — safe).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_manifest_scan.py
import json as _json
import tempfile


class GroundedNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        with open(os.path.join(self.dir, name), "w") as f:
            f.write(content)

    def test_package_lock_v3_names(self):
        self.write("package-lock.json", _json.dumps({"packages": {
            "": {}, "node_modules/react": {}, "node_modules/@types/node": {}}}))
        self.assertEqual(manifest_scan.grounded_names(self.dir, "npm"),
                         {"react", "@types/node"})

    def test_node_modules_dir(self):
        os.makedirs(os.path.join(self.dir, "node_modules", "lodash"))
        self.assertIn("lodash", manifest_scan.grounded_names(self.dir, "npm"))

    def test_composer_lock_names(self):
        self.write("composer.lock", _json.dumps(
            {"packages": [{"name": "monolog/monolog"}]}))
        self.assertEqual(manifest_scan.grounded_names(self.dir, "packagist"),
                         {"monolog/monolog"})

    def test_missing_lockfile_empty_set(self):
        self.assertEqual(manifest_scan.grounded_names(self.dir, "crates"), set())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: FAIL — `AttributeError: module 'manifest_scan' has no attribute 'grounded_names'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to hooks/manifest_scan.py
import os as _os


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _npm_locked(project_dir):
    names = set()
    data = _read_json(_os.path.join(project_dir, "package-lock.json"))
    if isinstance(data, dict):
        for key in (data.get("packages") or {}):
            if key.startswith("node_modules/"):
                names.add(key.split("node_modules/", 1)[1])
        names.update(data.get("dependencies") or {})  # lockfile v1
    nm = _os.path.join(project_dir, "node_modules")
    try:
        for entry in _os.listdir(nm):
            if entry.startswith("@"):
                for sub in _os.listdir(_os.path.join(nm, entry)):
                    names.add(entry + "/" + sub)
            elif not entry.startswith("."):
                names.add(entry)
    except OSError:
        pass
    return names


def _composer_locked(project_dir):
    data = _read_json(_os.path.join(project_dir, "composer.lock"))
    names = set()
    if isinstance(data, dict):
        for section in ("packages", "packages-dev"):
            for pkg in data.get(section) or []:
                if isinstance(pkg, dict) and pkg.get("name"):
                    names.add(pkg["name"])
    return names


def grounded_names(project_dir, ecosystem):
    """Names already locked/installed (real regardless of registry name).
    Best-effort; empty set when nothing is readable."""
    if ecosystem == "npm":
        return _npm_locked(project_dir)
    if ecosystem == "packagist":
        return _composer_locked(project_dir)
    return set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -p test_manifest_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hooks/manifest_scan.py tests/test_manifest_scan.py
git commit -m "feat: manifest_scan.grounded_names (lockfile/installed positive evidence)"
```

---

### Task 6: `pre_gate` orchestration + end-to-end

**Files:**
- Modify: `hooks/pre_gate.py` (the `gate_bash` G-2 block)
- Test: `tests/test_pre_gate.py`

**Interfaces:**
- Consumes: `shell_scan.manifest_installs`, `manifest_scan.deps`, `manifest_scan.grounded_names`, existing `registry.check_package`/`verdict.gate_package`/`known_pkgs` loop.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pre_gate.py (uses existing bash_payload + UrlGateTest-style ledger writer)
class ManifestGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, known_pkgs=None):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            json.dump({"read_files": {}, "verified_urls": {},
                       "known_pkgs": known_pkgs or {}}, f)

    def write(self, name, content):
        with open(os.path.join(self.cwd, name), "w") as f:
            f.write(content)

    def bash(self, command):
        return run_hook("pre_gate.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "cwd": self.cwd})

    def test_npm_install_hallucinated_manifest_dep_blocked(self):
        self.ledger(known_pkgs={"npm:lodashh": False})
        self.write("package.json", '{"dependencies":{"lodashh":"^1"}}')
        r = self.bash("npm install")
        self.assertEqual(r.returncode, 2)
        self.assertIn("lodashh", r.stderr)

    def test_npm_install_real_dep_passes(self):
        self.ledger(known_pkgs={"npm:react": True})
        self.write("package.json", '{"dependencies":{"react":"^18"}}')
        r = self.bash("npm install")
        self.assertEqual(r.returncode, 0)

    def test_missing_manifest_passes(self):
        self.ledger()
        r = self.bash("npm install")
        self.assertEqual(r.returncode, 0)

    def test_locked_dep_not_checked(self):
        # absent on registry but present in lockfile → positive evidence → pass
        self.ledger(known_pkgs={"npm:internalpkg": False})
        self.write("package.json", '{"dependencies":{"internalpkg":"^1"}}')
        self.write("package-lock.json",
                   '{"packages":{"node_modules/internalpkg":{}}}')
        r = self.bash("npm install")
        self.assertEqual(r.returncode, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -p test_pre_gate.py -v`
Expected: FAIL — `npm install` not gated against the manifest yet (returncode 0 where 2 expected)

- [ ] **Step 3: Write minimal implementation**

In `hooks/pre_gate.py`, add `import manifest_scan` at the top, and in `gate_bash`, just before the existing `package_specs` loop, extend the spec list:

```python
    package_specs = shell_scan.package_specs(command) if cfg["g-2"] else []
    if cfg["g-2"]:
        package_specs = list(package_specs)
        for eco, rel in shell_scan.manifest_installs(command):
            mpath = ledger_io.normalize(rel, cwd)
            try:
                with open(mpath, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue  # no manifest → nothing to verify (fail open)
            project_dir = os.path.dirname(mpath)
            grounded = manifest_scan.grounded_names(project_dir, eco)
            for name in manifest_scan.deps(eco, content):
                if name not in grounded and (eco, name) not in package_specs:
                    package_specs.append((eco, name))
```

The existing `for ecosystem, name in package_specs[:MAX_REGISTRY_LOOKUPS]:` loop below then checks them unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -p test_pre_gate.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: `OK` (all prior tests still pass)

- [ ] **Step 6: Commit**

```bash
git add hooks/pre_gate.py tests/test_pre_gate.py
git commit -m "feat: gate manifest-declared deps on bare install (G-2 manifest)"
```

---

## Self-Review

**Spec coverage:** install→manifest mapping (Task 4) ✓; 5 parsers (Tasks 1-3) ✓; lockfile positive-evidence (Task 5) ✓; orchestration + fail-open (Task 6) ✓; custom-source skip (Tasks 2-3) ✓; TOML fallback (Task 2) ✓; verdict reuse/no-new-keys (Task 6, reuses existing loop) ✓; existence≠safety / non-goals are documented in the spec, no code needed.

**Placeholder scan:** none — every step has runnable code and exact commands.

**Type consistency:** `deps(ecosystem, content)->list[str]`, `grounded_names(project_dir, ecosystem)->set[str]`, `manifest_installs(command)->list[tuple]` are used consistently across Tasks 1-6. `_PARSERS` is replaced (not duplicated) at each parser-adding task.

**Deferred (documented in spec, not in this plan):** edit-time gating, registry creation-date/timing signal, API/member checking, non-JSON lockfile formats (yarn.lock/pnpm/Cargo.lock/poetry.lock/Gemfile.lock) — these fall through to the registry check, which is safe.
