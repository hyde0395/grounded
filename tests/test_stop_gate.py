"""stop_gate entrypoint (G-4 speech gate) — subprocess, network faked via
the verified_urls cache (a seeded status short-circuits check_url)."""
import json
import os
import tempfile
import unittest

from hook_runner import run_hook


class StopGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_ledger(self, verified_urls=None):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            json.dump({"read_files": {}, "verified_urls": verified_urls or {},
                       "known_pkgs": {}, "warned": {}, "compacted_at": 0}, f)

    def transcript(self, text, trailing_tool=False):
        p = os.path.join(self.cwd, "t.jsonl")
        events = [
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant",
             "message": {"content": [{"type": "text", "text": text}]}},
        ]
        if trailing_tool:  # a tool_use-only event after the text answer
            events.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {}}]}})
        with open(p, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        return p

    def payload(self, transcript_path, stop_hook_active=False):
        return {"hook_event_name": "Stop", "transcript_path": transcript_path,
                "stop_hook_active": stop_hook_active, "cwd": self.cwd}

    # --- blocking on a dead cited link ---
    def test_dead_link_blocks(self):
        self.write_ledger({"https://a.com/dead": 404})
        t = self.transcript("you can read more at https://a.com/dead today")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("https://a.com/dead", out["reason"])
        self.assertIn("[grounded G-4]", out["reason"])

    def test_dns_dead_link_blocks(self):
        self.write_ledger({"https://nope.invalid/x": 0})
        t = self.transcript("source: https://nope.invalid/x")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    # --- alive link: silent pass ---
    def test_alive_link_silent(self):
        self.write_ledger({"https://a.com/ok": 200})
        t = self.transcript("see https://a.com/ok for the docs")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    # --- ambiguous (403): advisory warn, never block ---
    def test_ambiguous_link_warns_not_blocks(self):
        self.write_ledger({"https://a.com/maybe": 403})
        t = self.transcript("possibly at https://a.com/maybe somewhere")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "Stop")
        self.assertIn("https://a.com/maybe",
                      out["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("decision", out)

    # --- code spans are illustrative: never gated (false-block control) ---
    def test_dead_link_in_code_fence_not_gated(self):
        self.write_ledger({"https://a.com/dead": 404})
        t = self.transcript("fine\n```\ncurl https://a.com/dead\n```\n")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    # --- loop safety: never block twice in a stop chain ---
    def test_stop_hook_active_short_circuits(self):
        self.write_ledger({"https://a.com/dead": 404})
        t = self.transcript("dead one: https://a.com/dead")
        r = run_hook("stop_gate.py", self.payload(t, stop_hook_active=True))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    # --- final answer is the last assistant message, ignoring trailing tools ---
    def test_trailing_tool_event_ignored(self):
        self.write_ledger({"https://a.com/dead": 404})
        t = self.transcript("link https://a.com/dead", trailing_tool=True)
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    # --- fail-open / no-op cases ---
    def test_no_transcript_path_passes(self):
        self.write_ledger({"https://a.com/dead": 404})
        p = {"hook_event_name": "Stop", "stop_hook_active": False,
             "cwd": self.cwd}
        r = run_hook("stop_gate.py", p)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_no_urls_passes(self):
        self.write_ledger()
        t = self.transcript("a plain answer with no links at all")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_disabled_via_env_passes(self):
        self.write_ledger({"https://a.com/dead": 404})
        t = self.transcript("dead https://a.com/dead")
        r = run_hook("stop_gate.py", self.payload(t),
                     env={"GROUNDED_DISABLE": "g-4"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_disabled_via_config_file_passes(self):
        self.write_ledger({"https://a.com/dead": 404})
        d = os.path.join(self.cwd, ".grounded")
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({"G-4": False}, f)
        t = self.transcript("dead https://a.com/dead")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_ambiguous_warn_is_once_per_session(self):
        # first stop warns; the claim persists to the ledger so a later stop
        # citing the same unverifiable link stays silent (no nagging).
        self.write_ledger({"https://a.com/maybe": 403})
        t = self.transcript("maybe https://a.com/maybe here")
        first = run_hook("stop_gate.py", self.payload(t))
        self.assertIn("additionalContext", first.stdout)
        second = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(second.stdout, "")

    def test_corrupt_ledger_fails_open(self):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            f.write("{not json")
        t = self.transcript("dead https://a.com/dead")
        r = run_hook("stop_gate.py", self.payload(t))
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
