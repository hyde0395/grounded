"""Pure parsing of Bash commands — no I/O, no LLM.

Extracts (a) file write targets and (b) package install specs so pre_gate
can demand evidence for them. Parsing is deliberately conservative: anything
we cannot resolve statically (variables, substitutions, quoted targets) is
skipped, because a false block costs more than a miss (spec §05).
"""
import os
import re
import shlex

TRUNCATE = "truncate"   # > , tee          — destroys content never seen
APPEND = "append"       # >>, tee -a       — adds blindly, but preserves
INPLACE = "inplace"     # sed -i, perl -i  — rewrites content never seen
OVERWRITE = "overwrite"  # cp/mv onto file — replaces with content the model
#                          may not have seen either (never accrued as a read)

_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_OPERATORS = re.compile(r"\|\||&&|[|;\n]")
# `>|` forces a clobber past noclobber — same destructive write as `>`.
_REDIRECT = re.compile(r"(>>|>\||>)\s*([^\s|;&<>]+)")
_UNRESOLVABLE = set("$`(){}*?")
_SKIP_PREFIXES = ("/dev/", "/proc/", "-")


def _mask_quotes(command):
    """Blank out quoted spans (same length) so operators inside quotes are inert."""
    return _QUOTED.sub(lambda m: " " * len(m.group(0)), command)


# Delimiter must start like an identifier so $((1<<2)) is not a heredoc.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def _mask_heredocs(command):
    """Blank out heredoc bodies (same length) — they are data, not shell.

    The opener line itself is kept: `cat <<EOF > out.txt` still redirects.
    """
    pos = 0
    while True:
        m = _HEREDOC.search(command, pos)
        if m is None:
            return command
        body_start = command.find("\n", m.end()) + 1
        if body_start == 0:  # no newline → no body in this string
            return command
        terminator = re.compile(r"^\t*" + re.escape(m.group(2)) + r"[ \t]*$",
                                re.MULTILINE)
        t = terminator.search(command, body_start)
        body_end = t.start() if t else len(command)
        masked = re.sub(r"[^\n]", " ", command[body_start:body_end])
        command = command[:body_start] + masked + command[body_end:]
        pos = t.end() if t else len(command)


def _split_segments(command):
    """Split on | ; && || found OUTSIDE quotes, returning original-text segments."""
    masked = _mask_quotes(command)
    segments, last = [], 0
    for m in _OPERATORS.finditer(masked):
        segments.append(command[last:m.start()])
        last = m.end()
    segments.append(command[last:])
    return segments


def _plausible_path(target):
    if not target or target.startswith(_SKIP_PREFIXES):
        return False
    return not (_UNRESOLVABLE & set(target))


def _tokens(segment):
    try:
        toks = shlex.split(segment)
    except ValueError:
        return []
    return toks


def _positionals(tokens, value_flags, drop_first_unless_flagged):
    """Non-flag args, minus values consumed by `value_flags`; optionally drop
    the leading positional (an inline script/program, not a file)."""
    out, flagged, skip_next = [], False, False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if not tok:
            continue
        if tok in value_flags:
            flagged = True
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        out.append(tok)
    if drop_first_unless_flagged and not flagged and out:
        out = out[1:]
    return out


def _sed_inplace_flag(token):
    return (token == "--in-place" or token.startswith("--in-place=")
            or (token.startswith("-i") and not token.startswith("--")))


def _inplace_invocation(tokens):
    """'sed -i' / 'perl -i' if `tokens` invoke an in-place editor, else None."""
    for i, tok in enumerate(tokens):
        base = os.path.basename(tok)
        rest = tokens[i + 1:]
        if base == "sed" and any(_sed_inplace_flag(t) for t in rest):
            return "sed -i"
        if base == "perl" and any(t.startswith("-i") for t in rest):
            return "perl -i"
    return None


def _awk_inplace(rest):
    """True if awk/gawk args load the in-place extension (`-i inplace`)."""
    for i, t in enumerate(rest):
        if t == "-i" and i + 1 < len(rest) and rest[i + 1] == "inplace":
            return True
        if t in ("-iinplace", "--include=inplace"):
            return True
    return False


