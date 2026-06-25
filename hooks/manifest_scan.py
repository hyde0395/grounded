"""Pure parsing of dependency manifests — no I/O for parsing, no LLM.

Given a manifest's text content, return the registry-package names it declares,
so pre_gate can run them through the same existence check G-2 already applies to
install-command package names (spec: docs/superpowers/specs/2026-06-26-...).

Conservative (spec §05): anything non-registry (git/path/url/workspace/private
source) or unparseable is skipped — a missed dep is cheaper than a false STOP.
`grounded_names` reads lockfiles/installed trees as positive evidence so a dep
that already resolved once is never re-questioned.
"""
import json
import os
import re

# --- package.json (npm) -------------------------------------------------
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


# --- requirements.txt (pypi) -------------------------------------------
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


# --- TOML (pyproject.toml, Cargo.toml) ---------------------------------
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
        names = [_pep508_name(s)
                 for s in (data.get("project") or {}).get("dependencies", []) or []]
        names += [k for k in (poetry.get("dependencies") or {}) if k != "python"]
        return _dedup(n for n in names if n)
    # regex fallback (no tomllib)
    if "[[tool.poetry.source]]" in content:
        return []
    names = [_pep508_name(s) for s in _toml_array_strings(content, "dependencies")]
    names += _toml_table_keys(content, "tool.poetry.dependencies", {"python"})
    return _dedup(n for n in names if n)


def _pypi(content):
    # pyproject is TOML (table headers); requirements is a flat line list.
    if "[tool.poetry" in content or "[project]" in content \
            or "[build-system]" in content:
        return _pyproject(content)
    return _requirements(content)


# --- Cargo.toml (crates) -----------------------------------------------
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
    # regex fallback: table keys whose line names no path/git/registry
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


# --- Gemfile (rubygems) ------------------------------------------------
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


# --- composer.json (packagist) -----------------------------------------
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


def deps(ecosystem, content):
    """[name] declared in a manifest of `ecosystem`; [] on any trouble."""
    parser = _PARSERS.get(ecosystem)
    return parser(content) if parser else []


# --- lockfile / installed positive evidence ----------------------------
def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _npm_locked(project_dir):
    names = set()
    data = _read_json(os.path.join(project_dir, "package-lock.json"))
    if isinstance(data, dict):
        for key in (data.get("packages") or {}):
            if key.startswith("node_modules/"):
                names.add(key.split("node_modules/", 1)[1])
        names.update(data.get("dependencies") or {})  # lockfile v1
    nm = os.path.join(project_dir, "node_modules")
    try:
        for entry in os.listdir(nm):
            if entry.startswith("@"):
                for sub in os.listdir(os.path.join(nm, entry)):
                    names.add(entry + "/" + sub)
            elif not entry.startswith("."):
                names.add(entry)
    except OSError:
        pass
    return names


def _composer_locked(project_dir):
    data = _read_json(os.path.join(project_dir, "composer.lock"))
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
