"""Cheap section attribution and sentence extraction.

Not a LaTeX parser -- a heuristic good enough to answer "was this mention in the
introduction or in the methods?", which is the single most useful feature for
the rule-based engagement classifier.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from typing import Optional

_SECTION_PATTERNS = [
    ("abstract",     r"abstract"),
    ("introduction", r"introduction|motivation"),
    ("data",         r"data|observations?|dataset|event selection|sample"),
    ("methods",      r"methods?|analysis|formalism|framework|model(?:ling|ing)?|"
                     r"waveform|inference|bayesian|simulation"),
    ("results",      r"results?|constraints?|measurements?|posterior"),
    ("discussion",   r"discussion|implications?|interpretation"),
    ("conclusion",   r"conclusions?|summary|outlook"),
    ("acknowledgements", r"acknowledge?ments?|funding"),
    ("references",   r"references|bibliography"),
    ("appendix",     r"appendix|supplement"),
]
_COMPILED = [(name, re.compile(pat, re.I)) for name, pat in _SECTION_PATTERNS]

# A heading line: optional numbering, then a short title, on its own line.
_HEADING = re.compile(
    r"^[ \t]*(?:\\?(?:sub)*section\*?\{)?"
    r"(?:(?:[IVXLC]+|\d+(?:\.\d+)*)[.)]?\s+)?"
    r"([A-Z][^\n{}]{2,60}?)\s*\}?[ \t]*$",
    re.M,
)


def _classify_heading(title: str) -> Optional[str]:
    t = title.strip().strip(".:").lower()
    if len(t) > 60:
        return None
    for name, pat in _COMPILED:
        if pat.fullmatch(t) or pat.match(t):
            return name
    return None


class SectionIndex:
    """Maps a character offset to a coarse section label."""

    def __init__(self, text: str):
        self._starts: list = [0]
        self._labels: list = ["front_matter"]
        for m in _HEADING.finditer(text):
            label = _classify_heading(m.group(1))
            if label is not None:
                self._starts.append(m.start())
                self._labels.append(label)

    def label_at(self, offset: int) -> str:
        i = bisect_right(self._starts, offset) - 1
        return self._labels[max(i, 0)]


# Blank lines end a "sentence" too: headings and display equations carry no
# terminal punctuation, and without this a mention inherits the heading text.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\\])|\n[ \t]*\n\s*")


def sentence_around(text: str, start: int, end: int, window: int = 400) -> str:
    """The sentence containing [start, end), falling back to a character window.

    Separator spans are tracked exactly rather than assumed to be one character
    wide -- paragraph breaks are multi-character, and guessing here would drift
    the offsets and return a neighbouring sentence.
    """
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    chunk = text[lo:hi]
    rel = start - lo

    bounds, cursor = [], 0
    for sep in _SENT_SPLIT.finditer(chunk):
        if sep.start() > cursor:
            bounds.append((cursor, sep.start()))
        cursor = sep.end()
    if cursor < len(chunk):
        bounds.append((cursor, len(chunk)))

    for a, b in bounds:
        if a <= rel < b:
            return " ".join(chunk[a:b].split())
    return " ".join(chunk.split())
