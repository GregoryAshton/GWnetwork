"""GWnetwork command line."""
from __future__ import annotations

from typing import List, Optional

import typer
from sqlalchemy import func, select

from .config import CONFIG
from .db import init_db, session
from .models import (
    Event, EventDesignation, EventMergeProposal, EventRecord, EventUsage,
    Mention, Paper, UnknownDesignation,
)

app = typer.Typer(add_completion=False, help="GWnetwork: GW events <-> literature")
events_app = typer.Typer(help="Event database")
corpus_app = typer.Typer(help="Paper corpus")
app.add_typer(events_app, name="events")
app.add_typer(corpus_app, name="corpus")


@app.command("init")
def cmd_init():
    """Create the database and data directories."""
    CONFIG.ensure_dirs()
    init_db()
    typer.echo(f"initialised {CONFIG.db_url}")


@events_app.command("ingest")
def cmd_events_ingest(
    source: str = typer.Option("all", help="gwosc | manual | all"),
):
    """Pull events from providers into the database."""
    from .events.gwosc import GWOSCProvider
    from .events.ingest import ingest
    from .events.manual import ManualProvider

    CONFIG.ensure_dirs()
    init_db()
    providers = []
    if source in ("all", "gwosc"):
        providers.append(GWOSCProvider())
    if source in ("all", "manual"):
        providers.append(ManualProvider())

    for provider in providers:
        typer.echo(f"-> {provider.name}")
        typer.echo(f"   {ingest(provider.fetch())}")


@events_app.command("list")
def cmd_events_list(limit: int = 30, search: Optional[str] = None):
    """List events with their catalogue history."""
    with session() as s:
        q = select(Event).order_by(Event.canonical_name)
        if search:
            q = q.where(Event.canonical_name.contains(search))
        events = s.scalars(q.limit(limit)).all()
        total = s.scalar(select(func.count()).select_from(Event))
        for e in events:
            recs = s.scalars(select(EventRecord)
                             .where(EventRecord.event_id == e.id)
                             .order_by(EventRecord.version)).all()
            cats = ", ".join(
                f"{r.catalog}:v{r.version}" if r.version is not None else str(r.catalog)
                for r in recs if r.catalog
            ) or "(no catalogue)"
            aliases = s.scalars(select(EventDesignation.designation)
                                .where(EventDesignation.event_id == e.id)).all()
            extra = [a for a in aliases if a != e.canonical_name]
            line = f"{e.canonical_name:<20} {cats}"
            if extra:
                line += f"   aka {', '.join(extra)}"
            typer.echo(line)
        typer.echo(f"\n{len(events)} shown of {total} events")


@events_app.command("rebuild-view")
def cmd_rebuild_view():
    """Rebuild the materialised event view used for filtering."""
    from .events.view import rebuild
    init_db()
    typer.echo(str(rebuild()))


@events_app.command("propose-aliases")
def cmd_propose_aliases(
    min_occurrences: int = 5,
    write: bool = typer.Option(False, "--write", help="Write the YAML and review files"),
):
    """Propose designation aliases from the unknown queue, GPS-verified."""
    from .events.aliases import propose, write_review, write_yaml
    report = propose(min_occurrences=min_occurrences)
    typer.echo(str(report))
    typer.echo("\ntop safe proposals:")
    for p in sorted(report.safe, key=lambda x: -x.occurrences)[:10]:
        typer.echo(f"   {p.designation:<20} -> {p.event_name:<20} x{p.occurrences}  {p.reason}")
    if write:
        CONFIG.ensure_dirs()
        n = write_yaml(report)
        write_review(report)
        typer.echo(f"\nwrote aliases for {n} events to data/events/aliases-auto.yaml")
        typer.echo("wrote data/events/REVIEW-ambiguous.md")
        typer.echo("then: gwn events ingest --source manual && gwn extract")
    else:
        typer.echo("\n(dry run -- pass --write to generate the files)")


@events_app.command("merges")
def cmd_events_merges():
    """Show pending cross-provider merge proposals (never auto-applied)."""
    with session() as s:
        rows = s.scalars(select(EventMergeProposal)
                         .where(EventMergeProposal.status == "proposed")).all()
        if not rows:
            typer.echo("no pending merge proposals")
            return
        names = dict(s.execute(select(Event.id, Event.canonical_name)).all())
        for r in rows:
            typer.echo(f"{names.get(r.event_id_a)} <-> {names.get(r.event_id_b)}  "
                       f"dGPS={r.gps_delta:.3f}s  {r.reason}")


@corpus_app.command("discover")
def cmd_discover(
    limit_designations: Optional[int] = typer.Option(None, "--limit-designations"),
    max_rows: Optional[int] = typer.Option(None, help='Cap results per designation (default: no cap)'),
):
    """Query ADS for papers mentioning each known designation."""
    from .corpus.discover import discover
    init_db()
    stats = discover(limit_designations=limit_designations, max_rows=max_rows)
    typer.echo(str(stats))
    for e in stats.errors[:10]:
        typer.echo(f"  ! {e}")


@corpus_app.command("metrics")
def cmd_metrics(
    citations: bool = typer.Option(True, help="Per-paper citation counts (ADS bigquery)"),
    ceilings: bool = typer.Option(True, help="Per-event ADS full-text mention ceiling"),
):
    """Backfill citation counts and the ADS mention ceiling per event."""
    from .corpus.metrics import backfill
    init_db()
    typer.echo(str(backfill(citations=citations, ceilings=ceilings)))


