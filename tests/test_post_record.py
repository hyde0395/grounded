import json
import os
import tempfile
import unittest

from hook_runner import run_hook


def payload(tool_name, tool_input, cwd):
    return {"hook_event_name": "PostToolUse", "tool_name": tool_name,
            "tool_input": tool_input, "tool_response": {}, "cwd": cwd}


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

    def test_unrelated_bash_records_nothing(self):
        run_hook("post_record.py", payload("Bash", {"command": "ls -la"}, self.cwd))
        self.assertFalse(os.path.exists(self.ledger))

    def test_webfetch_success_records_url_as_alive(self):
        run_hook("post_record.py",
                 payload("WebFetch", {"url": "https://a.com/p#frag", "prompt": "x"}, self.cwd))
        with open(self.ledger) as f:
            urls = json.load(f)["verified_urls"]
        self.assertEqual(urls.get("https://a.com/p"), 200)

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
