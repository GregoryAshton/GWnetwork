"""Read-only projection of the database.

Deliberately thin: every interesting question is a SQL query, and the templates
render answers rather than computing them. Evidence quotes are shown inline
because a classification the reader cannot check is not worth displaying.

Event filtering runs against `event_view`, the materialised projection built by
`gwn events rebuild-view` -- parameters live in JSON on EventRecord and are not
directly queryable.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..db import session
from ..version import build_info, data_info
from ..models import (
    AnalysisRun, Event, EventDesignation, EventRecord, EventUsage, EventView,
    Mention, Paper,
)

app = FastAPI(title="GWnetwork")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

ROW_LIMIT = 500
ENGAGEMENT_ORDER = ["strain_reanalysis", "posterior_samples",
                    "published_results", "cited_only"]
SORTS = [("papers", "Most mentioning papers"), ("name", "Name"),
         ("gps", "Date (newest first)"), ("m1", "Primary mass"),
         ("snr", "Network SNR"), ("dist", "Distance")]


def current_run(s, stage: str):
    """The analysis run the site renders.

    Analytical tables are append-only and run-versioned, so the database holds
    every superseded run as well. EVERY query touching Mention or EventUsage
    must filter on this -- without it, counts are summed across all historical
    runs (a 5x inflation on mention counts once five extract runs exist).
    """
    return s.scalar(select(AnalysisRun).where(
        AnalysisRun.stage == stage,
        AnalysisRun.is_current == True))  # noqa: E712


def mentions_in(run):
    from sqlalchemy import literal
    return Mention.analysis_run_id == (run.id if run is not None else literal("none"))


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    """Every page carries build and data provenance in its footer."""
    ctx.setdefault("build", build_info())
    if "data" not in ctx:
        with session() as s:
            ctx["data"] = data_info(s)
    return templates.TemplateResponse(request, name, ctx)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with session() as s:
        run = current_run(s, "extract")

        def count(model):
            return s.scalar(select(func.count()).select_from(model))

        stats = [
            ("Events", count(Event)),
            ("Designations", count(EventDesignation)),
            ("Papers", count(Paper)),
            ("Event–paper links", s.scalar(
                select(func.count()).select_from(
                    select(Mention.paper_id, Mention.event_id)
                    .where(mentions_in(run))
                    .group_by(Mention.paper_id, Mention.event_id).subquery())) or 0),
            ("Classified usages", s.scalar(
                select(func.count()).select_from(EventUsage)) or 0),
        ]
        n_papers = count(Paper)
        n_fulltext = s.scalar(select(func.count()).select_from(Paper)
                              .where(Paper.fulltext_path.is_not(None))) or 0
        top = [
            {"name": n, "n_papers": v.n_papers, "ceiling": c,
             "mass_class": v.mass_class, "run": v.observing_run}
            for n, v, c in s.execute(
                select(Event.canonical_name, EventView, Event.ads_ceiling)
                .join(EventView, EventView.event_id == Event.id)
                .order_by(EventView.n_papers.desc()).limit(15)).all()
        ]
    return render(request, "index.html", stats=stats, top=top,
                  n_papers=n_papers, n_fulltext=n_fulltext)


def _num(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@app.get("/events", response_class=HTMLResponse)
def events(request: Request, q: Optional[str] = None, mass_class: Optional[str] = None,
           run: Optional[str] = None, m1_min: Optional[str] = None,
           m1_max: Optional[str] = None, snr_min: Optional[str] = None,
           snr_max: Optional[str] = None, dist_min: Optional[str] = None,
           dist_max: Optional[str] = None, papers: Optional[str] = None,
           marginal: Optional[str] = None, sort: str = "papers",
           static: int = 0):
    f = {"q": q, "mass_class": mass_class, "run": run, "papers": papers,
         "marginal": marginal, "sort": sort,
         "m1_min": _num(m1_min), "m1_max": _num(m1_max),
         "snr_min": _num(snr_min), "snr_max": _num(snr_max),
         "dist_min": _num(dist_min), "dist_max": _num(dist_max)}

    with session() as s:
        total = s.scalar(select(func.count()).select_from(Event)) or 0
        stmt = select(Event.canonical_name, EventView).join(
            EventView, EventView.event_id == Event.id)

        if q:
            matching = select(EventDesignation.event_id).where(
                EventDesignation.designation.contains(q))
            stmt = stmt.where(Event.canonical_name.contains(q) | Event.id.in_(matching))
        if mass_class:
            stmt = stmt.where(EventView.mass_class == mass_class)
        if run:
            stmt = stmt.where(EventView.observing_run == run)
        if papers == "yes":
            stmt = stmt.where(EventView.n_papers > 0)
        elif papers == "no":
            stmt = stmt.where(EventView.n_papers == 0)
        if marginal == "exclude":
            stmt = stmt.where(EventView.is_marginal.is_(False))
        elif marginal == "only":
            stmt = stmt.where(EventView.is_marginal.is_(True))

        # A numeric filter can only ever match events where the quantity was
        # measured. Count what that excludes rather than letting it vanish:
        # a third of events have no mass, and silently dropping them would
        # misrepresent the catalogue.
        numeric = [
            (EventView.mass_1_source, f["m1_min"], f["m1_max"]),
            (EventView.network_matched_filter_snr, f["snr_min"], f["snr_max"]),
            (EventView.luminosity_distance, f["dist_min"], f["dist_max"]),
        ]
        hidden_no_value = 0
        for col, lo, hi in numeric:
            if lo is None and hi is None:
                continue
            hidden_no_value += s.scalar(
                select(func.count()).select_from(EventView).where(col.is_(None))) or 0
            if lo is not None:
                stmt = stmt.where(col >= lo)
            if hi is not None:
                stmt = stmt.where(col <= hi)

        order = {
            "papers": EventView.n_papers.desc(),
            "name": Event.canonical_name.asc(),
            "gps": EventView.gps.desc(),
            "m1": EventView.mass_1_source.desc(),
            "snr": EventView.network_matched_filter_snr.desc(),
            "dist": EventView.luminosity_distance.desc(),
        }.get(sort, EventView.n_papers.desc())

        shown = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = s.execute(stmt.order_by(order).limit(ROW_LIMIT)).all()
        out = []
        for name, view in rows:
            view.name = name
            out.append(view)
        runs = [r for (r,) in s.execute(
            select(EventView.observing_run).where(EventView.observing_run.is_not(None))
            .group_by(EventView.observing_run)
            .order_by(EventView.observing_run)).all()]

    return render(request, "events.html", events=out, f=f, total=total, shown=shown,
                  runs=runs, sorts=SORTS, hidden_no_value=hidden_no_value,
                  static=bool(static))


@app.get("/event/{name}", response_class=HTMLResponse)
def event_page(request: Request, name: str):
    with session() as s:
        ev = s.scalar(select(Event).where(Event.canonical_name == name))
        if ev is None:
            return render(request, "not_found.html",
                          message=f"No event named {name}.")
        erun, crun = current_run(s, "extract"), current_run(s, "classify")
        view = s.scalar(select(EventView).where(EventView.event_id == ev.id))
        records = s.scalars(select(EventRecord).where(EventRecord.event_id == ev.id)
                            .order_by(EventRecord.catalog, EventRecord.version)).all()
        aliases = s.scalars(select(EventDesignation.designation)
                            .where(EventDesignation.event_id == ev.id)).all()
        rows = s.execute(
            select(Paper, EventUsage).join(EventUsage, EventUsage.paper_id == Paper.id)
            .where(EventUsage.event_id == ev.id,
                   EventUsage.analysis_run_id == (crun.id if crun else None))).all()
        grouped = defaultdict(list)
        for paper, usage in rows:
            grouped[usage.engagement].append((paper, usage))
        usages = [(lvl, grouped[lvl]) for lvl in ENGAGEMENT_ORDER if grouped.get(lvl)]

        mentions = s.execute(
            select(Paper, func.count(Mention.id))
            .join(Mention, Mention.paper_id == Paper.id)
            .where(Mention.event_id == ev.id, mentions_in(erun))
            .group_by(Paper.id)
            .order_by(Paper.citation_count.desc().nullslast(),
                      func.count(Mention.id).desc())
            .limit(ROW_LIMIT)).all()
    return render(request, "event.html", event=ev, view=view, records=records,
                  aliases=aliases, usages=usages, mentions=mentions)


@app.get("/explore", response_class=HTMLResponse)
def explore(request: Request):
    from .charts import attention_over_time, attention_vs_snr, mass_plane
    with session() as s:
        run = current_run(s, "extract")
        ctx = {"mass": mass_plane(s), "time": attention_over_time(s, run),
               "snr": attention_vs_snr(s)}
    return render(request, "explore.html", **ctx)


@app.get("/papers", response_class=HTMLResponse)
def papers(request: Request):
    with session() as s:
        run = current_run(s, "extract")
        total = s.scalar(select(func.count()).select_from(Paper)) or 0
        rows = s.execute(
            select(Paper, func.count(func.distinct(Mention.event_id)))
            .outerjoin(Mention, (Mention.paper_id == Paper.id) & mentions_in(run))
            .group_by(Paper.id)
            .order_by(Paper.citation_count.desc().nullslast())
            .limit(200)).all()
    return render(request, "papers.html", rows=rows, total=total)


@app.get("/paper/{paper_id}", response_class=HTMLResponse)
def paper_page(request: Request, paper_id: str):
    with session() as s:
        p = s.scalar(select(Paper).where(Paper.id == paper_id))
        if p is None:
            return render(request, "not_found.html", message="No such paper.")
        erun, crun = current_run(s, "extract"), current_run(s, "classify")
        rows = s.execute(
            select(Event.canonical_name, func.count(Mention.id))
            .join(Mention, Mention.event_id == Event.id)
            .where(Mention.paper_id == paper_id, mentions_in(erun))
            .group_by(Event.id).order_by(func.count(Mention.id).desc())).all()
        usages = dict(s.execute(
            select(Event.canonical_name, EventUsage.engagement)
            .join(EventUsage, EventUsage.event_id == Event.id)
            .where(EventUsage.paper_id == paper_id,
                   EventUsage.analysis_run_id == (crun.id if crun else None))).all())
    return render(request, "paper.html", paper=p, rows=rows, usages=usages)
