"""Designation matcher: find event names in paper text.

Compiled *from the database*, not hardcoded. Adding a non-GWOSC event via YAML
therefore makes the entire existing corpus searchable for it on the next
extraction run, with no code change.

Longest-match-first is mandatory, not a nicety. Real GWOSC data contains
GW190814 and GW190814_192009 as different events 1.8 hours apart (likewise
GW190521 / GW190521_074359). A greedy short-form match would silently
misattribute every mention of the long-form event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Optional

from sqlalchemy import select

from ..models import EventDesignation

# PDF text extraction reliably turns the underscore in GW200115_042309 into a
# space or a line break. Long-form names would then never match, and their bare
# prefixes would flood the unknown queue -- observed on the real GWTC-3 PDF.
# Tolerating one separator character here, rather than rewriting the stored
# text, keeps evidence offsets exact against the file on disk.
SEP = r"[ \t\u00a0_\n-]"

# An underscore flanked by digits -- the only place PDF mangling occurs, and so
# the only place separator tolerance is safe.
_DIGIT_UNDERSCORE = re.compile(r"(?<=\d)_(?=\d)")

# A GW-shaped designation we may or may not know. This is the discovery channel
# for events absent from GWOSC: unmatched hits become review-queue rows.
GENERIC_GW = re.compile(r"\bGW\d{6}(?:" + SEP + r"\d{6})?\b")


def normalise(text: str) -> str:
    """Canonical form of a matched designation: one underscore, no whitespace.

    The GW prefix is deliberately NOT required: GWOSC names marginal candidates
    without it (23 of them, e.g. `200214_224526` in GWTC-3-marginal), and
    requiring the prefix here silently routed every PDF-mangled marginal
    candidate into the unknown-designation queue.

    Applied only to text the matcher already identified as designation-shaped,
    so the loose digit pattern cannot capture unrelated table numbers.
    """
    return re.sub(r"(\d{6})" + SEP + r"(\d{6})", r"\1_\2", " ".join(text.split()))

# Bare `GW170817` should match, `GW170817-like` should too, but `xGW170817`
# should not. Designations may contain regex metacharacters, hence re.escape.
_LEFT = r"(?<![0-9A-Za-z_])"
_RIGHT = r"(?![0-9A-Za-z_])"


@dataclass(frozen=True)
class Match:
    text: str
    start: int
    end: int
    event_id: Optional[str]
    is_ambiguous: bool = False


class DesignationMatcher:
    def __init__(self, designations: dict, ambiguous: Optional[set] = None):
        """designations: {designation_string: event_id}"""
        self._map = dict(designations)
        self._ambiguous = set(ambiguous or ())
        # Longest first so GW190814_192009 wins over GW190814. Python's `|`
        # alternation is first-match, not longest-match, so ordering is the
        # entire mechanism here.
        keys = sorted(self._map, key=len, reverse=True)
        # Underscores become a tolerant separator class ONLY between digit runs,
        # which is where PDF extraction actually mangles them. Applying it to
        # word-shaped designations is a false-positive generator: GWOSC has an
        # event named `blind_injection`, and a blanket rule made it match the
        # ordinary phrase "blind injection" throughout the GW literature.
        pats = [_DIGIT_UNDERSCORE.sub(lambda _: SEP, re.escape(k)) for k in keys]
        self._re = (
            re.compile(_LEFT + "(?:" + "|".join(pats) + ")" + _RIGHT) if keys else None
        )

    @classmethod
    def from_db(cls, s) -> DesignationMatcher:
        rows = s.execute(
            select(EventDesignation.designation, EventDesignation.event_id,
                   EventDesignation.is_ambiguous)
        ).all()
        mapping, ambiguous = {}, set()
        for desig, event_id, is_amb in rows:
            if desig in mapping and mapping[desig] != event_id:
                # One string, two events: genuinely ambiguous, never guess.
                ambiguous.add(desig)
            mapping[desig] = event_id
            if is_amb:
                ambiguous.add(desig)
        return cls(mapping, ambiguous)

    def __len__(self) -> int:
        return len(self._map)

    def find(self, text: str) -> Iterator[Match]:
        """Known designations, then unknown GW-shaped strings in the gaps."""
        covered = []
        if self._re is not None:
            for m in self._re.finditer(text):
                covered.append((m.start(), m.end()))
                key = normalise(m.group(0))
                yield Match(m.group(0), m.start(), m.end(),
                            self._map.get(key), key in self._ambiguous)

        for m in GENERIC_GW.finditer(text):
            if any(s <= m.start() < e for s, e in covered):
                continue
            yield Match(m.group(0), m.start(), m.end(), None, False)
