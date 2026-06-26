"""Pure parsing of package-install commands — no I/O, no LLM (G-2 input).

Split out of shell_scan: this module owns *what gets installed* (registry
package specs from install commands, and the manifests bare installs resolve),
while shell_scan owns the shell lexing and write/fetch extraction. The shared
lexing primitives (`_mask_heredocs`, `_split_segments`, `_tokens`,
`_strip_prefixes`) live in shell_scan and are imported here.

Conservative (spec §05): a custom index/registry/source flag suppresses G-2 for
that segment (the public registry can't answer for a private install), and
anything statically unresolvable is skipped — a false block costs more.
"""
import os
import re

from shell_scan import (_mask_heredocs, _split_segments, _strip_prefixes,
                        _tokens)

_PIP_VALUE_FLAGS = {
    "-r", "-c", "-t", "-i", "-f", "--requirement", "--constraint", "--target",
    "--index-url", "--extra-index-url", "--find-links", "--platform",
    "--python-version", "--abi", "--implementation", "-e", "--editable",
}
_NON_REGISTRY_PREFIXES = ("file:", "git+", "http:", "https:", "github:", ".", "/", "~")

# A custom index/registry/source aims an install at a registry grounded cannot
# query, so the public-registry lookup would falsely STOP a legitimate private
# install. Its presence suppresses G-2 for that segment (fail open).
_PIP_INDEX_FLAGS = {"-i", "--index-url", "--extra-index-url"}
_UV_INDEX_FLAGS = _PIP_INDEX_FLAGS | {"--index", "--default-index"}
_NPM_INDEX_FLAGS = {"--registry"}
_GEM_INDEX_FLAGS = {"-s", "--source"}
_CARGO_INDEX_FLAGS = {"--registry", "--index"}
_POETRY_INDEX_FLAGS = {"--source"}


def _has_index_flag(args, flags):
    """True if any token is one of `flags`, including the `--flag=value` form."""
    return any(a in flags or a.split("=", 1)[0] in flags for a in args)


def _pip_names(args):
    names, skip_next = [], False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in _PIP_VALUE_FLAGS:
            skip_next = True
            continue
        if not a or a.startswith("-"):
            continue
        if a.startswith(_NON_REGISTRY_PREFIXES):
            continue
        # `@` covers poetry's name@constraint; pip itself uses ==/extras
        base = re.split(r"[@\[<>=!~;,]", a)[0].strip()
        if base:
            names.append(base)
    return names


def _npm_names(args):
    names = []
    for a in args:
        if not a or a.startswith("-"):
            continue
        if a.startswith(_NON_REGISTRY_PREFIXES):
            continue
        if a.startswith("@"):
            if "/" not in a:
                continue
            cut = a.find("@", 1)  # version suffix on a scoped name
            names.append(a if cut == -1 else a[:cut])
        elif "/" not in a:
            names.append(a.split("@", 1)[0])
    return [n for n in names if n]


def _cargo_names(args):
    names = []
    for a in args:
        if not a or a.startswith("-") or "/" in a or a.startswith("."):
            continue
        names.append(a.split("@", 1)[0])
    return [n for n in names if n]


# gem/bundle flags that consume the following token as their value.
_GEM_VALUE_FLAGS = {"-v", "--version", "-s", "--source", "-g", "--gemfile",
                    "--group", "-i", "--install-dir"}


def _gem_names(args):
    names, skip_next = [], False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in _GEM_VALUE_FLAGS:
            skip_next = True
            continue
        if not a or a.startswith("-") or "/" in a or a.startswith("."):
            continue
        if a.endswith(".gem"):  # a local gem file, not a registry name
            continue
        names.append(a.split(":", 1)[0].split("@", 1)[0])
    return [n for n in names if n]


def _composer_names(args):
    # Packagist packages are always vendor/name; bare tokens (php extensions
    # like ext-gd, the version in `name ^2.0`) are not registry packages.
    names = []
    for a in args:
        if not a or a.startswith("-") or "/" not in a:
            continue
        names.append(a.split(":", 1)[0])
    return [n for n in names if n]


