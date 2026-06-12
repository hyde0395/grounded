#!/bin/sh
# Cross-platform Python launcher for grounded hooks.
# macOS/Linux ship `python3`; Windows (Git Bash) often has only `python`.
# If neither exists, exit 0: grounded fails open rather than breaking tools.
PY="$(command -v python3 || command -v python)" || exit 0
[ -n "$PY" ] || exit 0
exec "$PY" "$@"