@corpus_app.command("add-arxiv")
def cmd_add_arxiv(arxiv_ids: List[str] = typer.Argument(..., help="arXiv ids, e.g. 1710.05832")):
    """Seed papers by arXiv id (no ADS token needed). Useful for gold sets."""
    from .corpus.discover import add_arxiv
    init_db()
    typer.echo(f"{add_arxiv(list(arxiv_ids))} papers added")


@corpus_app.command("fetch")
def cmd_fetch(
    limit: Optional[int] = None,
    force: bool = False,
    min_designations: int = typer.Option(
        0, help="Only papers matching at least this many event designations. "
                "5 covers 432/434 events from ~978 papers; 0 fetches everything."),
    rate_delay: float = typer.Option(
        3.0, help="Seconds between arXiv requests. Be polite; 3 s is the default."),
):
    """Fetch full text from arXiv. Densest papers first, so interrupting is safe."""
    from .corpus.discover import fetch_fulltext
    typer.echo(str(fetch_fulltext(limit=limit, force=force,
                                  min_designations=min_designations,
                                  rate_delay=rate_delay)))


@app.command("extract")
def cmd_extract(limit: Optional[int] = None):
    """Find event mentions in paper text. Deterministic, free."""
    from .extract.runner import extract_all
    init_db()
    typer.echo(str(extract_all(limit=limit)))


@app.command("classify")
def cmd_classify(
    limit: Optional[int] = None,
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Plan only: show rule/LLM split and spend nothing"),
    batch: bool = typer.Option(True, help="Use the Batch API (50% cheaper)"),
    require_fulltext: bool = typer.Option(
        True, help="Skip abstract-only papers (engagement is undecidable without full text)"),
):
    """Classify how papers use events. Rules first, LLM for the remainder."""
    from .classify.runner import classify
    init_db()
    stats = classify(limit=limit, dry_run=dry_run, use_batch=batch,
                     require_fulltext=require_fulltext)
    typer.echo(str(stats))
    for e in stats.errors[:10]:
        typer.echo(f"  ! {e}")


@app.command("unknown")
def cmd_unknown(limit: int = 30):
    """GW-shaped strings matching no known event: the discovery queue."""
    from .models import AnalysisRun
    with session() as s:
        run = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
        if run is None:
            typer.echo("no extract run yet")
            return
        # Anything a human already rejected stays rejected across runs.
        rejected = select(UnknownDesignation.designation).where(
            UnknownDesignation.status == "rejected")
        rows = s.scalars(select(UnknownDesignation)
                         .where(UnknownDesignation.analysis_run_id == run.id,
                                UnknownDesignation.status == "new",
                                UnknownDesignation.designation.not_in(rejected))
                         .order_by(UnknownDesignation.occurrences.desc())
                         .limit(limit)).all()
        if not rows:
            typer.echo("no unknown designations")
            return
        for r in rows:
            typer.echo(f"{r.designation:<20} x{r.occurrences}  {r.example_sentence[:90]}")


@app.command("status")
def cmd_status():
    """Counts across the whole pipeline.

    Analytical tables are run-versioned and never overwritten, so totals include
    superseded runs. Current-run figures are what the website shows.
    """
    from .models import AnalysisRun
    init_db()
    with session() as s:
        def n(model):
            return s.scalar(select(func.count()).select_from(model))

        def current(stage):
            return s.scalar(select(AnalysisRun).where(
                AnalysisRun.stage == stage,
                AnalysisRun.is_current == True))  # noqa: E712

        erun, crun = current("extract"), current("classify")

        def in_run(model, run, col):
            if run is None:
                return 0
            return s.scalar(select(func.count()).select_from(model).where(col == run.id))

        rows = [
            ("events", n(Event)),
            ("designations", n(EventDesignation)),
            ("event records", n(EventRecord)),
            ("papers", n(Paper)),
            ("papers with full text",
             s.scalar(select(func.count()).select_from(Paper)
                      .where(Paper.fulltext_path.is_not(None)))),
            ("merge proposals", n(EventMergeProposal)),
            ("", ""),
            ("mentions (current run)", in_run(Mention, erun, Mention.analysis_run_id)),
            ("unknown designations (current run)",
             in_run(UnknownDesignation, erun, UnknownDesignation.analysis_run_id)),
            ("event usages (current run)", in_run(EventUsage, crun, EventUsage.analysis_run_id)),
            ("", ""),
            ("analysis runs", n(AnalysisRun)),
            ("mentions (all runs)", n(Mention)),
            ("event usages (all runs)", n(EventUsage)),
        ]
        width = max(len(k) for k, _ in rows)
        for k, v in rows:
            typer.echo("" if not k else f"{k:<{width}}  {v}")


@app.command("export")
def cmd_export(
    out: str = typer.Option("docs", help="Output directory (docs/ is GitHub Pages' default)"),
):
    """Render the site to static HTML for GitHub Pages."""
    from pathlib import Path
    from .web.export import export
    stats = export(Path(out))
    typer.echo(str(stats))
    for w in stats.warnings[:10]:
        typer.echo(f"  ! {w}")


@app.command("serve")
def cmd_serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = typer.Option(
        False, help="Restart on code changes. Templates always reload; Python does not."),
):
    """Serve the read-only website."""
    import uvicorn
    uvicorn.run("gwnetwork.web.app:app", host=host, port=port, reload=reload,
                reload_includes=["*.py", "*.html"] if reload else None)


def main():  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
