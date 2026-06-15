#!/bin/sh
# Build a clean sandbox for the demo recording: a tiny project with
# grounded's hooks wired in and permissions pre-allowed, so the GIF shows
# blocking verdicts — not permission popups.
set -eu

SANDBOX="/tmp/grounded-demo"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

rm -rf "$SANDBOX"
mkdir -p "$SANDBOX/.claude"

cat > "$SANDBOX/config.yaml" <<'EOF'
# demo service config
server:
  host: 0.0.0.0
  port: 8080
  workers: 4
EOF

# Off-screen behavior rules so the visible prompts can stay human and short.
# These keep the live model on-script; the hook blocks themselves are real.
cat > "$SANDBOX/CLAUDE.md" <<'EOF'
# Working rules for this project

- If a command is blocked by a hook, just report what happened and stop.
  Do not retry it, do not search for or suggest an alternative package,
  and do not try to work around the block.
- When asked to install a package, install exactly the name given. Do not
  "correct" the spelling, and do not first check whether it is already
  installed — just run the install.
- For a tiny config tweak, prefer a quick `sed -i` one-liner over opening
  the file, unless asked to open it.
EOF

cat > "$SANDBOX/.claude/settings.json" <<EOF
{
  "permissions": {
    "allow": ["Bash", "Read", "Edit", "Write"]
  },
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "sh \\"$REPO/hooks/run.sh\\" \\"$REPO/hooks/session_start.py\\"" }] }
    ],
    "PostToolUse": [
      { "matcher": "Read|Grep|Edit|Write|MultiEdit|NotebookEdit|Bash|WebFetch",
        "hooks": [{ "type": "command", "command": "sh \\"$REPO/hooks/run.sh\\" \\"$REPO/hooks/post_record.py\\"" }] }
    ],
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|WebFetch",
        "hooks": [{ "type": "command", "command": "sh \\"$REPO/hooks/run.sh\\" \\"$REPO/hooks/pre_gate.py\\"" }] }
    ]
  }
}
EOF

echo "sandbox ready: $SANDBOX"
