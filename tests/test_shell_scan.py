"""shell_scan is pure logic — test it directly, no subprocess needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
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
        self.assertEqual(shell_scan.package_specs(cmd), [])

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
        self.assertEqual(shell_scan.package_specs(cmd), [("pypi", "requests")])


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


class PackageSpecsTest(unittest.TestCase):
    def specs(self, command):
        return shell_scan.package_specs(command)

    def test_pip_install_basic(self):
        self.assertEqual(self.specs("pip install requests"), [("pypi", "requests")])

    def test_pip3_and_version_specifier(self):
        self.assertEqual(self.specs("pip3 install requests==2.31.0"), [("pypi", "requests")])

    def test_pip_extras_and_range(self):
        self.assertEqual(self.specs("pip install 'uvicorn[standard]>=0.29'"), [("pypi", "uvicorn")])

    def test_pip_requirements_file_skipped(self):
        self.assertEqual(self.specs("pip install -r requirements.txt"), [])

    def test_pip_local_and_url_skipped(self):
        self.assertEqual(self.specs("pip install ."), [])
        self.assertEqual(self.specs("pip install git+https://github.com/x/y"), [])
        self.assertEqual(self.specs("pip install ./pkg"), [])

    def test_python_dash_m_pip(self):
        self.assertEqual(self.specs("python3 -m pip install flask"), [("pypi", "flask")])

    def test_uv_add_and_uv_pip(self):
        self.assertEqual(self.specs("uv add httpx"), [("pypi", "httpx")])
        self.assertEqual(self.specs("uv pip install httpx"), [("pypi", "httpx")])

    def test_npm_install_with_version(self):
        self.assertEqual(self.specs("npm install lodash@4.17.21"), [("npm", "lodash")])

    def test_npm_scoped_package(self):
        self.assertEqual(self.specs("npm i @types/node@20"), [("npm", "@types/node")])

    def test_npm_flags_skipped(self):
        self.assertEqual(self.specs("npm install --save-dev typescript"), [("npm", "typescript")])

    def test_npm_bare_install_is_empty(self):
        self.assertEqual(self.specs("npm install"), [])

    def test_yarn_and_pnpm(self):
        self.assertEqual(self.specs("yarn add react"), [("npm", "react")])
        self.assertEqual(self.specs("pnpm add vue"), [("npm", "vue")])

    def test_cargo_add_and_install(self):
        self.assertEqual(self.specs("cargo add serde@1"), [("crates", "serde")])
        self.assertEqual(self.specs("cargo install ripgrep"), [("crates", "ripgrep")])

    def test_poetry_add_maps_to_pypi(self):
        self.assertEqual(self.specs("poetry add httpx"), [("pypi", "httpx")])

    def test_poetry_add_strips_at_constraint(self):
        # poetry uses name@constraint as well as name>=x
        self.assertEqual(self.specs("poetry add 'pendulum@^2.0.5'"), [("pypi", "pendulum")])

    def test_bun_add_maps_to_npm(self):
        self.assertEqual(self.specs("bun add react"), [("npm", "react")])
        self.assertEqual(self.specs("bun add lodash@4.17.21"), [("npm", "lodash")])
        self.assertEqual(self.specs("bun add @types/node"), [("npm", "@types/node")])

    def test_gem_install_maps_to_rubygems(self):
        self.assertEqual(self.specs("gem install rails"), [("rubygems", "rails")])

    def test_gem_install_version_flag_not_treated_as_name(self):
        # `-v 7.0`: the 7.0 is a version, not a gem
        self.assertEqual(self.specs("gem install rails -v 7.0"), [("rubygems", "rails")])

    def test_gem_install_multiple(self):
        self.assertEqual(self.specs("gem install rails rake"),
                         [("rubygems", "rails"), ("rubygems", "rake")])

    def test_bundle_add_maps_to_rubygems(self):
        self.assertEqual(self.specs("bundle add rails"), [("rubygems", "rails")])
        self.assertEqual(self.specs("bundle add rails --version '~> 7.0'"),
                         [("rubygems", "rails")])

    def test_composer_require_maps_to_packagist(self):
        self.assertEqual(self.specs("composer require monolog/monolog"),
                         [("packagist", "monolog/monolog")])

    def test_composer_require_strips_version_and_dev_flag(self):
        self.assertEqual(self.specs("composer require --dev 'phpunit/phpunit:^10.0'"),
                         [("packagist", "phpunit/phpunit")])

    def test_composer_php_extension_without_vendor_skipped(self):
        # `ext-gd` is a PHP extension, not a Packagist package
        self.assertEqual(self.specs("composer require ext-gd"), [])

    # A custom index/registry means the package lives on a registry grounded
    # cannot query — checking it against the public one would falsely STOP a
    # legitimate private install. Skip G-2 for the whole segment (fail open).
    def test_pip_index_url_skips_check(self):
        self.assertEqual(
            self.specs("pip install --index-url https://pypi.internal/simple acme-lib"), [])

    def test_pip_short_index_flag_skips_check(self):
        self.assertEqual(self.specs("pip install -i https://pypi.internal/simple acme-lib"), [])

    def test_pip_extra_index_url_skips_check(self):
        self.assertEqual(
            self.specs("pip install --extra-index-url https://pypi.internal acme-lib"), [])

    def test_uv_pip_index_url_skips_check(self):
        self.assertEqual(
            self.specs("uv pip install --index-url https://pypi.internal acme-lib"), [])

    def test_npm_custom_registry_skips_check(self):
        self.assertEqual(
            self.specs("npm install --registry=https://npm.internal @acme/lib"), [])

    def test_yarn_custom_registry_skips_check(self):
        self.assertEqual(
            self.specs("yarn add --registry https://npm.internal acme-lib"), [])

    def test_gem_custom_source_skips_check(self):
        self.assertEqual(
            self.specs("gem install acme-lib --source https://gems.internal"), [])

    def test_cargo_custom_registry_skips_check(self):
        self.assertEqual(self.specs("cargo add acme-lib --registry internal"), [])

    def test_public_install_still_checked(self):
        # the guard must not suppress ordinary public-registry installs
        self.assertEqual(self.specs("pip install requests"), [("pypi", "requests")])

    def test_sudo_and_env_prefix(self):
        self.assertEqual(self.specs("sudo pip install requests"), [("pypi", "requests")])
        self.assertEqual(self.specs("PIP_NO_CACHE=1 pip install requests"), [("pypi", "requests")])

    def test_multiple_packages_and_segments(self):
        self.assertEqual(
            self.specs("pip install flask requests && npm install lodash"),
            [("pypi", "flask"), ("pypi", "requests"), ("npm", "lodash")],
        )

    def test_dedup(self):
        self.assertEqual(self.specs("pip install x; pip install x"), [("pypi", "x")])

    def test_non_install_commands_empty(self):
        self.assertEqual(self.specs("pip freeze && npm run build && cargo test"), [])


if __name__ == "__main__":
    unittest.main()
