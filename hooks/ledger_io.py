"""Shared ledger I/O for grounded hooks.

Hooks are stateless one-shot processes; this file IS the session state.
Philosophy (spec §05): false positives are worse than misses — when the
ledger is unreadable, callers fail open.
"""
import contextlib
import json
import os
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt  # Windows region locks
except ImportError:
    msvcrt = None

LEDGER_DIR = ".grounded"
LEDGER_FILE = "ledger.json"
LOCK_FILE = "ledger.lock"
CONFIG_FILE = "config.json"

# Canonical toggle names. g-1s is the shell-write arm of G-1; g-4 is the Stop
# speech gate (dead links in the answer text); g-2-recent is the opt-in
# recently-published-package warning; audit is the opt-in decision log;
# custom-rules runs user rules from .grounded/rules.json (inert without that
# file); grep-evidence controls whether a Grep counts as having read the file
# (strict mode: off).
RULES = ("g-1", "g-1s", "g-2", "g-2-recent", "g-3", "g-4", "audit",
         "custom-rules", "freshness", "grep-evidence")

# Rules that ship OFF and are enabled per-project (heuristics or side-features
# that are a deliberate opt-in, not the default).
OPT_IN = ("g-2-recent", "audit")


def _canon(name):
    return str(name).strip().lower().replace("_", "-")


def resolve_root(cwd, env=None):
    """Directory that owns .grounded/ — NOT necessarily the payload cwd.

    The hook payload's cwd follows shell `cd`, so anchoring state to it
    would orphan the ledger the moment the session changes directory
    (observed live: a subdir's empty ledger false-blocked a recorded file).
    Preference: $CLAUDE_PROJECT_DIR if valid, else the nearest ancestor
    (cwd included) that already has a .grounded dir, else cwd itself.
    """
    env = os.environ if env is None else env
    project_dir = env.get("CLAUDE_PROJECT_DIR")
    if project_dir and os.path.isdir(project_dir):
        return project_dir
    probe = os.path.realpath(cwd)
    while True:
        if os.path.isdir(os.path.join(probe, LEDGER_DIR)):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return cwd
        probe = parent


def load_config(cwd, env=None):
    """Enabled flag per rule from .grounded/config.json + GROUNDED_DISABLE.

    Absent or corrupt config enables everything (the toggles exist to opt
    out, so failure to read them must not change default behavior).
    """
    cfg = {rule: rule not in OPT_IN for rule in RULES}
    try:
        with open(os.path.join(cwd, LEDGER_DIR, CONFIG_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        data = None
    if isinstance(data, dict):
        for key, value in data.items():
            name = _canon(key)
            if name in cfg and isinstance(value, bool):
                cfg[name] = value
    env = os.environ if env is None else env
    for name in (env.get("GROUNDED_DISABLE") or "").split(","):
        name = _canon(name)
        if name in cfg:
            cfg[name] = False
    return cfg


def default_ledger():
    # compacted_at: unix ts of the last compaction (0 = none). Reads recorded
    # before it may have been evicted from the model's context window.
    return {"read_files": {}, "verified_urls": {}, "known_pkgs": {},
            "warned": {}, "compacted_at": 0}


def ledger_path(cwd):
    return os.path.join(cwd, LEDGER_DIR, LEDGER_FILE)


def load_ledger(cwd):
    """Ledger dict; default if the file is absent; None if corrupt."""
    try:
        with open(ledger_path(cwd), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return default_ledger()
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    merged = default_ledger()
    for key in merged:
        val = data.get(key)
        if isinstance(merged[key], dict):
            if isinstance(val, dict):
                merged[key] = val
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            merged[key] = val  # scalar sections (e.g. compacted_at)
    return merged


def save_ledger(cwd, ledger):
    """Atomic replace so parallel hook invocations never leave partial JSON."""
    path = ledger_path(cwd)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@contextlib.contextmanager
def _locked(cwd):
    """Exclusive advisory lock; any failure degrades to running unlocked.

    POSIX uses flock; Windows uses an msvcrt region lock on the first byte
    (LK_LOCK retries ~10s, then raises — treated as running unlocked).
    """
    if fcntl is None and msvcrt is None:
        yield
        return
    lock_path = os.path.join(cwd, LEDGER_DIR, LOCK_FILE)
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        f = open(lock_path, "a+")
    except OSError:
        yield
        return
    win_locked = False
    try:
        try:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_EX)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                win_locked = True
        except OSError:
            pass
        yield
    finally:
        if win_locked:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        f.close()  # close releases the flock


def update_ledger(cwd, mutate):
    """Read-modify-write as one locked step.

    Parallel tool calls mean parallel hook processes; an unsynchronized
    load→save pair lets one writer overwrite another's accrual. `mutate`
    receives the ledger dict and edits it in place. Corrupt state heals
    to a fresh ledger (recording must never crash or block).
    """
    with _locked(cwd):
        ledger = load_ledger(cwd)
        if ledger is None:
            ledger = default_ledger()
        mutate(ledger)
        save_ledger(cwd, ledger)


def normalize(path, cwd):
    """Absolute, symlink-resolved path so Read('./a.py') grounds Edit('/abs/a.py').

    Expands `~` first: shell-derived targets (e.g. `>> ~/.zshrc`) arrive unexpanded.
    """
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    # normcase: identity on POSIX; on Windows it folds case and separators so
    # C:\Foo and c:/foo land on the same ledger key.
    return os.path.normcase(os.path.realpath(path))
