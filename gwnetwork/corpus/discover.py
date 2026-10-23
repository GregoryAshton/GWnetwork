"""Discovery + full-text fetch orchestration."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select

from ..config import CONFIG
from ..db import session
from ..models import EventDesignation, Paper, PaperDiscovery
from .ads import AdsClient, AdsPaper
from .arxiv import (HTML_CONVERTER, ArxivFetcher, VersionGone, body_start,
                    pdf_converter, text_issues, version_dates)


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
    no_version: int = 0
    withdrawn: int = 0
    flagged: list = field(default_factory=list)

    def __str__(self) -> str:
        out = f"{self.fetched}/{self.attempted} full texts fetched ({self.failed} unavailable)"
        if self.withdrawn:
            out += f"; {self.withdrawn} withdrawn (latest version has no HTML or PDF)"
        if self.no_version:
            out += f"; {self.no_version} skipped, arXiv API returned no version"
        for arxiv_id, issues in self.flagged:
            out += f"\n  review {arxiv_id}: {', '.join(issues)}"
        return out


def load_overrides(path: Optional[Path] = None) -> dict:
    """Reviewed per-paper fetch overrides: {arxiv_id: {version, source, reason}}.

    For papers whose latest version renders badly (see arxiv.text_issues).
    Each entry must say why, since it departs from "latest version".
    """
    import yaml
    path = path or CONFIG.data_dir / "fulltext_overrides.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    out = {}
    for arxiv_id, entry in raw.items():
        if not entry.get("version") or not entry.get("reason"):
            raise ValueError(f"{path}: {arxiv_id} needs a version and a reason")
        if entry.get("source", "arxiv_html") not in ("arxiv_html", "pdf"):
            raise ValueError(f"{path}: {arxiv_id} source must be arxiv_html or pdf")
        out[str(arxiv_id)] = entry
    return out


def fetch_fulltext(limit: Optional[int] = None, force: bool = False,
                   unpinned: bool = False, only_source: Optional[str] = None,
                   min_designations: int = 0,
                   rate_delay: float = 3.0) -> FetchStats:
    """Fetch full text, pinned to each paper's latest arXiv version.

    Selects papers without text, plus overrides not yet applied; `unpinned`
    adds papers whose stored text has no known version (see pin_existing) or
    came from an outdated converter,
    `force` re-fetches everything (or every `only_source` text, e.g. all PDFs
    after a converter change). Overrides in data/fulltext_overrides.yaml pin
    another version and format.
    Version metadata is refreshed from the arXiv API first, so the version we
    pin is the current latest and new papers get their dates in the same step.

    Event coverage saturates fast: papers matching >=5 ADS designations number
    978 and cover 432 of 434 events -- the same breadth as fetching all 13,893
    at a fifteenth of the requests. Everything beyond that buys depth, not
    breadth, so it is not worth hammering arXiv for.
    """
    stats = FetchStats()
    overrides = load_overrides()
    with session() as s:
        q = select(Paper).where(Paper.arxiv_id.is_not(None))
        if only_source:
            q = q.where(Paper.fulltext_source == only_source)
        if not force:
            missing = Paper.fulltext_path.is_(None) & ~Paper.withdrawn
            if unpinned:
                # Unknown version, or made by a converter we no longer use.
                outdated = (((Paper.fulltext_source == "pdf")
                             & (Paper.fulltext_converter != pdf_converter()))
                            | ((Paper.fulltext_source == "arxiv_html")
                               & (Paper.fulltext_converter != HTML_CONVERTER)))
                missing = missing | (~Paper.withdrawn & (
                    Paper.fulltext_version.is_(None) | outdated))
            # An override not yet applied (new entry, or version changed).
            stale = [a for (a, v, src) in s.execute(
                select(Paper.arxiv_id, Paper.fulltext_version, Paper.fulltext_source)
                .where(Paper.arxiv_id.in_(list(overrides))))
                     if (v, src) != (overrides[a]["version"],
                                     overrides[a].get("source", "arxiv_html"))]
            q = q.where(missing | Paper.arxiv_id.in_(stale))
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
        ids = [p.arxiv_id for p in papers]

    if not ids:
        return stats
    update_versions(rate_delay=rate_delay, arxiv_ids=ids)

    with session() as s, ArxivFetcher(rate_delay=rate_delay) as fetcher:
        for arxiv_id in ids:
            paper = s.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
            # A full run takes hours; commit as we go so an interruption
            # keeps what was fetched rather than rolling it all back.
            if stats.attempted and stats.attempted % 25 == 0:
                s.commit()
            stats.attempted += 1
            if not paper.version:
                stats.no_version += 1
                continue
            ov = overrides.get(paper.arxiv_id)
            version = ov["version"] if ov else paper.version
            sources = (ov.get("source", "arxiv_html"),) if ov else ("arxiv_html", "pdf")
            try:
                got = fetcher.fetch(paper.arxiv_id, version, sources)
            except VersionGone:
                # Keep any text we had for the record; extraction skips it.
                paper.withdrawn = True
                stats.withdrawn += 1
                continue
            if got is None:
                stats.failed += 1
                # Abstract-only is a degraded but valid mode. A failed re-fetch
                # keeps the text we already had.
                if not paper.fulltext_path:
                    paper.fulltext_source = "abstract_only"
                continue
            paper.fulltext_path = str(got.path)
            paper.fulltext_sha256 = got.sha256
            paper.fulltext_source = got.source
            paper.fulltext_converter = got.converter
            paper.fulltext_version = version
            paper.fulltext_version_date = (paper.updated_at_src if version == paper.version
                                           else version_dates(paper.arxiv_id).get(version))
            paper.fulltext_fetched_at = _utcnow()
            paper.fulltext_issues = got.issues
            paper.fulltext_body_start = got.body_start
            if got.issues:
                stats.flagged.append((paper.arxiv_id, got.issues))
            stats.fetched += 1
    return stats


def pin_existing() -> dict:
    """Record the arXiv version of texts fetched before fetches were pinned.

    Those fetches requested the unversioned URL, which serves the latest
    version at that moment. So if the latest version's date (from a fresh
    `update-versions --all`) predates the file's modification time, the text
    is that version. Texts with a newer version since, from ar5iv (which
    serves a snapshot of unknown version), or recorded only as "cached" stay
    unpinned for `fetch --unpinned` to replace. A file whose hash no longer
    matches the database is left alone too.

    Run `update-versions --all` immediately before this: the inference is
    only sound if the version metadata is newer than every fetch.
    """
    stats = {"pinned": 0, "revised_since_fetch": 0, "unpinnable_source": 0,
             "hash_mismatch": 0, "file_missing": 0, "no_version": 0}
    converters = {"arxiv_html": HTML_CONVERTER, "pdf": pdf_converter()}
    with session() as s:
        papers = s.scalars(select(Paper).where(
            Paper.fulltext_path.is_not(None), Paper.fulltext_version.is_(None))).all()
        for paper in papers:
            path = Path(paper.fulltext_path)
            if paper.fulltext_source not in converters:
                stats["unpinnable_source"] += 1
                continue
            if not path.exists():
                stats["file_missing"] += 1
                continue
            if not paper.version or not paper.updated_at_src:
                stats["no_version"] += 1
                continue
            fetched = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(tzinfo=None)
            if paper.updated_at_src >= fetched:
                stats["revised_since_fetch"] += 1
                continue
            # Raw bytes: read_text() would turn the \r some PDF texts contain
            # into \n and report a mismatch for an untouched file.
            if hashlib.sha256(path.read_bytes()).hexdigest() != paper.fulltext_sha256:
                stats["hash_mismatch"] += 1
                continue
            paper.fulltext_version = paper.version
            paper.fulltext_version_date = paper.updated_at_src
            paper.fulltext_fetched_at = fetched
            paper.fulltext_converter = converters[paper.fulltext_source]
            stats["pinned"] += 1
    return stats


def check_stored_texts() -> dict:
    """Run the content checks over every stored text and record the issues
    (and each HTML text's body start).

    Covers texts fetched before the checks existed. PDF truncation can only be
    seen at conversion time, so it is not re-detected here.
    """
    flagged = {}
    with session() as s:
        for paper in s.scalars(select(Paper).where(Paper.fulltext_path.is_not(None))):
            path = Path(paper.fulltext_path)
            if not path.exists():
                continue
            text = path.read_text(errors="replace")
            paper.fulltext_body_start = body_start(text, paper.fulltext_source)
            found = text_issues(text)
            kept = [i for i in paper.fulltext_issues or [] if i == "pdf-truncated"]
            paper.fulltext_issues = kept + found
            if paper.fulltext_issues:
                flagged[paper.arxiv_id] = paper.fulltext_issues
    return flagged


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
                    only_missing: bool = True,
                    arxiv_ids: Optional[list] = None) -> dict:
    """Fill first-submission and latest-version dates from the arXiv API.

    `fetch_fulltext` pins the latest version found here. ADS gave only a
    month-level publication date, and nothing recorded later revisions.
    `arxiv_ids` restricts the refresh to those papers, ignoring only_missing.

    Commits after every batch, so an interrupted run keeps its progress and a
    re-run with only_missing picks up where it stopped.
    """
    import time

    from .arxiv import fetch_metadata

    if arxiv_ids is not None:
        ids = list(arxiv_ids)
    else:
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
