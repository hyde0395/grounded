"""Pure extraction of URLs from assistant prose — no I/O, no LLM.

Feeds the G-4 speech gate: the Stop hook scans the final answer text for
links it asserts, so a dead one can be caught before the user acts on it.

Parsing is deliberately conservative (spec §05 'a false block costs more
than a miss'): code is treated as illustrative and masked out, so a URL
shown inside a fenced block or inline code is never gated. Markdown
wrappers are stripped to the bare URL.
"""
import re

# A URL runs until whitespace or a delimiter that brackets/quotes it in
# prose or markdown ( ) [ ] < > " ' ` — those are never part of the link.
_URL = re.compile(r"https?://[^\s<>)\]\"'`]+", re.IGNORECASE)

# Trailing sentence punctuation clings to a URL in prose ("see https://x.").
_TRAILING = ".,;:!?"

_FENCED = re.compile(r"(```|~~~).*?\1", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]*`")


def _mask(pattern, text):
    """Blank out matched spans (same length) so URLs inside are inert."""
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def answer_urls(text):
    """[url] this answer text asserts, order-preserving dedup.

    Code spans (fenced and inline) are masked first so illustrative URLs do
    not trigger the gate. Returned URLs are raw (not normalized) — the caller
    applies liveness checking and host filtering.
    """
    if not text:
        return []
    masked = _mask(_INLINE, _mask(_FENCED, text))
    seen, result = set(), []
    for raw in _URL.findall(masked):
        url = raw.rstrip(_TRAILING)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result
