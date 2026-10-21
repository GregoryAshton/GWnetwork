"""Discovery + full-text fetch orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from ..config import CONFIG
from ..db import session
from ..models import EventDesignation, Paper, PaperDiscovery
from .ads import AdsClient, AdsPaper
from .arxiv import ArxivFetcher


@dataclass
class DiscoverStats:
    designations_queried: int = 0
    hits: int = 0
    papers_new: int = 0
    papers_seen: int = 0
    queries: int = 0
    rate_remaining: Optional[int] = None
    errors: list = field(default_factory=list)

    def __str__(self) -> str:
        tail = (f"  |  {self.queries} ADS queries, {self.rate_remaining} left today"
                if self.rate_remaining is not None else "")
        return (f"{self.designations_queried} designations queried, {self.hits} hits, "
                f"{self.papers_new} new papers ({self.papers_seen} already known){tail}")


def _parse_pubdate(pubdate: Optional[str]) -> Optional[datetime]:
    if not pubdate:
        return None
    try:
        y, m, _ = pubdate.split("-")
        return datetime(int(y), max(int(m), 1), 1)
    except Exception:  # noqa: BLE001
        return None


def _record_discovery(s, paper_id: str, designation: str) -> None:
    exists = s.scalar(select(PaperDiscovery).where(
        PaperDiscovery.paper_id == paper_id,
        PaperDiscovery.designation == designation))
    if exists is None:
        s.add(PaperDiscovery(paper_id=paper_id, designation=designation))


def _upsert(s, ap: AdsPaper, via: str) -> bool:
    existing = None
    if ap.arxiv_id:
        existing = s.scalar(select(Paper).where(Paper.arxiv_id == ap.arxiv_id))
    if existing is None:
        existing = s.scalar(select(Paper).where(Paper.bibcode == ap.bibcode))
    if existing is not None:
        if not existing.arxiv_id and ap.arxiv_id:
            existing.arxiv_id = ap.arxiv_id
        _record_discovery(s, existing.id, via.split(":", 1)[-1])
        return False
    paper = Paper(
        arxiv_id=ap.arxiv_id, bibcode=ap.bibcode, doi=ap.doi, title=ap.title,
        abstract=ap.abstract, authors=ap.authors, categories=ap.categories,
        primary_category=(ap.categories[0] if ap.categories else None),
        submitted_at=_parse_pubdate(ap.pubdate), discovered_via=via,
    )
    s.add(paper)
    s.flush()
    _record_discovery(s, paper.id, via.split(":", 1)[-1])
    return True


def discover(limit_designations: Optional[int] = None,
             max_rows: Optional[int] = None,
             categories: Optional[list] = None) -> DiscoverStats:
    """Query ADS once per known designation. This is the whole backfill."""
    stats = DiscoverStats()
    with session() as s:
        desigs = [d for (d,) in s.execute(
            select(EventDesignation.designation)
            .group_by(EventDesignation.designation)
            .order_by(func.length(EventDesignation.designation).desc())
        ).all()]
    if limit_designations:
        desigs = desigs[:limit_designations]

    with AdsClient() as ads, session() as s:
        for designation in desigs:
            stats.designations_queried += 1
            try:
                for ap in ads.papers_mentioning(designation, categories=categories,
                                                max_rows=max_rows):
                    stats.hits += 1
                    if _upsert(s, ap, via=f"ads:{designation}"):
                        stats.papers_new += 1
                    else:
                        stats.papers_seen += 1
            except Exception as exc:  # noqa: BLE001
                stats.errors.append(f"{designation}: {exc}")
        stats.queries = ads.queries
        stats.rate_remaining = ads.rate_remaining
        for label, found, cap in ads.truncated:
            stats.errors.append(f"TRUNCATED {label}: {found} results, capped at {cap}")
    return stats


@dataclass
class FetchStats:
    attempted: int = 0
    fetched: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return f"{self.fetched}/{self.attempted} full texts fetched ({self.failed} unavailable)"


def fetch_fulltext(limit: Optional[int] = None, force: bool = False,
                   min_designations: int = 0,
                   rate_delay: float = 3.0) -> FetchStats:
    """Fetch full text, optionally only for event-dense papers.

    Event coverage saturates fast: papers matching >=5 ADS designations number
    978 and cover 432 of 434 events -- the same breadth as fetching all 13,893
    at a fifteenth of the requests. Everything beyond that buys depth, not
    breadth, so it is not worth hammering arXiv for.
    """
    stats = FetchStats()
    with session() as s:
        q = select(Paper).where(Paper.arxiv_id.is_not(None))
        if not force:
            q = q.where(Paper.fulltext_path.is_(None))
        if min_designations > 0:
            dense = (select(PaperDiscovery.paper_id)
                     .group_by(PaperDiscovery.paper_id)
                     .having(func.count() >= min_designations))
            q = q.where(Paper.id.in_(dense))
        # Always order densest-first: a partial run then maximises event
        # coverage rather than fetching an arbitrary slice.
        counts = dict(s.execute(
            select(PaperDiscovery.paper_id, func.count())
            .group_by(PaperDiscovery.paper_id)).all())
        papers = s.scalars(q).all()
        if counts:
            papers.sort(key=lambda p: -counts.get(p.id, 0))
        if limit:
            papers = papers[:limit]

        with ArxivFetcher(rate_delay=rate_delay) as fetcher:
            for paper in papers:
                # A full run takes hours; commit as we go so an interruption
                # keeps what was fetched rather than rolling it all back.
                if stats.attempted and stats.attempted % 25 == 0:
                    s.commit()
                stats.attempted += 1
                got = fetcher.fetch(paper.arxiv_id, force=force)
                if got is None:
                    stats.failed += 1
                    # Abstract-only is a degraded but valid mode.
                    paper.fulltext_source = "abstract_only"
                    continue
                path, sha, source = got
                paper.fulltext_path = str(path)
                paper.fulltext_sha256 = sha
                paper.fulltext_source = source
                stats.fetched += 1
    return stats


def add_arxiv(arxiv_ids: list) -> int:
    """Seed papers by explicit arXiv id (gold sets, demos, no ADS token needed)."""
    from .arxiv import fetch_metadata

    meta = fetch_metadata(arxiv_ids)
    added = 0
    with session() as s:
        for m in meta:
            if s.scalar(select(Paper).where(Paper.arxiv_id == m["arxiv_id"])):
                continue
            submitted = None
            if m.get("published"):
                try:
                    submitted = datetime.strptime(m["published"][:10], "%Y-%m-%d")
                except ValueError:
                    pass
            s.add(Paper(
                arxiv_id=m["arxiv_id"], title=m["title"], abstract=m["abstract"],
                authors=m["authors"], categories=m["categories"],
                primary_category=(m["categories"][0] if m["categories"] else None),
                submitted_at=submitted, discovered_via="manual:arxiv-id",
            ))
            added += 1
    return added


def update_versions(batch_size: int = 100, rate_delay: float = 3.0,
                    only_missing: bool = True) -> dict:
    """Fill first-submission and latest-version dates from the arXiv API.

    The text we fetch is the latest arXiv version, so that version's date is
    the one the site shows (models.paper_date). ADS gave only a month-level
    publication date, and nothing recorded later revisions.

    Commits after every batch, so an interrupted run keeps its progress and a
    re-run with only_missing picks up where it stopped.
    """
    import time

    from .arxiv import fetch_metadata

    with session() as s:
        q = select(Paper.arxiv_id).where(Paper.arxiv_id.is_not(None))
        if only_missing:
            q = q.where(Paper.updated_at_src.is_(None))
        ids = [a for (a,) in s.execute(q).all()]

    stats = {"papers": len(ids), "updated": 0, "revised_after_first_year": 0, "missing": 0}
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        try:
            meta = {m["arxiv_id"]: m for m in fetch_metadata(chunk)}
        except Exception as exc:  # noqa: BLE001 -- one bad batch must not lose the rest
            print(f"batch {i // batch_size}: {exc}", flush=True)
            time.sleep(rate_delay * 5)
            continue
        with session() as s:
            for paper in s.scalars(select(Paper).where(Paper.arxiv_id.in_(chunk))):
                m = meta.get(paper.arxiv_id)
                if not m or not m.get("updated"):
                    stats["missing"] += 1
                    continue
                first = _iso(m.get("published"))
                latest = _iso(m["updated"])
                if first:
                    paper.submitted_at = first
                paper.updated_at_src = latest
                paper.version = m.get("version")
                stats["updated"] += 1
                if first and latest and latest.year > first.year:
                    stats["revised_after_first_year"] += 1
        time.sleep(rate_delay)
    return stats


def _iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