def _segment_package_specs(tokens):
    tokens = _strip_prefixes(tokens)
    if not tokens:
        return []
    cmd, rest = os.path.basename(tokens[0]), tokens[1:]
    if cmd in ("npm", "pnpm") and rest and rest[0] in ("install", "i", "add"):
        if _has_index_flag(rest[1:], _NPM_INDEX_FLAGS):
            return []
        return [("npm", n) for n in _npm_names(rest[1:])]
    if cmd == "yarn" and rest and rest[0] == "add":
        if _has_index_flag(rest[1:], _NPM_INDEX_FLAGS):
            return []
        return [("npm", n) for n in _npm_names(rest[1:])]
    if cmd in ("pip", "pip2", "pip3") and rest and rest[0] == "install":
        if _has_index_flag(rest[1:], _PIP_INDEX_FLAGS):
            return []
        return [("pypi", n) for n in _pip_names(rest[1:])]
    if cmd in ("python", "python2", "python3") and len(rest) >= 3 \
            and rest[0] == "-m" and rest[1] == "pip" and rest[2] == "install":
        if _has_index_flag(rest[3:], _PIP_INDEX_FLAGS):
            return []
        return [("pypi", n) for n in _pip_names(rest[3:])]
    if cmd == "uv" and rest:
        if rest[0] == "add":
            if _has_index_flag(rest[1:], _UV_INDEX_FLAGS):
                return []
            return [("pypi", n) for n in _pip_names(rest[1:])]
        if len(rest) >= 2 and rest[0] == "pip" and rest[1] == "install":
            if _has_index_flag(rest[2:], _UV_INDEX_FLAGS):
                return []
            return [("pypi", n) for n in _pip_names(rest[2:])]
    if cmd == "cargo" and rest and rest[0] in ("add", "install"):
        if _has_index_flag(rest[1:], _CARGO_INDEX_FLAGS):
            return []
        return [("crates", n) for n in _cargo_names(rest[1:])]
    if cmd == "poetry" and rest and rest[0] == "add":
        if _has_index_flag(rest[1:], _POETRY_INDEX_FLAGS):
            return []
        return [("pypi", n) for n in _pip_names(rest[1:])]
    if cmd == "bun" and rest and rest[0] in ("add", "install", "i"):
        if _has_index_flag(rest[1:], _NPM_INDEX_FLAGS):
            return []
        return [("npm", n) for n in _npm_names(rest[1:])]
    if cmd == "gem" and rest and rest[0] == "install":
        if _has_index_flag(rest[1:], _GEM_INDEX_FLAGS):
            return []
        return [("rubygems", n) for n in _gem_names(rest[1:])]
    if cmd == "bundle" and rest and rest[0] == "add":
        if _has_index_flag(rest[1:], _GEM_INDEX_FLAGS):
            return []
        return [("rubygems", n) for n in _gem_names(rest[1:])]
    if cmd == "composer" and rest and rest[0] in ("require", "require-dev"):
        return [("packagist", n) for n in _composer_names(rest[1:])]
    return []


def package_specs(command):
    """[(ecosystem, name)] this command installs, order-preserving dedup."""
    command = _mask_heredocs(command)
    found = []
    for segment in _split_segments(command):
        found.extend(_segment_package_specs(_tokens(segment)))
    seen, result = set(), []
    for item in found:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _positional_after(rest, verb):
    """True if a non-flag token (a package name) follows `verb` in `rest`."""
    seen_verb = False
    for t in rest:
        if not seen_verb:
            seen_verb = t == verb
            continue
        if t and not t.startswith("-"):
            return True
    return False


def _dash_r_file(rest):
    """The file named by `-r`/`--requirement` (a manifest), else None."""
    for i, t in enumerate(rest):
        if t in ("-r", "--requirement") and i + 1 < len(rest):
            return rest[i + 1]
        if t.startswith("--requirement="):
            return t.split("=", 1)[1]
    return None


def _segment_manifest_install(tokens):
    """(ecosystem, manifest_path) if this segment is a bare, manifest-resolving
    install (no positional package names), else None."""
    tokens = _strip_prefixes(tokens)
    if not tokens:
        return None
    cmd, rest = os.path.basename(tokens[0]), tokens[1:]
    if cmd in ("npm", "pnpm", "bun"):
        if rest and rest[0] in ("install", "i", "ci") \
                and not _positional_after(rest, rest[0]):
            return ("npm", "package.json")
    if cmd == "yarn":
        if (not rest or rest[0] == "install") \
                and not _positional_after(rest, "install"):
            return ("npm", "package.json")
    if cmd in ("pip", "pip2", "pip3") and rest[:1] == ["install"]:
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
    commands (no positional package names). Statically conservative —
    mutually exclusive with package_specs (commands carrying names)."""
    command = _mask_heredocs(command)
    found = []
    for segment in _split_segments(command):
        hit = _segment_manifest_install(_tokens(segment))
        if hit and hit not in found:
            found.append(hit)
    return found
