import json
import os
import subprocess
import sys
import tempfile
import unittest

from hook_runner import HOOKS_DIR, run_hook


def payload(tool_name, file_path, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": tool_name,
            "tool_input": {"file_path": file_path}, "cwd": cwd}


class PreGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # realpath: macOS /var is a symlink to /private/var
        self.cwd = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        p = os.path.join(self.cwd, name)
        with open(p, "w") as f:
            f.write("x")
        return p

    def write_ledger(self, read_files):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            json.dump({"read_files": read_files, "verified_urls": {}, "known_pkgs": {}}, f)

    def test_edit_unread_existing_file_blocked_exit2(self):
        p = self.touch("a.py")
        self.write_ledger({})
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 2)
        self.assertIn("a.py", r.stderr)
        self.assertIn("grounded", r.stderr)

    def test_edit_read_file_passes_silently(self):
        p = self.touch("a.py")
        self.write_ledger({p: 1718000000})
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_write_new_file_passes(self):
        self.write_ledger({})
        r = run_hook("pre_gate.py", payload("Write", os.path.join(self.cwd, "new.py"), self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_write_existing_unread_file_blocked(self):
        p = self.touch("a.py")
        self.write_ledger({})
        r = run_hook("pre_gate.py", payload("Write", p, self.cwd))
        self.assertEqual(r.returncode, 2)

    def test_relative_path_matches_absolute_ledger_entry(self):
        p = self.touch("a.py")
        self.write_ledger({p: 1718000000})
        r = run_hook("pre_gate.py", payload("Edit", "a.py", self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_missing_ledger_blocks_edit(self):
        p = self.touch("a.py")
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 2)

    def test_corrupt_ledger_fails_open(self):
        p = self.touch("a.py")
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            f.write("{not json")
        r = run_hook("pre_gate.py", payload("Edit", p, self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_garbage_stdin_fails_open(self):
        r = subprocess.run(
            [sys.executable, os.path.join(HOOKS_DIR, "pre_gate.py")],
            input="not json", capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)


def bash_payload(command, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "cwd": cwd}


class BashGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # realpath: macOS /var is a symlink to /private/var
        self.cwd = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        p = os.path.join(self.cwd, name)
        with open(p, "w") as f:
            f.write("x")
        return p

    def write_ledger(self, read_files=None, known_pkgs=None):
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            json.dump({"read_files": read_files or {}, "verified_urls": {},
                       "known_pkgs": known_pkgs or {}}, f)

    def test_plain_command_passes(self):
        self.write_ledger()
        r = run_hook("pre_gate.py", bash_payload("ls -la && git status", self.cwd))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_sed_inplace_on_unread_file_blocked(self):
        p = self.touch("a.py")
        self.write_ledger()
        r = run_hook("pre_gate.py", bash_payload(f"sed -i 's/a/b/' {p}", self.cwd))
        self.assertEqual(r.returncode, 2)
        self.assertIn("[grounded G-1]", r.stderr)

    def test_sed_inplace_on_read_file_passes(self):
        p = self.touch("a.py")
        self.write_ledger(read_files={p: 1})
        r = run_hook("pre_gate.py", bash_payload(f"sed -i 's/a/b/' {p}", self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_redirect_to_new_file_passes(self):
        self.write_ledger()
        r = run_hook("pre_gate.py", bash_payload(f"echo x > {self.cwd}/new.txt", self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_truncate_existing_unread_file_blocked(self):
        p = self.touch("a.txt")
        self.write_ledger()
        r = run_hook("pre_gate.py", bash_payload(f"echo x > {p}", self.cwd))
        self.assertEqual(r.returncode, 2)

    def test_append_existing_unread_file_warns_via_additional_context(self):
        p = self.touch("a.txt")
        self.write_ledger()
        r = run_hook("pre_gate.py", bash_payload(f"echo x >> {p}", self.cwd))
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[grounded G-1]", ctx)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_relative_redirect_target_resolved_against_cwd(self):
        self.touch("a.txt")
        self.write_ledger()
        r = run_hook("pre_gate.py", bash_payload("echo x > a.txt", self.cwd))
        self.assertEqual(r.returncode, 2)

    def test_install_cached_absent_package_blocked_without_network(self):
        self.write_ledger(known_pkgs={"pypi:reqests": False})
        r = run_hook("pre_gate.py", bash_payload("pip install reqests", self.cwd))
        self.assertEqual(r.returncode, 2)
        self.assertIn("[grounded G-2]", r.stderr)

    def test_install_cached_existing_package_passes_without_network(self):
        self.write_ledger(known_pkgs={"pypi:requests": True})
        r = run_hook("pre_gate.py", bash_payload("pip install requests", self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_corrupt_ledger_fails_open_for_bash(self):
        p = self.touch("a.py")
        d = os.path.join(self.cwd, ".grounded")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ledger.json"), "w") as f:
            f.write("{not json")
        r = run_hook("pre_gate.py", bash_payload(f"sed -i 's/a/b/' {p}", self.cwd))
        self.assertEqual(r.returncode, 0)


class UrlGateTest(unittest.TestCase):
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
                       "known_pkgs": {}}, f)

    def webfetch(self, url):
        return {"hook_event_name": "PreToolUse", "tool_name": "WebFetch",
                "tool_input": {"url": url, "prompt": "x"}, "cwd": self.cwd}

    def test_webfetch_cached_dead_url_blocked(self):
        self.write_ledger({"https://a.com/dead": 404})
        r = run_hook("pre_gate.py", self.webfetch("https://a.com/dead"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("[grounded G-3]", r.stderr)

    def test_webfetch_cached_alive_url_passes_silently(self):
        self.write_ledger({"https://a.com/ok": 200})
        r = run_hook("pre_gate.py", self.webfetch("https://a.com/ok"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_webfetch_fragment_hits_cache(self):
        self.write_ledger({"https://a.com/dead": 404})
        r = run_hook("pre_gate.py", self.webfetch("https://a.com/dead#part"))
        self.assertEqual(r.returncode, 2)

    def test_webfetch_localhost_not_checked(self):
        self.write_ledger()
        r = run_hook("pre_gate.py", self.webfetch("http://localhost:3000/api"))
        self.assertEqual(r.returncode, 0)

    def test_bash_curl_cached_dead_blocked(self):
        self.write_ledger({"https://a.com/dead": 404})
        r = run_hook("pre_gate.py", bash_payload("curl -s https://a.com/dead", self.cwd))
        self.assertEqual(r.returncode, 2)
        self.assertIn("[grounded G-3]", r.stderr)

    def test_bash_curl_post_to_cached_dead_not_gated(self):
        self.write_ledger({"https://a.com/dead": 404})
        r = run_hook("pre_gate.py",
                     bash_payload("curl -X POST https://a.com/dead", self.cwd))
        self.assertEqual(r.returncode, 0)

    def test_bash_curl_cached_alive_passes(self):
        self.write_ledger({"https://a.com/ok": 200})
        r = run_hook("pre_gate.py", bash_payload("curl https://a.com/ok", self.cwd))
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
