from __future__ import annotations

from sqlalchemy import select

from gwnetwork.events.base import DesignationIn, EventRecordIn
from gwnetwork.events.ingest import ingest
from gwnetwork.models import Event, EventDesignation, EventMergeProposal, EventRecord


def rec(name, source="gwosc", catalog="GWTC-1", version=1, gps=1000.0, ext=None, desigs=None):
    return EventRecordIn(
        common_name=name, source=source, catalog=catalog, version=version, gps=gps,
        external_id=ext or f"{name}-v{version}",
        designations=desigs or [DesignationIn(name)],
    )


def test_versions_of_one_event_share_an_event_row(temp_db):
    stats = ingest([
        rec("GW150914", catalog="GWTC-1", version=1),
        rec("GW150914", catalog="GWTC-2.1", version=4, ext="GW150914-v4"),
    ])
    assert stats.events_created == 1
    assert stats.records_created == 2
    with temp_db.session() as s:
        assert s.scalar(select(Event.canonical_name)) == "GW150914"
        assert len(s.scalars(select(EventRecord)).all()) == 2


def test_prefix_related_names_stay_separate(temp_db):
    """GW190521 and GW190521_074359 are different events 4.7 hours apart."""
    ingest([rec("GW190521", gps=1242442967.4),
            rec("GW190521_074359", gps=1242459857.5)])
    with temp_db.session() as s:
        names = sorted(n for (n,) in s.execute(select(Event.canonical_name)).all())
    assert names == ["GW190521", "GW190521_074359"]


def test_ingest_is_idempotent(temp_db):
    records = [rec("GW150914")]
    ingest(records)
    again = ingest([rec("GW150914")])
    assert again.events_created == 0
    assert again.records_created == 0
    assert again.records_updated == 1


def test_manual_aliases_attach_to_existing_event(temp_db):
    ingest([rec("GW170817")])
    ingest([rec("GW170817", source="manual", catalog=None, version=None,
                ext="GW170817-aliases",
                desigs=[DesignationIn("GW170817"),
                        DesignationIn("AT2017gfo", kind="em_counterpart")])])
    with temp_db.session() as s:
        assert s.scalar(select(Event.canonical_name)) == "GW170817"
        desigs = sorted(d for (d,) in s.execute(select(EventDesignation.designation)).all())
    assert desigs == ["AT2017gfo", "GW170817"]


def test_cross_provider_gps_match_proposes_a_merge_but_does_not_apply_it(temp_db):
    ingest([rec("GW151216", source="gwosc", gps=1134293073.0),
            rec("SomeOtherName", source="ias", catalog="IAS-O1",
                gps=1134293073.4, ext="other-1")])
    with temp_db.session() as s:
        proposals = s.scalars(select(EventMergeProposal)).all()
        events = s.scalars(select(Event)).all()
    assert len(proposals) == 1
    assert proposals[0].status == "proposed"
    # Crucially: still two events. Nothing merged automatically.
    assert len(events) == 2