def _segment_batch_hint(tokens):
    tokens = _strip_prefixes(tokens)
    if not tokens:
        return None
    cmd, rest = os.path.basename(tokens[0]), tokens[1:]
    if cmd == "xargs":
        tool = _inplace_invocation(rest)
        if tool:
            return f"xargs {tool}"
    if cmd == "find":
        for flag in ("-exec", "-execdir"):
            if flag in rest:
                tool = _inplace_invocation(rest[rest.index(flag) + 1:])
                if tool:
                    return f"find {flag} {tool}".replace("-execdir", "-exec")
    return None


def batch_write_hints(command):
    """Batch in-place writes whose targets only exist at run time
    (`find -exec sed -i`, `xargs sed -i`). Statically unresolvable —
    callers can warn, never block (spec §05)."""
    command = _mask_heredocs(command)
    found = []
    for segment in _split_segments(command):
        hint = _segment_batch_hint(_tokens(segment))
        if hint and hint not in found:
            found.append(hint)
    return found


def _segment_write_targets(tokens):
    if not tokens:
        return []
    cmd = os.path.basename(tokens[0])
    rest = tokens[1:]
    if cmd == "sudo" and rest:
        cmd, rest = os.path.basename(rest[0]), rest[1:]
    if cmd == "sed":
        if not any(_sed_inplace_flag(t) for t in rest):
            return []
        files = _positionals(rest, {"-e", "-f", "--expression", "--file"}, True)
        return [(f, INPLACE) for f in files]
    if cmd == "perl":
        if not any(t.startswith("-i") for t in rest):
            return []
        # flags bundling 'e' (-e, -pe, -ne) consume the next token as the script
        out, skip_next, script_inline = [], False, False
        for tok in rest:
            if skip_next:
                skip_next = False
                continue
            if tok.startswith("-") and "e" in tok[1:]:
                script_inline = True
                skip_next = True
                continue
            if tok.startswith("-") or not tok:
                continue
            out.append(tok)
        if not script_inline and out:
            out = out[1:]  # first positional is the program file (read, not written)
        return [(f, INPLACE) for f in out]
    if cmd == "tee":
        mode = APPEND if any(t in ("-a", "--append") for t in rest) else TRUNCATE
        return [(f, mode) for f in _positionals(rest, set(), False)]
    if cmd in ("cp", "mv"):
        if any(t in ("-n", "--no-clobber") for t in rest):
            return []  # explicitly refuses to overwrite — nothing at risk
        if any(t == "-t" or t.startswith("--target-directory") for t in rest):
            return []  # per-file targets inside a directory: unresolvable
        files = _positionals(rest, set(), False)
        if len(files) < 2:
            return []
        return [(files[-1], OVERWRITE)]
    if cmd == "dd":
        # of= names an output the model writes; content is if='s, not authored
        for tok in rest:
            if tok.startswith("of="):
                target = tok[3:]
                if target and target != "/dev/stdout":
                    return [(target, OVERWRITE)]
        return []
    if cmd == "truncate":
        # shrinks/zeroes a file — destroys content the model never saw
        files = _positionals(rest, {"-s", "--size", "-r", "--reference"}, False)
        return [(f, OVERWRITE) for f in files]
    if cmd in ("awk", "gawk"):
        if not _awk_inplace(rest):
            return []
        value_flags = {"-i", "-v", "-F", "-f", "--include", "--assign",
                       "--field-separator", "--file", "--source"}
        has_program_file = any(t in ("-f", "--file") for t in rest)
        files, skip_next = [], False
        for tok in rest:
            if skip_next:
                skip_next = False
                continue
            if tok in value_flags:
                skip_next = True
                continue
            if tok.startswith("-") or not tok:
                continue
            files.append(tok)
        if not has_program_file and files:
            files = files[1:]  # first positional is the inline awk program
        return [(f, INPLACE) for f in files]
    return []


