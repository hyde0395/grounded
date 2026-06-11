"""Run a hook script the way Claude Code does: subprocess + JSON on stdin."""
import json
import os
import subprocess
import sys

HOOKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")


def run_hook(script, payload):
    return subprocess.run(
        [sys.executable, os.path.join(HOOKS_DIR, script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
