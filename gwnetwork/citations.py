"""Find where a reference-list entry is cited in the body of a paper.

A designation matched inside the bibliography is usually the title of a cited
paper ("GW150914: Implications for the stochastic background..."), so the
mention itself says nothing about how the citing paper uses the event. What
does is the sentence that cites that reference. This maps a reference-list
offset back to those sentences.

Two bibliography shapes cover nearly all of the arXiv HTML corpus:

- numbered: the entry starts on its own line with `[84]`, `(84)` or `84.`, and
  the body cites it as `[84]`, `[12, 84]` or `[80–86]`;
- author-year: arXiv's LaTeXML puts a label line `Abbott et al. (2016a)` above
  each entry, and the body cites `Abbott et al. (2016a)` or `(Abbott et al. 2016a)`.

Heuristic by nature: on a sample of 300 reference mentions it located the
citation for ~90% of genuine bibliography hits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

LOOKBACK = 3000

_NUM_LABEL = re.compile(
    r"\n[ \t]*(?:\[(\d{1,4})\]|\((\d{1,4})\)|(\d{1,4})\.[ \t]*\n)")
_AY_LABEL = re.compile(r"\n[ \t]*([^\n]{2,120}?)\s*\((\d{4}[a-z]?)\)[ \t]*\n")
_BRACKET = re.compile(r"\[([0-9,;\s–—-]{1,80})\]")
_RANGE = re.compile(r"^(\d+)\s*[–—-]\s*(\d+)$")


@dataclass
class Citation:
    key: str                       # "[84]" or "Abbott 2016a", for display
    entry_start: int               # where the reference entry begins
    spans: list = field(default_factory=list)   # (start, end) of in-text cites


def _last(pattern: re.Pattern, text: str, pos: int):
    m = None
    for m in pattern.finditer(text, max(0, pos - LOOKBACK), pos):
        pass
    return m


def _numbers(inner: str) -> set:
    out: set = set()
    for part in re.split(r"[,;]", inner):
        part = part.strip()
        r = _RANGE.match(part)
        if r:
            a, b = int(r[1]), int(r[2])
            if 0 < b - a < 200:
                out.update(range(a, b + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def find_citations(text: str, pos: int) -> Optional[Citation]:
    """In-text citations of the reference entry containing offset `pos`.

    Returns None when no entry label precedes `pos` -- typically because the
    offset is not really inside a bibliography. Only text before the entry is
    searched, so the bibliography's own later entries never match.
    """
    m = _last(_NUM_LABEL, text, pos)
    if m:
        n = int(next(g for g in m.groups() if g))
        spans = [(b.start(), b.end()) for b in _BRACKET.finditer(text, 0, m.start())
                 if n in _numbers(b[1])]
        return Citation(f"[{n}]", m.start(), spans)

    m = _last(_AY_LABEL, text, pos)
    if m:
        lead = re.split(r"\s+et\s+al\.?|\s+and\s+|\s*&\s*|,", m[1])[0].split()
        if not lead or len(lead[-1]) < 2:
            return None
        surname, year = lead[-1], m[2]
        cite = re.compile(re.escape(surname) + r"[^\n()\[\]]{0,60}?[\s(,]"
                          + re.escape(year) + r"(?![0-9a-z])")
        spans = [(c.start(), c.end()) for c in cite.finditer(text, 0, m.start())]
        return Citation(f"{surname} {year}", m.start(), spans)
    return None


_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])|\n[ \t]*\n")


def sentence_at(text: str, start: int, end: int, window: int = 400) -> str:
    """The sentence containing [start, end), whitespace-normalised.

    Deliberately local rather than shared with extract/: layers do not import
    each other's logic.
    """
    lo, hi = max(0, start - window), min(len(text), end + window)
    a, b = lo, hi
    for m in _SENT_END.finditer(text, lo, start):
        a = m.end()
    m = _SENT_END.search(text, end, hi)
    if m:
        b = m.start()
    return " ".join(text[a:b].split())


def citing_sentences(text: str, pos: int, limit: int = 2) -> tuple:
    """(reference key, [sentences citing the reference that contains `pos`])."""
    cite = find_citations(text, pos)
    if cite is None:
        return None, []
    out = []
    for a, b in cite.spans:
        s = sentence_at(text, a, b)
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return cite.key, out
