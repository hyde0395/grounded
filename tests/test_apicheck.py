"""apicheck validates `from X import Y` against the installed module WITHOUT
importing it (no execution). Tested against real stdlib modules."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import apicheck  # noqa: E402
import verdict  # noqa: E402


class GateApiSymbolTest(unittest.TestCase):
    def test_absent_warns(self):
        v = verdict.gate_api_symbol("json", "dumpz", False)
        self.assertEqual(v.decision, verdict.WARN)
        self.assertIn("dumpz", v.reason)

    def test_present_passes(self):
        self.assertEqual(
            verdict.gate_api_symbol("json", "dumps", True).decision, verdict.PASS)

    def test_unverifiable_passes(self):
        self.assertEqual(
            verdict.gate_api_symbol("math", "x", None).decision, verdict.PASS)


class FromImportSymbolsTest(unittest.TestCase):
    def syms(self, src):
        return apicheck.from_import_symbols(src)

    def test_basic_from_import(self):
        self.assertEqual(self.syms("from json import dumps, loads"),
                         [("json", "dumps"), ("json", "loads")])

    def test_plain_import_ignored(self):
        self.assertEqual(self.syms("import os\nimport sys as s"), [])

    def test_relative_import_ignored(self):
        self.assertEqual(self.syms("from . import x\nfrom .util import y"), [])

    def test_star_import_ignored(self):
        self.assertEqual(self.syms("from json import *"), [])

    def test_syntax_error_returns_empty(self):
        self.assertEqual(self.syms("def ( this is not python"), [])


class ValidateTest(unittest.TestCase):
    def test_real_stdlib_symbol_present(self):
        self.assertIs(apicheck.validate("json", "dumps"), True)

    def test_absent_stdlib_symbol(self):
        self.assertIs(apicheck.validate("json", "dumpz"), False)

    def test_c_extension_module_is_unknown(self):
        # math is a C extension (no .py source) — we cannot see its names
        self.assertIsNone(apicheck.validate("math", "sqrt"))
        self.assertIsNone(apicheck.validate("math", "definitely_not_there"))

    def test_dotted_module_skipped(self):
        # find_spec on a dotted name would import the parent package — skip it
        self.assertIsNone(apicheck.validate("os.path", "join"))

    def test_uninstalled_module_is_unknown(self):
        self.assertIsNone(apicheck.validate("totally_not_installed_xyz", "x"))


if __name__ == "__main__":
    unittest.main()
