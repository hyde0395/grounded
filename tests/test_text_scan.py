"""text_scan is pure logic — test it directly, no network needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import text_scan  # noqa: E402


class AnswerUrlsTest(unittest.TestCase):
    def urls(self, text):
        return text_scan.answer_urls(text)

    def test_plain_url(self):
        self.assertEqual(self.urls("see https://example.com/docs for more"),
                         ["https://example.com/docs"])

    def test_http_and_https(self):
        self.assertEqual(
            self.urls("a http://a.test b https://b.test"),
            ["http://a.test", "https://b.test"])

    def test_order_preserving_dedup(self):
        text = "https://a.test then https://b.test then https://a.test again"
        self.assertEqual(self.urls(text), ["https://a.test", "https://b.test"])

    def test_no_urls(self):
        self.assertEqual(self.urls("nothing to see here"), [])

    def test_non_http_scheme_ignored(self):
        self.assertEqual(self.urls("mailto:x@y.com ftp://h/f file:///etc"), [])

    # --- masking: code is illustrative, never gated (false-block control) ---
    def test_fenced_code_block_excluded(self):
        text = "real https://live.test\n```\ncurl https://in-fence.test\n```\n"
        self.assertEqual(self.urls(text), ["https://live.test"])

    def test_fenced_with_language_hint_excluded(self):
        text = "```python\nurl = 'https://in-fence.test'\n```"
        self.assertEqual(self.urls(text), [])

    def test_inline_code_excluded(self):
        text = "use `https://in-code.test` but cite https://live.test"
        self.assertEqual(self.urls(text), ["https://live.test"])

    # --- markdown wrappers: extract the URL, drop the syntax ---
    def test_markdown_link(self):
        self.assertEqual(self.urls("[docs](https://example.com/page) here"),
                         ["https://example.com/page"])

    def test_autolink_angle_brackets(self):
        self.assertEqual(self.urls("<https://example.com/x>"),
                         ["https://example.com/x"])

    def test_paren_wrapped(self):
        self.assertEqual(self.urls("(https://example.com/y)"),
                         ["https://example.com/y"])

    def test_trailing_sentence_punctuation_stripped(self):
        self.assertEqual(self.urls("go to https://example.com/z."),
                         ["https://example.com/z"])
        self.assertEqual(self.urls("https://example.com/q, and more"),
                         ["https://example.com/q"])

    def test_quoted_url(self):
        self.assertEqual(self.urls('see "https://example.com/d"'),
                         ["https://example.com/d"])


if __name__ == "__main__":
    unittest.main()
