"""shell_scan is pure logic — test it directly, no subprocess needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import shell_scan  # noqa: E402


class WriteTargetsTest(unittest.TestCase):
    def targets(self, command):
        return shell_scan.write_targets(command)

    def test_sed_inplace_basic(self):
        self.assertEqual(self.targets("sed -i 's/a/b/' foo.py"),
                         [("foo.py", shell_scan.INPLACE)])

    def test_sed_inplace_multiple_files(self):
        self.assertEqual(self.targets("sed -i 's/a/b/' foo.py bar.py"),
                         [("foo.py", shell_scan.INPLACE), ("bar.py", shell_scan.INPLACE)])

    def test_sed_inplace_with_expression_flag(self):
        self.assertIn(("foo.py", shell_scan.INPLACE),
                      self.targets("sed -i -e 's/a/b/' foo.py"))

    def test_sed_bsd_empty_suffix(self):
        self.assertIn(("foo.py", shell_scan.INPLACE),
                      self.targets("sed -i '' 's/a/b/' foo.py"))

    def test_sed_without_inplace_is_not_a_write(self):
        self.assertEqual(self.targets("sed 's/a/b/' foo.py"), [])

    def test_perl_inplace(self):
        self.assertEqual(self.targets("perl -i -pe 's/a/b/' foo.py"),
                         [("foo.py", shell_scan.INPLACE)])

    def test_tee_truncates(self):
        self.assertEqual(self.targets("echo hi | tee foo.txt"),
                         [("foo.txt", shell_scan.TRUNCATE)])

    def test_tee_append(self):
        self.assertEqual(self.targets("echo hi | tee -a foo.txt"),
                         [("foo.txt", shell_scan.APPEND)])

    def test_redirect_truncate(self):
        self.assertEqual(self.targets("echo hi > foo.txt"),
                         [("foo.txt", shell_scan.TRUNCATE)])

    def test_redirect_append(self):
        self.assertEqual(self.targets("echo hi >> foo.txt"),
                         [("foo.txt", shell_scan.APPEND)])

    def test_redirect_no_space(self):
        self.assertEqual(self.targets("echo hi >foo.txt"),
                         [("foo.txt", shell_scan.TRUNCATE)])

    def test_stderr_redirect_counts(self):
        self.assertEqual(self.targets("cmd 2> err.log"),
                         [("err.log", shell_scan.TRUNCATE)])

    def test_redirect_inside_quotes_ignored(self):
        self.assertEqual(self.targets('echo "a > b"'), [])

    def test_variable_target_skipped(self):
        self.assertEqual(self.targets("echo hi > $OUT"), [])

    def test_command_substitution_target_skipped(self):
        self.assertEqual(self.targets("echo hi > $(mktemp)"), [])

    def test_dev_null_skipped(self):
        self.assertEqual(self.targets("cmd > /dev/null 2>&1"), [])

    def test_segments_split_on_operators(self):
        got = self.targets("cat a.txt && echo hi > b.txt; sed -i 's/x/y/' c.txt")
        self.assertIn(("b.txt", shell_scan.TRUNCATE), got)
        self.assertIn(("c.txt", shell_scan.INPLACE), got)

    def test_semicolon_inside_quotes_does_not_split(self):
        # the ';' lives inside the sed script — still one segment
        self.assertEqual(self.targets("sed -i 's/;/x/' foo.py"),
                         [("foo.py", shell_scan.INPLACE)])

    def test_plain_command_has_no_targets(self):
        self.assertEqual(self.targets("ls -la && git status"), [])

    def test_dedup(self):
        self.assertEqual(self.targets("echo a > f.txt; echo b > f.txt"),
                         [("f.txt", shell_scan.TRUNCATE)])


if __name__ == "__main__":
    unittest.main()
