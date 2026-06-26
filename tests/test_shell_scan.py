"""shell_scan is pure logic — test it directly, no subprocess needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import install_scan  # noqa: E402  (masking lives in shell_scan; package_specs in install_scan)
import shell_scan  # noqa: E402


class LeadingCdTest(unittest.TestCase):
    def test_absolute_dir(self):
        self.assertEqual(shell_scan.leading_cd("cd /proj && cat a.py"), "/proj")

    def test_relative_dir(self):
        self.assertEqual(shell_scan.leading_cd("cd sub && cat a.py"), "sub")

    def test_semicolon_separator(self):
        self.assertEqual(shell_scan.leading_cd("cd sub; cat a.py"), "sub")

    def test_no_cd_returns_none(self):
        self.assertIsNone(shell_scan.leading_cd("cat a.py"))

    def test_bare_cd_returns_none(self):
        self.assertIsNone(shell_scan.leading_cd("cd && cat a.py"))

    def test_unresolvable_dir_returns_none(self):
        # a variable/substitution target can't be resolved statically
        self.assertIsNone(shell_scan.leading_cd("cd $HOME && cat a.py"))
        self.assertIsNone(shell_scan.leading_cd("cd - && cat a.py"))

    def test_cd_not_first_returns_none(self):
        # only a leading cd reparents the whole command in our conservative model
        self.assertIsNone(shell_scan.leading_cd("cat a.py && cd sub"))


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

    def test_force_clobber_redirect_truncates(self):
        # `>|` overrides noclobber — same destructive write as `>`
        self.assertEqual(self.targets("echo hi >| foo.txt"),
                         [("foo.txt", shell_scan.TRUNCATE)])

    def test_force_clobber_redirect_no_space(self):
        self.assertEqual(self.targets("echo hi >|foo.txt"),
                         [("foo.txt", shell_scan.TRUNCATE)])

    def test_dd_of_overwrites(self):
        # content comes from if=, which the model has not necessarily seen
        self.assertEqual(self.targets("dd if=/dev/zero of=disk.img bs=1M count=1"),
                         [("disk.img", shell_scan.OVERWRITE)])

    def test_dd_without_of_has_no_target(self):
        self.assertEqual(self.targets("dd if=a.img | gzip"), [])

    def test_truncate_overwrites(self):
        self.assertEqual(self.targets("truncate -s 0 log.txt"),
                         [("log.txt", shell_scan.OVERWRITE)])

    def test_truncate_size_equals_form(self):
        self.assertEqual(self.targets("truncate --size=0 log.txt"),
                         [("log.txt", shell_scan.OVERWRITE)])

    def test_awk_inplace_basic(self):
        self.assertEqual(self.targets("awk -i inplace '{print}' data.txt"),
                         [("data.txt", shell_scan.INPLACE)])

    def test_awk_inplace_with_program_file_keeps_data(self):
        # `-f prog.awk` supplies the program → positionals are data files
        self.assertEqual(self.targets("gawk -i inplace -f prog.awk data.txt"),
                         [("data.txt", shell_scan.INPLACE)])

    def test_awk_without_inplace_is_not_a_write(self):
        self.assertEqual(self.targets("awk '{print}' data.txt"), [])


class BatchWriteHintsTest(unittest.TestCase):
    """find -exec / xargs feeding an in-place editor: targets are dynamic,
    so they can't be gated — but they can be flagged."""

    def hints(self, command):
        return shell_scan.batch_write_hints(command)

    def test_find_exec_sed_inplace(self):
        self.assertEqual(self.hints("find . -name '*.py' -exec sed -i 's/a/b/' {} +"),
                         ["find -exec sed -i"])

    def test_xargs_sed_inplace(self):
        self.assertEqual(self.hints("git ls-files | xargs sed -i 's/a/b/'"),
                         ["xargs sed -i"])

    def test_xargs_perl_inplace(self):
        self.assertEqual(self.hints("ls *.txt | xargs perl -i -pe 's/a/b/'"),
                         ["xargs perl -i"])

    def test_xargs_without_inplace_tool_is_silent(self):
        self.assertEqual(self.hints("git ls-files | xargs grep TODO"), [])
        self.assertEqual(self.hints("ls | xargs sed 's/a/b/'"), [])

    def test_find_without_exec_is_silent(self):
        self.assertEqual(self.hints("find . -name '*.py'"), [])

    def test_direct_sed_inplace_is_not_a_batch_hint(self):
        # resolvable targets are write_targets' job, not a hint
        self.assertEqual(self.hints("sed -i 's/a/b/' foo.py"), [])

    def test_heredoc_body_is_inert(self):
        self.assertEqual(self.hints("cat <<EOF\nxargs sed -i 's/a/b/'\nEOF"), [])


