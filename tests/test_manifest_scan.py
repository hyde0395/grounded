"""manifest_scan is pure logic — test it directly, no I/O for parsing."""
import json as _json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import manifest_scan  # noqa: E402


class NpmTest(unittest.TestCase):
    def deps(self, content):
        return manifest_scan.deps("npm", content)

    def test_dependencies_and_dev(self):
        c = '{"dependencies":{"react":"^18"},"devDependencies":{"jest":"^29"}}'
        self.assertEqual(self.deps(c), ["react", "jest"])

    def test_scoped_name_kept(self):
        self.assertEqual(self.deps('{"dependencies":{"@types/node":"^20"}}'),
                         ["@types/node"])

    def test_non_registry_specs_skipped(self):
        c = ('{"dependencies":{"a":"file:../a","b":"git+https://x/b.git",'
             '"c":"workspace:*","d":"github:o/d","ok":"^1"}}')
        self.assertEqual(self.deps(c), ["ok"])

    def test_corrupt_json_returns_empty(self):
        self.assertEqual(self.deps("{not json"), [])

    def test_unknown_ecosystem_returns_empty(self):
        self.assertEqual(manifest_scan.deps("maven", '{"x":1}'), [])


class PypiRequirementsTest(unittest.TestCase):
    def deps(self, content):
        return manifest_scan.deps("pypi", content)

    def test_basic_names_and_versions(self):
        self.assertEqual(self.deps("requests==2.31\nflask>=3\n"),
                         ["requests", "flask"])

    def test_extras_and_markers_stripped(self):
        self.assertEqual(self.deps("uvicorn[standard]>=0.2 ; python_version>'3'"),
                         ["uvicorn"])

    def test_comments_and_blank_lines(self):
        self.assertEqual(self.deps("# a comment\n\nrequests\n"), ["requests"])

    def test_options_and_vcs_skipped(self):
        c = "-r base.txt\n-e .\ngit+https://x/y.git\n./local\nrequests\n"
        self.assertEqual(self.deps(c), ["requests"])

    def test_custom_index_skips_whole_file(self):
        self.assertEqual(self.deps("--index-url https://pri/\nsecretpkg\n"), [])


class PyprojectTest(unittest.TestCase):
    def deps(self, content):
        return manifest_scan.deps("pypi", content)

    def test_pep621_dependencies(self):
        c = '[project]\ndependencies = ["requests>=2", "flask"]\n'
        self.assertEqual(set(self.deps(c)), {"requests", "flask"})

    def test_poetry_table_skips_python(self):
        c = ('[tool.poetry.dependencies]\npython = "^3.11"\n'
             'requests = "^2.31"\n')
        self.assertEqual(self.deps(c), ["requests"])

    def test_poetry_custom_source_skips_file(self):
        c = ('[[tool.poetry.source]]\nname = "pri"\nurl = "https://pri/"\n'
             '[tool.poetry.dependencies]\nsecret = "^1"\n')
        self.assertEqual(self.deps(c), [])


class CargoTest(unittest.TestCase):
    def deps(self, c):
        return manifest_scan.deps("crates", c)

    def test_dependencies_table(self):
        c = '[dependencies]\nserde = "1.0"\ntokio = { version = "1" }\n'
        self.assertEqual(set(self.deps(c)), {"serde", "tokio"})

    def test_path_and_git_deps_skipped(self):
        c = '[dependencies]\nlocal = { path = "../local" }\nserde = "1"\n'
        self.assertEqual(self.deps(c), ["serde"])


class GemfileTest(unittest.TestCase):
    def deps(self, c):
        return manifest_scan.deps("rubygems", c)

    def test_gem_lines(self):
        c = "source 'https://rubygems.org'\ngem 'rails', '~> 7'\ngem \"pg\"\n"
        self.assertEqual(self.deps(c), ["rails", "pg"])

    def test_local_and_git_gems_skipped(self):
        c = "gem 'a', path: '../a'\ngem 'b', git: 'https://x'\ngem 'pg'\n"
        self.assertEqual(self.deps(c), ["pg"])

    def test_custom_source_skips_file(self):
        c = "source 'https://gems.corp.internal'\ngem 'secret'\n"
        self.assertEqual(self.deps(c), [])


class ComposerTest(unittest.TestCase):
    def deps(self, c):
        return manifest_scan.deps("packagist", c)

    def test_require_vendor_names(self):
        c = '{"require":{"monolog/monolog":"^3","php":">=8"}}'
        self.assertEqual(self.deps(c), ["monolog/monolog"])

    def test_platform_and_ext_skipped(self):
        c = '{"require":{"ext-gd":"*","lib-curl":"*","a/b":"^1"}}'
        self.assertEqual(self.deps(c), ["a/b"])

    def test_repositories_skips_file(self):
        c = '{"repositories":[{"type":"vcs"}],"require":{"a/b":"^1"}}'
        self.assertEqual(self.deps(c), [])


class GroundedNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        with open(os.path.join(self.dir, name), "w") as f:
            f.write(content)

    def test_package_lock_v3_names(self):
        self.write("package-lock.json", _json.dumps({"packages": {
            "": {}, "node_modules/react": {}, "node_modules/@types/node": {}}}))
        self.assertEqual(manifest_scan.grounded_names(self.dir, "npm"),
                         {"react", "@types/node"})

    def test_node_modules_dir(self):
        os.makedirs(os.path.join(self.dir, "node_modules", "lodash"))
        self.assertIn("lodash", manifest_scan.grounded_names(self.dir, "npm"))

    def test_composer_lock_names(self):
        self.write("composer.lock", _json.dumps(
            {"packages": [{"name": "monolog/monolog"}]}))
        self.assertEqual(manifest_scan.grounded_names(self.dir, "packagist"),
                         {"monolog/monolog"})

    def test_missing_lockfile_empty_set(self):
        self.assertEqual(manifest_scan.grounded_names(self.dir, "crates"), set())


if __name__ == "__main__":
    unittest.main()
