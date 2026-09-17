from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from gwnetwork.events.base import DesignationIn, EventRecordIn
from gwnetwork.events.ingest import ingest
from gwnetwork.events.view import rebuild
from gwnetwork.models import AnalysisRun, Event, EventView, Mention, Paper
from gwnetwork.web.charts import MAX_POINTS, event_timeline

GW150914_GPS = 1126259462.4     # 2015-09-14


def _seed(db, n_papers: int, citations=None):
    with db.session() as s:
        ingest([EventRecordIn(
            common_name="GW150914", source="gwosc", catalog="GWTC-1-confident",
            version=1, gps=GW150914_GPS, external_id="a",
            parameters={"mass_1_source": 34.6, "mass_2_source": 30.0},
            designations=[DesignationIn("GW150914")])])
    rebuild()
    with db.session() as s:
        ev = s.scalar(select(Event))
        run = AnalysisRun(stage="extract", pipeline_version="t", is_current=True)
        s.add(run)
        s.flush()
        for i in range(n_papers):
            year = 2015 + (i % 11)
            p = Paper(arxiv_id=f"20{i:04d}.{i:05d}", title=f"Paper {i}", abstract="x",
                      submitted_at=datetime(year, 1 + (i % 12), 1),
                      citation_count=(citations(i) if citations else i))
            s.add(p)
            s.flush()
            s.add(Mention(paper_id=p.id, event_id=ev.id, analysis_run_id=run.id,
                          matched_text="GW150914", char_start=0, char_end=8,
                          section="results", sentence="We analyse GW150914."))
        return ev.id, run.id


def _chart(db):
    with db.session() as s:
        ev = s.scalar(select(Event))
        view = s.scalar(select(EventView))
        run = s.scalar(select(AnalysisRun).where(AnalysisRun.is_current == True))  # noqa: E712
        return event_timeline(s, ev, view, run)


def test_too_few_papers_yields_no_chart(temp_db):
    _seed(temp_db, 2)
    assert _chart(temp_db)["empty"] is True


def test_event_marker_sits_at_the_detection_date(temp_db):
    _seed(temp_db, 20)
    c = _chart(temp_db)
    assert c["marker"]["label"] == "2015-09-14"
    assert 0 <= c["marker"]["x"] <= c["w"]


def test_zero_citation_papers_are_plotted_not_dropped(temp_db):
    """10% of GW170817's papers have zero citations; a log axis cannot show 0."""
    _seed(temp_db, 30, citations=lambda i: 0 if i % 3 == 0 else i)
    c = _chart(temp_db)
    zeros = [p for p in c["points"] if p["cites"] == 0]
    assert len(zeros) == 10
    assert all(p["y"] > c["zero_rule"] for p in zeros), "zeros belong below the rule"
    assert c["n_zero"] == 10


def test_sampling_accounts_for_every_paper(temp_db):
    """`plotted + dropped` must equal the true total.

    Computing `truncated` before sampling reported 73 dropped when 276 were.
    """
    n = MAX_POINTS + 400
    _seed(temp_db, n)
    c = _chart(temp_db)
    assert c["n"] + c["truncated"] == n
    assert c["n"] <= MAX_POINTS


def test_sampling_keeps_the_most_recent_papers(temp_db):
    """An integer stride plus a trailing cut deleted the latest year entirely."""
    n = MAX_POINTS + 400
    _seed(temp_db, n)
    c = _chart(temp_db)
    years = {p["date"][:4] for p in c["points"]}
    assert "2025" in years, sorted(years)
    assert "2015" in years


def test_points_stay_inside_the_plot(temp_db):
    _seed(temp_db, 200)
    c = _chart(temp_db)
    assert all(0 <= p["x"] - p["r"] and p["x"] + p["r"] <= c["w"] + 1 for p in c["points"])
    assert all(0 <= p["y"] - p["r"] and p["y"] + p["r"] <= c["h"] + 1 for p in c["points"])
