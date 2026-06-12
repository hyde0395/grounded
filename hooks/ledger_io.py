"""Shared ledger I/O for grounded hooks.

Hooks are stateless one-shot processes; this file IS the session state.
Philosophy (spec §05): false positives are worse than misses — when the
ledger is unreadable, callers fail open.
"""
import json
import os
import tempfile

LEDGER_DIR = ".grounded"
LEDGER_FILE = "ledger.json"


def default_ledger():
    return {"read_files": {}, "verified_urls": {}, "known_pkgs": {}}


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
        if isinstance(data.get(key), dict):
            merged[key] = data[key]
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
