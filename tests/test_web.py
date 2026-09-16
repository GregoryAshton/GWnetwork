from __future__ import annotations

import re

from fastapi.testclient import TestClient

from gwnetwork.models import AnalysisRun, Event, EventDesignation, Mention, Paper


def _seed(db, n_runs: int):
    """One event, one paper, mentioned once per extract run."""
    with db.session() as s:
        ev = Event(canonical_name="GW150914")
        paper = Paper(arxiv_id="1602.03837", title="Observation of GWs", abstract="x")
        s.add_all([ev, paper])
        s.flush()
        s.add(EventDesignation(event_id=ev.id, designation="GW150914"))
        for i in range(n_runs):
            run = AnalysisRun(stage="extract", pipeline_version=f"v{i}",
                              is_current=(i == n_runs - 1))
            s.add(run)
            s.flush()
            s.add(Mention(paper_id=paper.id, event_id=ev.id, analysis_run_id=run.id,
                          matched_text="GW150914", char_start=0, char_end=8,
                          section="abstract", sentence="We report GW150914."))
        return ev.id, paper.id


def test_counts_use_only_the_current_run(temp_db):
    """Superseded runs must not inflate counts.

    Analytical tables are append-only, so five extract runs meant every mention
    count on the site was five times too large.
    """
    _seed(temp_db, n_runs=5)
    from gwnetwork.web.app import app
    c = TestClient(app)

    body = c.get("/event/GW150914").text
    assert ">1</td>" in body, "mention count should be 1, not 5"
    assert ">5</td>" not in body

    papers = c.get("/papers").text
    assert papers.count("Observation of GWs") == 1


def test_pages_render_with_no_runs_at_all(temp_db):
    with temp_db.session() as s:
        s.add(Event(canonical_name="GW170817"))
    from gwnetwork.web.app import app
    c = TestClient(app)
    for path in ["/", "/events", "/papers", "/event/GW170817"]:
        assert c.get(path).status_code == 200, path


def test_unknown_event_does_not_500(temp_db):
    from gwnetwork.web.app import app
    r = TestClient(app).get("/event/GW999999")
    assert r.status_code == 200
    assert "No event named" in r.text


def _seed_view(db):
    from gwnetwork.events.base import DesignationIn, EventRecordIn
    from gwnetwork.events.ingest import ingest
    from gwnetwork.events.view import rebuild
    ingest([
        EventRecordIn(common_name="GW170817", source="gwosc", catalog="GWTC-1-confident",
                      version=1, gps=1187008882.4, external_id="a",
                      parameters={"mass_1_source": 1.46, "mass_2_source": 1.27,
                                  "luminosity_distance": 40.0,
                                  "network_matched_filter_snr": 33.0},
                      designations=[DesignationIn("GW170817"),
                                    DesignationIn("AT2017gfo", kind="em_counterpart")]),
        EventRecordIn(common_name="GW150914", source="gwosc", catalog="GWTC-1-confident",
                      version=1, gps=1126259462.4, external_id="b",
                      parameters={"mass_1_source": 34.6, "mass_2_source": 30.0,
                                  "luminosity_distance": 470.0,
                                  "network_matched_filter_snr": 24.4},
                      designations=[DesignationIn("GW150914")]),
        EventRecordIn(common_name="GW000000", source="gwosc", catalog="GWTC-1-marginal",
                      version=1, gps=1126259999.0, external_id="c",
                      parameters={"far": 2.0}, designations=[DesignationIn("GW000000")]),
    ])
    rebuild()


def test_event_filters(temp_db):
    _seed_view(temp_db)
    from gwnetwork.web.app import app
    c = TestClient(app)

    # Match the result link, not the bare name: event names also appear in the
    # search box placeholder, which made a naive substring assertion pass on
    # pages that contained no results at all.
    def listed(qs):
        body = c.get("/events?" + qs).text
        return set(re.findall(r'/event/([A-Za-z0-9_]+)"', body))

    assert listed("mass_class=BNS") == {"GW170817"}
    assert listed("mass_class=BBH") == {"GW150914"}
    assert listed("q=AT2017gfo") == {"GW170817"}          # alias search
    assert listed("dist_max=100") == {"GW170817"}
    assert listed("marginal=only") == {"GW000000"}
    # GW000000 is seeded at an O1 GPS time too, marginal or not.
    assert listed("run=O1") == {"GW150914", "GW000000"}


def test_numeric_filter_discloses_events_with_no_measurement(temp_db):
    """A third of real events have no mass. Excluding them must be visible."""
    _seed_view(temp_db)
    from gwnetwork.web.app import app
    body = TestClient(app).get("/events?m1_min=1").text
    assert "hidden because they have no" in body
    assert "GW000000" not in set(re.findall(r'/event/([A-Za-z0-9_]+)"', body))
