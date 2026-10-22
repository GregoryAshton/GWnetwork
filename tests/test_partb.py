"""Raw facts for the attribution and citation studies (DESIGN.md §13)."""
from __future__ import annotations

from sqlalchemy import select


def test_store_detectors_keeps_both_gwosc_fields(temp_db):
    from gwnetwork.events.ingest import store_detectors
    from gwnetwork.models import Event, EventRecord
    with temp_db.session() as s:
        e = Event(canonical_name="GW190425")
        s.add(e)
        s.flush()
        s.add_all([EventRecord(event_id=e.id, source="gwosc", external_id="GW190425-v3"),
                   EventRecord(event_id=e.id, source="manual", external_id="GW190425-v3")])
    stats = store_detectors({
        "GW190425-v3": {"detectors": ["H1", "L1", "V1"], "strain_detectors": ["L1", "V1"],
                        "data_doi": "https://doi.org/10.7935/qf3a-3z67"},
        "GW999999-v1": {"detectors": [], "strain_detectors": [], "data_doi": None}})
    assert stats["updated"] == 1 and stats["unmatched_gwosc"] == 1
    assert stats["summary_differs"] == [
        "GW190425-v3: summary ['H1', 'L1', 'V1'], strain ['L1', 'V1']"]
    with temp_db.session() as s:
        recs = {r.source: r for r in s.scalars(select(EventRecord))}
    assert recs["gwosc"].strain_detectors == ["L1", "V1"]
    assert recs["manual"].detectors is None          # only GWOSC records are touched


def test_instrument_rows_parse_including_split_codes():
    from gwnetwork.events.instruments import parse_table_rows
    text = ("GW230529_181500 L ⋯ ⋯ ⋯ 0.0058 11.8 0.85\n"
            "GW200129 065458 HL V – – – < 1.0× 10−5 26.5\n"
            "GW230630_070659* HL ⋯ ⋯ ⋯ 0.47 9.8 0.88\n"
            "GW240407_040938HLV – – – 18 7.8 0.36\n"
            "GW190814 is discussed in the text, LV not a row\n")
    assert parse_table_rows(text) == {"GW230529_181500": "L", "GW200129_065458": "HLV",
                                      "GW230630_070659": "HL", "GW240407_040938": "HLV"}


def test_conflicting_instrument_rows_raise():
    import pytest
    from gwnetwork.events.instruments import parse_table_rows
    with pytest.raises(ValueError, match="conflicting"):
        parse_table_rows("GW190814 LV 1 2\nGW190814 HLV 1 2\n")


def test_event_instruments_prefer_table_then_strain_then_summary():
    from types import SimpleNamespace as R
    from gwnetwork.events.view import event_instruments
    table = R(instruments="LV", strain_detectors=["H1", "L1", "V1"], detectors=["H1", "L1", "V1"])
    assert event_instruments([table]) == ("LV", "table")
    # GW150914's GWTC-2.1 version has no strain files; an earlier one does.
    o1 = [R(instruments=None, strain_detectors=[], detectors=["H1", "L1"]),
          R(instruments=None, strain_detectors=["L1", "H1"], detectors=["H1", "L1"])]
    assert event_instruments(o1) == ("HL", "strain")
    gw170817 = [R(instruments=None, strain_detectors=["G1", "H1", "L1", "V1"], detectors=[])]
    assert event_instruments(gw170817) == ("HLV", "strain")
    o4 = [R(instruments=None, strain_detectors=[], detectors=["L1", "H1"])]
    assert event_instruments(o4) == ("HL", "summary")
    assert event_instruments([R(instruments=None, strain_detectors=None, detectors=None)]) == (None, None)