class CopyMoveTest(unittest.TestCase):
    """cp/mv onto an existing file destroys content as surely as `>` does."""

    def targets(self, command):
        return shell_scan.write_targets(command)

    def test_cp_basic(self):
        self.assertEqual(self.targets("cp a.py b.py"),
                         [("b.py", shell_scan.OVERWRITE)])

    def test_mv_basic(self):
        self.assertEqual(self.targets("mv tmp.py src/main.py"),
                         [("src/main.py", shell_scan.OVERWRITE)])

    def test_last_positional_is_destination(self):
        self.assertEqual(self.targets("cp -r a.py b.py dst.py"),
                         [("dst.py", shell_scan.OVERWRITE)])

    def test_single_argument_is_not_a_write(self):
        self.assertEqual(self.targets("cp a.py"), [])

    def test_no_clobber_is_not_a_write(self):
        self.assertEqual(self.targets("cp -n a.py b.py"), [])
        self.assertEqual(self.targets("mv --no-clobber a.py b.py"), [])

    def test_target_directory_flag_bails_out(self):
        # -t names a directory; the real per-file targets are unresolvable
        self.assertEqual(self.targets("cp -t backup a.py b.py"), [])

    def test_sudo_cp(self):
        self.assertEqual(self.targets("sudo cp a.conf /etc/app.conf"),
                         [("/etc/app.conf", shell_scan.OVERWRITE)])

    def test_variable_destination_skipped(self):
        self.assertEqual(self.targets("cp a.py $DST"), [])


class HeredocTest(unittest.TestCase):
    """Heredoc bodies are data, not shell — nothing inside them may match."""

    def test_redirect_like_text_in_body_ignored(self):
        cmd = "cat <<'EOF'\nuse a > b comparison\nEOF"
        self.assertEqual(shell_scan.write_targets(cmd), [])

    def test_redirect_on_heredoc_opener_line_still_detected(self):
        cmd = "cat <<EOF > out.txt\nbody > noise\nEOF"
        self.assertEqual(shell_scan.write_targets(cmd),
                         [("out.txt", shell_scan.TRUNCATE)])

    def test_install_like_text_in_body_ignored(self):
        cmd = "cat <<EOF\npip install totally-fake-pkg\nEOF"
        self.assertEqual(install_scan.package_specs(cmd), [])

    def test_url_in_body_ignored(self):
        cmd = "cat <<EOF\ncurl https://a.com/dead\nEOF"
        self.assertEqual(shell_scan.fetch_urls(cmd), [])

    def test_command_after_heredoc_still_scanned(self):
        cmd = "cat <<EOF\nbody\nEOF\necho hi > real.txt"
        self.assertEqual(shell_scan.write_targets(cmd),
                         [("real.txt", shell_scan.TRUNCATE)])

    def test_tab_indented_terminator_with_dash(self):
        cmd = "cat <<-EOF\n\tbody > noise\n\tEOF\necho hi > real.txt"
        self.assertEqual(shell_scan.write_targets(cmd),
                         [("real.txt", shell_scan.TRUNCATE)])

    def test_bit_shift_is_not_a_heredoc(self):
        # $((1<<2)) must not swallow the rest of the command as a body
        cmd = "echo $((1<<2))\npip install requests"
        self.assertEqual(install_scan.package_specs(cmd), [("pypi", "requests")])


class FetchUrlsTest(unittest.TestCase):
    def urls(self, command):
        return shell_scan.fetch_urls(command)

    def test_curl_basic(self):
        self.assertEqual(self.urls("curl https://a.com/docs"), ["https://a.com/docs"])

    def test_curl_with_flags_and_output(self):
        self.assertEqual(self.urls("curl -sL -o out.html https://a.com/x"),
                         ["https://a.com/x"])

    def test_multiple_urls(self):
        self.assertEqual(self.urls("curl https://a.com https://b.com"),
                         ["https://a.com", "https://b.com"])

    def test_wget(self):
        self.assertEqual(self.urls("wget https://a.com/f.tar.gz"),
                         ["https://a.com/f.tar.gz"])

    def test_curl_post_is_not_gated(self):
        self.assertEqual(self.urls("curl -X POST https://api.a.com/v1"), [])
        self.assertEqual(self.urls("curl -d 'x=1' https://api.a.com/v1"), [])
        self.assertEqual(self.urls("curl --data-raw '{}' https://api.a.com"), [])
        self.assertEqual(self.urls("curl -F f=@x.txt https://api.a.com"), [])

    def test_wget_post_is_not_gated(self):
        self.assertEqual(self.urls("wget --post-data 'x=1' https://a.com"), [])

    def test_non_fetch_commands_empty(self):
        self.assertEqual(self.urls("git clone https://github.com/x/y"), [])
        self.assertEqual(self.urls("echo https://a.com"), [])

    def test_segments_and_dedup(self):
        self.assertEqual(self.urls("curl https://a.com && curl https://a.com"),
                         ["https://a.com"])


if __name__ == "__main__":
    unittest.main()
