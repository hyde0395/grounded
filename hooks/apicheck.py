"""G-6 (opt-in): validate `from X import Y` against the installed module's
top-level namespace WITHOUT importing it — a no-execution, no-LLM check for
hallucinated API/identifier names (the case FORGE'26 / arXiv 2601.19106 shows
is reachable deterministically).

Conservative by construction (spec §05): we only return False (a confident
"this symbol isn't there") when we can see the module's *full* top-level
namespace from its source. Anything opaque — a C-extension (no `.py`), a
star-import, a module-level `__getattr__`, a dotted/uninstalled module — returns
None and the caller stays silent. So a hit is high-confidence; misses are
plentiful, which is the right trade-off for a guardrail.
"""
import ast
import importlib.util


def from_import_symbols(content):
    """[(module, name)] for absolute `from X import a, b` statements in `content`.

    Skips relative imports (`from . import x`), star imports, and plain
    `import X`. Returns [] if the source does not parse."""
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name != "*":
                    out.append((node.module, alias.name))
    return out


def _top_level_names(module):
    """(names, resolvable). resolvable is False whenever we cannot see the full
    namespace, so the caller must not treat a miss as 'absent'."""
    if "." in module:
        return set(), False  # find_spec on a dotted name imports the parent pkg
    try:
        spec = importlib.util.find_spec(module)  # locates without executing
    except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
        return set(), False
    if spec is None or not spec.origin or not spec.origin.endswith(".py"):
        return set(), False  # not found, C-extension, builtin, or namespace pkg
    try:
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return set(), False
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return names, False  # re-exports an unknown namespace
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    if "__getattr__" in names:
        return names, False  # module synthesizes attributes dynamically
    return names, True


def validate(module, name):
    """True if `name` is a top-level attribute of `module`, False if confidently
    absent, None if the module's namespace cannot be fully resolved offline."""
    names, resolvable = _top_level_names(module)
    if not resolvable:
        return None
    return name in names
