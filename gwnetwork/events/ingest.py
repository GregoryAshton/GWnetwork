"""Turn provider records into Event / EventDesignation / EventRecord rows.

Grouping is by ``common_name``. Real GWOSC data validates this: GW190521 and
GW190521_074359 are *different* events 4.7 hours apart, and GWOSC never renamed
the short form -- so common_name is a stable identity key, and a prefix
relationship between two names means nothing.

Cross-provider reconciliation (a non-GWOSC catalogue reporting the same signal
under a different name) is deliberately NOT automatic: it raises a merge
proposal for a human.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select

from ..db import session
from ..models import Event, EventDesignation, EventMergeProposal, EventRecord
from .base import EventRecordIn

# Two records this close in time from different providers are probably the same
# signal. Loose enough to catch differing PE conventions, tight enough that
# unrelated events never collide (the closest real GWOSC pair is ~1.8 h apart).
GPS_MERGE_TOLERANCE_S = 2.0


@dataclass
class IngestStats:
    events_created: int = 0
    records_created: int = 0
    records_updated: int = 0
    designations_created: int = 0
    merge_proposals: int = 0

    def __str__(self) -> str:
        return (f"events +{self.events_created}  records +{self.records_created} "
                f"(~{self.records_updated} refreshed)  designations +{self.designations_created} "
                f"  merge proposals +{self.merge_proposals}")


def ingest(records: Iterable[EventRecordIn]) -> IngestStats:
    stats = IngestStats()
    with session() as s:
        for rec in records:
            event = s.scalar(select(Event).where(Event.canonical_name == rec.common_name))
            if event is None:
                event = Event(canonical_name=rec.common_name)
                s.add(event)
                s.flush()
                stats.events_created += 1

            for d in rec.designations:
                exists = s.scalar(
                    select(EventDesignation).where(
                        EventDesignation.designation == d.designation,
                        EventDesignation.event_id == event.id,
                    )
                )
                if exists is None:
                    s.add(EventDesignation(
                        event_id=event.id, designation=d.designation, kind=d.kind,
                        source=rec.source, precedence=d.precedence, valid_from=d.valid_from,
                    ))
                    stats.designations_created += 1

            existing = s.scalar(
                select(EventRecord).where(
                    EventRecord.source == rec.source,
                    EventRecord.catalog == rec.catalog,
                    EventRecord.external_id == rec.external_id,
                )
            )
            if existing is None:
                s.add(EventRecord(
                    event_id=event.id, source=rec.source, catalog=rec.catalog,
                    external_id=rec.external_id, version=rec.version, gps=rec.gps,
                    parameters=rec.parameters, reference=rec.reference,
                ))
                stats.records_created += 1
            else:
                # Same external id re-fetched: refresh parameters, keep the row.
                existing.parameters = rec.parameters
                existing.gps = rec.gps
                stats.records_updated += 1

        s.flush()
        stats.merge_proposals = _propose_merges(s)
    return stats


def _propose_merges(s) -> int:
    """Flag events from different providers that sit at the same GPS time."""
    rows = s.execute(
        select(EventRecord.event_id, EventRecord.gps, EventRecord.source)
        .where(EventRecord.gps.is_not(None))
    ).all()

    by_event: dict = {}
    for event_id, gps, source in rows:
        by_event.setdefault(event_id, []).append((gps, source))

    items = [(eid, min(g for g, _ in v), {s_ for _, s_ in v}) for eid, v in by_event.items()]
    items.sort(key=lambda t: t[1])

    created = 0
    for i in range(len(items) - 1):
        eid_a, gps_a, src_a = items[i]
        eid_b, gps_b, src_b = items[i + 1]
        delta = abs(gps_b - gps_a)
        if delta > GPS_MERGE_TOLERANCE_S or eid_a == eid_b:
            continue
        if src_a == src_b:
            # Same provider using two names for one GPS is a provider quirk,
            # not a cross-catalogue duplicate. Still worth a human look.
            reason = f"same-provider duplicate GPS ({', '.join(sorted(src_a))})"
        else:
            reason = f"cross-provider GPS match ({', '.join(sorted(src_a | src_b))})"

        dup = s.scalar(
            select(EventMergeProposal).where(
                EventMergeProposal.event_id_a == eid_a,
                EventMergeProposal.event_id_b == eid_b,
            )
        )
        if dup is None:
            s.add(EventMergeProposal(event_id_a=eid_a, event_id_b=eid_b,
                                     reason=reason, gps_delta=delta))
            created += 1
    return created
