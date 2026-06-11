"""Pure parsing of Bash commands — no I/O, no LLM.

Extracts (a) file write targets and (b) package install specs so pre_gate
can demand evidence for them. Parsing is deliberately conservative: anything
we cannot resolve statically (variables, substitutions, quoted targets) is
skipped, because a false block costs more than a miss (spec §05).
"""
import os
import re
import shlex

TRUNCATE = "truncate"  # > , tee          — destroys content never seen
APPEND = "append"      # >>, tee -a       — adds blindly, but preserves
INPLACE = "inplace"    # sed -i, perl -i  — rewrites content never seen

_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_OPERATORS = re.compile(r"\|\||&&|[|;]")
_REDIRECT = re.compile(r"(>>|>)\s*([^\s|;&<>]+)")
_UNRESOLVABLE = set("$`(){}*?")
_SKIP_PREFIXES = ("/dev/", "/proc/", "-")


def _mask_quotes(command):
    """Blank out quoted spans (same length) so operators inside quotes are inert."""
    return _QUOTED.sub(lambda m: " " * len(m.group(0)), command)


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


def _segment_write_targets(tokens):
    if not tokens:
        return []
    cmd = os.path.basename(tokens[0])
    rest = tokens[1:]
    if cmd == "sudo" and rest:
        cmd, rest = os.path.basename(rest[0]), rest[1:]
    if cmd == "sed":
        inplace = any(t == "--in-place" or t.startswith("--in-place=")
                      or (t.startswith("-i") and not t.startswith("--")) for t in rest)
        if not inplace:
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
    return []


def write_targets(command):
    """[(raw_path, mode)] of files this command writes, order-preserving dedup."""
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
