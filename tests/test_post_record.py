import json
import os
import subprocess
import sys
import tempfile
import unittest

from hook_runner import HOOKS_DIR, run_hook


def payload(tool_name, tool_input, cwd, tool_response=None):
    return {"hook_event_name": "PostToolUse", "tool_name": tool_name,
            "tool_input": tool_input, "tool_response": tool_response or {},
            "cwd": cwd}


class PostRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # realpath: macOS /var is a symlink to /private/var
        self.cwd = os.path.realpath(self.tmp.name)
        self.ledger = os.path.join(self.cwd, ".grounded", "ledger.json")

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        p = os.path.join(self.cwd, name)
        with open(p, "w") as f:
            f.write("x")
        return p

    def read_files(self):
        with open(self.ledger) as f:
            return json.load(f)["read_files"]

    def test_read_records_file_with_timestamp(self):
        p = self.touch("a.py")
        r = run_hook("post_record.py", payload("Read", {"file_path": p}, self.cwd))
        self.assertEqual(r.returncode, 0)
        self.assertIn(p, self.read_files())
        self.assertIsInstance(self.read_files()[p], int)

    def test_creates_ledger_dir_if_missing(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Read", {"file_path": p}, self.cwd))
        self.assertTrue(os.path.exists(self.ledger))

    def test_write_grounds_the_written_file(self):
        p = self.touch("made.py")
        run_hook("post_record.py", payload("Write", {"file_path": p, "content": "x"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_relative_path_is_normalized_to_absolute(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Read", {"file_path": "a.py"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_grep_on_single_file_records_it(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Grep", {"pattern": "x", "path": p}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_grep_on_directory_records_nothing(self):
        run_hook("post_record.py", payload("Grep", {"pattern": "x", "path": self.cwd}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_bash_cat_records_existing_file(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"cat {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_cat_ignores_missing_file(self):
        run_hook("post_record.py", payload("Bash", {"command": "cat /no/such/file.py"}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_bash_less_records_existing_file(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"less {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_more_records_existing_file(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"more {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_bat_records_existing_file(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"bat {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_view_records_existing_file(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"view {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_head_records_existing_file(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"head {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_head_with_line_flag_records_file_not_count(self):
        # `head -n 5 file`: the `5` is a count, not a path — only the file grounds
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"head -n 5 {p}"}, self.cwd))
        self.assertEqual(list(self.read_files()), [p])

    def test_bash_tail_records_existing_file(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"tail -20 {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_sed_print_mode_records_file(self):
        # `sed -n p file` prints (reads) the file without modifying it
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"sed -n p {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_sed_without_n_does_not_record(self):
        # plain `sed 's/a/b/' file` prints transformed content, not the original
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"sed 's/a/b/' {p}"}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_unrelated_bash_records_nothing(self):
        run_hook("post_record.py", payload("Bash", {"command": "ls -la"}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_bash_truncate_write_grounds_the_file(self):
        # the model knows the full content it just wrote with `>`
        p = self.touch("made.txt")
        run_hook("post_record.py", payload("Bash", {"command": f"echo hi > {p}"}, self.cwd))
        self.assertIn(p, self.read_files())

    def test_bash_append_does_not_ground_the_file(self):
        # appending blind says nothing about the rest of the file
        p = self.touch("log.txt")
        run_hook("post_record.py", payload("Bash", {"command": f"echo hi >> {p}"}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_bash_cp_does_not_ground_the_destination(self):
        # the destination now holds the *source's* content, which the model
        # has not necessarily seen — unlike `>` it authored nothing
        src = self.touch("src.py")
        dst = self.touch("dst.py")
        run_hook("post_record.py", payload("Bash", {"command": f"cp {src} {dst}"}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_bash_sed_inplace_does_not_ground_the_file(self):
        # sed transforms content the model never saw — still unknown
        p = self.touch("a.py")
        run_hook("post_record.py", payload("Bash", {"command": f"sed -i 's/a/b/' {p}"}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_bash_git_diff_output_grounds_diffed_files(self):
        # seeing the full diff of a file is read evidence for it
        p = self.touch("a.py")
        stdout = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                  "@@ -1 +1 @@\n-x\n+y\n")
        run_hook("post_record.py", payload(
            "Bash", {"command": "git diff"}, self.cwd,
            tool_response={"stdout": stdout, "stderr": ""}))
        self.assertIn(p, self.read_files())

    def test_bash_git_show_records_post_image_path_on_rename(self):
        p = self.touch("new.py")
        stdout = "diff --git a/old.py b/new.py\n--- a/old.py\n+++ b/new.py\n"
        run_hook("post_record.py", payload(
            "Bash", {"command": "git show HEAD"}, self.cwd,
            tool_response={"stdout": stdout, "stderr": ""}))
        self.assertIn(p, self.read_files())

    def test_diff_headers_from_non_git_command_not_grounded(self):
        # cat-ing a patch file shows diff text but is not `git diff` output
        self.touch("a.py")
        stdout = "diff --git a/a.py b/a.py\n"
        run_hook("post_record.py", payload(
            "Bash", {"command": "cat fix.patch"}, self.cwd,
            tool_response={"stdout": stdout, "stderr": ""}))
        self.assertFalse(os.path.exists(self.ledger))

    def test_git_diff_header_for_missing_file_not_grounded(self):
        stdout = "diff --git a/gone.py b/gone.py\n"
        run_hook("post_record.py", payload(
            "Bash", {"command": "git diff"}, self.cwd,
            tool_response={"stdout": stdout, "stderr": ""}))
        self.assertFalse(os.path.exists(self.ledger))

    def test_grep_content_output_grounds_listed_files(self):
        p = self.touch("a.py")
        run_hook("post_record.py", payload(
            "Grep", {"pattern": "x", "path": self.cwd, "output_mode": "content"},
            self.cwd, tool_response={"content": f"{p}:1:x\n"}))
        self.assertIn(p, self.read_files())

    def test_grep_files_with_matches_output_not_grounded(self):
        # a filename listing proves the file matched, not that it was seen
        p = self.touch("a.py")
        run_hook("post_record.py", payload(
            "Grep", {"pattern": "x", "path": self.cwd,
                     "output_mode": "files_with_matches"},
            self.cwd, tool_response={"content": f"{p}\n"}))
        self.assertFalse(os.path.exists(self.ledger))

    def test_webfetch_success_records_url_as_alive(self):
        run_hook("post_record.py",
                 payload("WebFetch", {"url": "https://a.com/p#frag", "prompt": "x"}, self.cwd))
        with open(self.ledger) as f:
            urls = json.load(f)["verified_urls"]
        self.assertEqual(urls.get("https://a.com/p"), 200)

    def test_parallel_recorders_do_not_lose_accruals(self):
        # Claude Code runs parallel tool calls → parallel post_record
        # processes. Unsynchronized read-modify-write drops entries
        # (observed live: 3 of 4 parallel Reads went unrecorded).
        paths = [self.touch(f"f{i}.py") for i in range(12)]
        procs = [
            subprocess.Popen(
                [sys.executable, os.path.join(HOOKS_DIR, "post_record.py")],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True)
            for _ in paths
        ]
        for proc, p in zip(procs, paths):
            proc.stdin.write(json.dumps(payload("Read", {"file_path": p}, self.cwd)))
            proc.stdin.close()
        for proc in procs:
            self.assertEqual(proc.wait(timeout=30), 0)
        recorded = self.read_files()
        missing = [p for p in paths if p not in recorded]
        self.assertEqual(missing, [])

    def test_update_ledger_works_without_any_lock_primitive(self):
        # platforms with neither fcntl nor msvcrt degrade to lock-free
        sys.path.insert(0, HOOKS_DIR)
        import ledger_io
        saved_fcntl, saved_msvcrt = ledger_io.fcntl, ledger_io.msvcrt
        ledger_io.fcntl = ledger_io.msvcrt = None
        try:
            ledger_io.update_ledger(self.cwd, lambda l: l["read_files"].update({"/x": 1}))
        finally:
            ledger_io.fcntl, ledger_io.msvcrt = saved_fcntl, saved_msvcrt
        self.assertEqual(self.read_files(), {"/x": 1})

    def test_corrupt_ledger_is_healed_not_crashed(self):
        os.makedirs(os.path.dirname(self.ledger), exist_ok=True)
        with open(self.ledger, "w") as f:
            f.write("{not json")
        p = self.touch("a.py")
        r = run_hook("post_record.py", payload("Read", {"file_path": p}, self.cwd))
        self.assertEqual(r.returncode, 0)
        self.assertIn(p, self.read_files())


if __name__ == "__main__":
    unittest.main()
