"""Detecting instruments per event, from the GWTC results papers' tables.

The catalogue papers are the authoritative source for which detectors took
part in each detection (e.g. Table 1 of GWTC-4.0, 2508.18082). GWOSC's API is
not: its summary field lists H1 for GW190425, when Hanford was off. Each
table row starts with the candidate name and its instruments
("GW230529_181500 L ..."), so the stored full text of those papers parses
deterministically. PDF text splits "HLV" as "HL V" and can drop the space
after the name ("GW240407_040938HLV"); both are tolerated, since names have
a fixed length.

GWTC-1 has no instrument column. For O1/O2, the GWOSC strain-file detectors
(EventRecord.strain_detectors) match the known facts and stand in.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from ..db import session
from ..models import EventDesignation, EventRecord, Paper

# Results paper -> the GWOSC catalogues its table describes.
SOURCES = {
    "2010.14527": ("GWTC-2",),
    "2108.01045": ("GWTC-2.1-confident", "GWTC-2.1-marginal", "GWTC-2.1-auxiliary"),
    "2111.03606": ("GWTC-3-confident", "GWTC-3-marginal"),
    "2508.18082": ("GWTC-4.0",),
    "2605.27225": ("GWTC-5.0",),
}

_ROW = re.compile(
    r"^((?:GW)?\d{6}(?:[_ ]\d{6})?(?:\.\d)?)\*?\s*([HLVK]{1,4}(?: [HLVK]{1,3})?)(?=\s)",
    re.M)


def parse_table_rows(text: str) -> dict:
    """{candidate name: instruments} from table rows; conflicting rows raise."""
    found = defaultdict(set)
    for m in _ROW.finditer(text):
        found[m.group(1).replace(" ", "_")].add(m.group(2).replace(" ", ""))
    conflicts = {k: v for k, v in found.items() if len(v) > 1}
    if conflicts:
        raise ValueError(f"conflicting instrument rows: {conflicts}")
    return {k: v.pop() for k, v in found.items()}


def store_instruments() -> dict:
    """Set EventRecord.instruments for each catalogue a results paper covers.

    Names resolve through event designations, since tables use short names
    (GWTC-2.1 writes GW190425 for GW190425_081805) and marginal candidates
    lack the GW prefix. A later paper may re-list earlier events (GWTC-5.0's
    table includes O4a); those rows must agree with the earlier paper.
    """
    stats = {"papers": 0, "rows": 0, "records": 0, "events": 0,
             "rows_unresolved": [], "relisted": 0, "disagreements": []}
    with session() as s:
        desig = dict(s.execute(select(EventDesignation.designation,
                                      EventDesignation.event_id)).all())
        per_event: dict = defaultdict(dict)
        for arxiv_id, catalogs in SOURCES.items():
            paper = s.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
            if paper is None or not paper.fulltext_path:
                raise RuntimeError(f"{arxiv_id}: results paper has no stored full text")
            rows = parse_table_rows(Path(paper.fulltext_path).read_text(errors="replace"))
            stats["papers"] += 1
            stats["rows"] += len(rows)
            source = f"arXiv:{arxiv_id}v{paper.fulltext_version}"
            for name, inst in rows.items():
                event_id = desig.get(name) or desig.get(name.removeprefix("GW"))
                if event_id is None:
                    stats["rows_unresolved"].append(f"{arxiv_id}:{name}")
                    continue
                per_event[event_id][arxiv_id] = inst
                recs = s.scalars(select(EventRecord).where(
                    EventRecord.event_id == event_id, EventRecord.source == "gwosc",
                    EventRecord.catalog.in_(catalogs))).all()
                if not recs:
                    stats["relisted"] += 1
                for r in recs:
                    r.instruments = inst
                    r.instruments_source = source
                    stats["records"] += 1
        stats["events"] = len(per_event)
        for event_id, by_paper in per_event.items():
            if len(set(by_paper.values())) > 1:
                stats["disagreements"].append(f"{event_id}: {by_paper}")
    return stats
