"""Propose designation aliases from the unknown-designation queue.

Papers routinely name an event in a form GWOSC does not use: the short form
`GW230529` for `GW230529_181500`, or conversely the long form `GW190521_030229`
for the event GWOSC stores as `GW190521`. Neither matches, so hundreds of real
mentions become dead ends.

Name-shape alone is not sufficient evidence -- `GW231113` has five long-form
candidates. Where a candidate carries an HHMMSS suffix we verify it against the
event's GPS time, which is decisive. Anything that cannot be verified or
uniquely resolved is written to a review file for a human instead of guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from ..config import CONFIG
from ..db import session
from ..models import AnalysisRun, Event, EventDesignation, EventRecord, UnknownDesignation

GPS_EPOCH = datetime(1980, 1, 6)

# GPS-UTC offset. Only the entries spanning the GW era matter.
_LEAP = [(datetime(2006, 1, 1), 14), (datetime(2009, 1, 1), 15),
         (datetime(2012, 7, 1), 16), (datetime(2015, 7, 1), 17),
         (datetime(2017, 1, 1), 18)]

SHORT = re.compile(r"^GW\d{6}$")
LONG = re.compile(r"^(GW\d{6})_(\d{2})(\d{2})(\d{2})$")


def gps_to_utc(gps: float) -> datetime:
    naive = GPS_EPOCH + timedelta(seconds=gps)
    offset = 18
    for start, secs in _LEAP:
        if naive >= start:
            offset = secs
    return naive - timedelta(seconds=offset)


def hhmmss_of(gps: float) -> str:
    return gps_to_utc(gps).strftime("%H%M%S")


@dataclass
class Proposal:
    designation: str
    event_name: str
    occurrences: int
    reason: str


@dataclass
class AliasReport:
    safe: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)

    def __str__(self) -> str:
        return (f"{len(self.safe)} safe aliases, {len(self.ambiguous)} ambiguous "
                f"(need a human), {len(self.unresolved)} unresolved designations")


def propose(min_occurrences: int = 5) -> AliasReport:
    report = AliasReport()
    with session() as s:
        run = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
        if run is None:
            raise RuntimeError("no current extract run")

        unknown = s.execute(
            select(UnknownDesignation.designation, UnknownDesignation.occurrences)
            .where(UnknownDesignation.analysis_run_id == run.id,
                   UnknownDesignation.occurrences >= min_occurrences)
            .order_by(UnknownDesignation.occurrences.desc())).all()

        events = {n: i for n, i in s.execute(
            select(Event.canonical_name, Event.id)).all()}
        gps_by_event: dict = {}
        for eid, gps in s.execute(
                select(EventRecord.event_id, EventRecord.gps)
                .where(EventRecord.gps.is_not(None))).all():
            gps_by_event.setdefault(eid, gps)

        for desig, occ in unknown:
            m_long = LONG.match(desig)
            if m_long:
                report_long(report, s, desig, occ, m_long, events, gps_by_event)
            elif SHORT.match(desig):
                report_short(report, desig, occ, events, gps_by_event)
            else:
                report.unresolved.append(Proposal(desig, "", occ, "not a GW designation shape"))
    return report


def report_long(report, s, desig, occ, m, events, gps_by_event) -> None:
    """`GW190521_030229` when GWOSC stores the event as `GW190521`."""
    short = m.group(1)
    hhmmss = "".join(m.groups()[1:])
    eid = events.get(short)
    if eid is None:
        report.unresolved.append(Proposal(desig, "", occ, "no event with this date prefix"))
        return
    gps = gps_by_event.get(eid)
    if gps is None:
        report.ambiguous.append(Proposal(desig, short, occ, "no GPS to verify against"))
        return
    actual = hhmmss_of(gps)
    if actual == hhmmss:
        report.safe.append(Proposal(desig, short, occ,
                                    f"long form; GPS time {actual} matches suffix"))
    else:
        report.unresolved.append(Proposal(
            desig, "", occ, f"suffix {hhmmss} != event GPS time {actual} -- different event"))


def report_short(report, desig, occ, events, gps_by_event) -> None:
    """`GW230529` when GWOSC stores the event as `GW230529_181500`."""
    candidates = sorted(n for n in events if n.startswith(desig + "_"))
    if not candidates:
        report.unresolved.append(Proposal(desig, "", occ, "no long-form candidate; may be a new event"))
    elif len(candidates) == 1:
        report.safe.append(Proposal(desig, candidates[0], occ,
                                    "short form; exactly one long-form candidate"))
    else:
        report.ambiguous.append(Proposal(
            desig, "", occ, "candidates: " + ", ".join(candidates)))


def write_yaml(report: AliasReport, path=None) -> int:
    path = path or (CONFIG.data_dir / "events" / "aliases-auto.yaml")
    by_event: dict = {}
    for p in report.safe:
        by_event.setdefault(p.event_name, []).append(p)
    lines = [
        "# GENERATED by `gwn events propose-aliases --write`. Safe to regenerate.",
        "# Only unambiguous cases appear here: a short form with exactly one",
        "# long-form candidate, or a long form whose HHMMSS suffix matches the",
        "# event's GPS time. Ambiguous cases are in REVIEW-ambiguous.md instead.",
        "",
    ]
    for event_name, props in sorted(by_event.items()):
        lines += [f"- common_name: {event_name}",
                  "  source: alias-inference",
                  f"  external_id: {event_name}-aliases-auto",
                  "  designations:",
                  f"    - designation: {event_name}"]
        for p in props:
            lines.append(f"    - designation: {p.designation}   "
                         f"# x{p.occurrences}; {p.reason}")
        lines.append("")
    path.write_text("\n".join(lines))
    return len(by_event)


def write_review(report: AliasReport, path=None) -> None:
    path = path or (CONFIG.data_dir / "events" / "REVIEW-ambiguous.md")
    lines = ["# Designations needing a human decision", "",
             "Generated by `gwn events propose-aliases`. These are NOT applied.",
             "Resolve one by adding the designation to the correct event in a",
             "hand-written YAML file in this directory.", ""]
    if report.ambiguous:
        lines += ["## Ambiguous: more than one candidate event", "",
                  "| Designation | Mentions | Candidates |", "|---|---|---|"]
        for p in sorted(report.ambiguous, key=lambda x: -x.occurrences):
            lines.append(f"| `{p.designation}` | {p.occurrences} | {p.reason} |")
        lines.append("")
    if report.unresolved:
        lines += ["## Unresolved: no matching event (possible new events)", "",
                  "| Designation | Mentions | Note |", "|---|---|---|"]
        for p in sorted(report.unresolved, key=lambda x: -x.occurrences):
            lines.append(f"| `{p.designation}` | {p.occurrences} | {p.reason} |")
    path.write_text("\n".join(lines) + "\n")
