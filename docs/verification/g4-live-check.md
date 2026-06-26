# G-4 (speech gate) live verification

G-4 adds a `Stop` hook that reads the finished answer from the transcript and
blocks the turn if it cites a dead link. Two layers need verifying; the first
is automated here, the second needs a human in a real Claude Code session.

## Layer 1 — wiring (automated, ✅ verified 2026-06-26)

Everything up to and including the hook process is verified without a live
session:

- **Offline unit/E2E:** `test_text_scan.py` (13) + `test_stop_gate.py` (13) pass
  in the full suite. Dead link → `decision: block`, alive → silent, ambiguous
  → advisory, code-fence link ignored, `stop_hook_active` short-circuits.
- **`run.sh` end-to-end:** piping a `Stop` payload to
  `sh hooks/run.sh hooks/stop_gate.py` returns `decision: block` for a dead
  link (cached and live-404), silent for a live-200 link.
- **Plugin-style invocation:** the exact command from `hooks/hooks.json`'s
  `Stop` entry, with `${CLAUDE_PLUGIN_ROOT}` expanded the way Claude Code does,
  produces `decision: block` for an answer that cites a dead link:

  ```
  command: sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/stop_gate.py"
  → {"decision": "block", "reason": "[grounded G-4] Your response cites links that appear dead: ..."}
  ```

## Layer 2 — Claude Code runtime (needs a human, ⬜ pending)

What automation cannot confirm: whether Claude Code itself (a) fires the `Stop`
event with a readable `transcript_path`, and (b) honors a `decision: block`
output by re-prompting the model. These are documented behaviors (per the
official hooks docs), and SessionStart/PreToolUse firing was verified live on
2026-06-12 — but the `Stop` event has not yet been observed live.

### Manual steps

1. Add this repo as a local marketplace and install the plugin:
   - `/plugin marketplace add /Users/hyde/Documents/grounded`
   - `/plugin install grounded`
   - Confirm with `/hooks` that a `Stop` hook is listed.
2. In a throwaway project, ask the agent something whose answer will cite a URL,
   and steer it toward a **known-dead** link (e.g. ask it to reference a page you
   know returns 404), so the final answer contains that URL **outside any code
   block**.
3. Expected: when the agent finishes, the `Stop` hook blocks the turn and the
   model receives the `[grounded G-4]` reason and revises the answer (removes or
   replaces the dead link). The block happens **at most once** per turn
   (`stop_hook_active` guard) — if it can't fix the link it is allowed to stop.
4. Negative check: an answer citing only **live** links, or a dead link inside a
   fenced code block, should **not** block.
5. Uninstall when done to avoid double-hooks during development:
   `/plugin uninstall grounded`.

### Record the result

When performed, note the outcome (CC version, did `Stop` fire, was the block
honored) in `CLAUDE.md` §8 (verification history), the way the 2026-06-12
plugin check was recorded.