def write_targets(command):
    """[(raw_path, mode)] of files this command writes, order-preserving dedup."""
    command = _mask_heredocs(command)
    found = []
    masked = _mask_quotes(command)
    for op, target in _REDIRECT.findall(masked):
        if _plausible_path(target):
            found.append((target, APPEND if op == ">>" else TRUNCATE))
    for segment in _split_segments(command):
        for target, mode in _segment_write_targets(_tokens(segment)):
            if _plausible_path(target):
                found.append((target, mode))
    seen, result = set(), []
    for item in found:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


_PIP_VALUE_FLAGS = {
    "-r", "-c", "-t", "-i", "-f", "--requirement", "--constraint", "--target",
    "--index-url", "--extra-index-url", "--find-links", "--platform",
    "--python-version", "--abi", "--implementation", "-e", "--editable",
}
_NON_REGISTRY_PREFIXES = ("file:", "git+", "http:", "https:", "github:", ".", "/", "~")


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


def _strip_prefixes(tokens):
    """Drop leading VAR=val assignments and sudo."""
    while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
        tokens = tokens[1:]
    if tokens and os.path.basename(tokens[0]) == "sudo":
        tokens = tokens[1:]
    return tokens


def _segment_package_specs(tokens):
    tokens = _strip_prefixes(tokens)
    if not tokens:
        return []
    cmd, rest = os.path.basename(tokens[0]), tokens[1:]
    if cmd in ("npm", "pnpm") and rest and rest[0] in ("install", "i", "add"):
        return [("npm", n) for n in _npm_names(rest[1:])]
    if cmd == "yarn" and rest and rest[0] == "add":
        return [("npm", n) for n in _npm_names(rest[1:])]
    if cmd in ("pip", "pip2", "pip3") and rest and rest[0] == "install":
        return [("pypi", n) for n in _pip_names(rest[1:])]
    if cmd in ("python", "python2", "python3") and len(rest) >= 3 \
            and rest[0] == "-m" and rest[1] == "pip" and rest[2] == "install":
        return [("pypi", n) for n in _pip_names(rest[3:])]
    if cmd == "uv" and rest:
        if rest[0] == "add":
            return [("pypi", n) for n in _pip_names(rest[1:])]
        if len(rest) >= 2 and rest[0] == "pip" and rest[1] == "install":
            return [("pypi", n) for n in _pip_names(rest[2:])]
    if cmd == "cargo" and rest and rest[0] in ("add", "install"):
        return [("crates", n) for n in _cargo_names(rest[1:])]
    if cmd == "poetry" and rest and rest[0] == "add":
        return [("pypi", n) for n in _pip_names(rest[1:])]
    if cmd == "bun" and rest and rest[0] in ("add", "install", "i"):
        return [("npm", n) for n in _npm_names(rest[1:])]
    if cmd == "gem" and rest and rest[0] == "install":
        return [("rubygems", n) for n in _gem_names(rest[1:])]
    if cmd == "bundle" and rest and rest[0] == "add":
        return [("rubygems", n) for n in _gem_names(rest[1:])]
    if cmd == "composer" and rest and rest[0] in ("require", "require-dev"):
        return [("packagist", n) for n in _composer_names(rest[1:])]
    return []


_DATA_FLAGS_PREFIXES = ("-X", "--request", "-d", "--data", "-F", "--form",
                        "-T", "--upload-file", "--post-data", "--post-file",
                        "--method", "--body-data", "--body-file")
_URL = re.compile(r"^https?://", re.IGNORECASE)


def _segment_fetch_urls(tokens):
    tokens = _strip_prefixes(tokens)
    if not tokens or os.path.basename(tokens[0]) not in ("curl", "wget"):
        return []
    rest = tokens[1:]
    # -X POST / --data … means an API call, not a citation fetch; a HEAD
    # probe against such an endpoint would be a false dead-signal.
    if any(t.startswith(_DATA_FLAGS_PREFIXES) for t in rest):
        return []
    return [t for t in rest if _URL.match(t)]


def fetch_urls(command):
    """[url] this command fetches (GET-style curl/wget only), deduped."""
    command = _mask_heredocs(command)
    found = []
    for segment in _split_segments(command):
        found.extend(_segment_fetch_urls(_tokens(segment)))
    seen, result = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


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
