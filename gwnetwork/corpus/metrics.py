"""Citation counts and the per-event ADS mention ceiling.

Two different numbers, deliberately kept apart:

* `Paper.citation_count` -- how often a paper is cited. Useful for ranking the
  papers on an event page, so the influential ones surface first.
* `Event.ads_ceiling` -- how many papers ADS full-text search matches for any of
  the event's designations. This is the denominator our mention count is a
  fraction of, and showing it is what stops a mention count being mistaken for
  a complete one.

Neither is a "citations of an event". That quantity is not well defined: GWTC-3
is the discovery paper for 35 events at once, so citations of it cannot be
attributed to any one of them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy import select

from ..config import CONFIG
from ..db import session
from ..models import Event, EventDesignation, Paper, PaperReference

BIGQUERY_CHUNK = 2000


@dataclass
class MetricStats:
    citations_updated: int = 0
    ceilings_updated: int = 0
    queries: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (f"{self.citations_updated} citation counts, "
                f"{self.ceilings_updated} event ceilings, "
                f"{self.queries} ADS queries, {self.errors} errors")


def _headers() -> dict:
    if not CONFIG.ads_token:
        raise RuntimeError("ADS_DEV_KEY is not set")
    return {"Authorization": f"Bearer {CONFIG.ads_token}"}


def backfill_citations(stats: MetricStats) -> None:
    """Bulk citation counts via the ADS bigquery endpoint."""
    with session() as s:
        bibs = [b for (b,) in s.execute(
            select(Paper.bibcode).where(Paper.bibcode.is_not(None))).all()]

    counts: dict = {}
    with httpx.Client(timeout=180.0) as c:
        for i in range(0, len(bibs), BIGQUERY_CHUNK):
            chunk = bibs[i:i + BIGQUERY_CHUNK]
            r = c.post(
                CONFIG.ads_base + "/search/bigquery",
                params={"q": "*:*", "fl": "bibcode,citation_count",
                        "rows": len(chunk), "fq": "{!bitset}"},
                content="bibcode\n" + "\n".join(chunk),
                headers={**_headers(), "Content-Type": "big-query/csv"})
            stats.queries += 1
            if r.status_code != 200:
                stats.errors += 1
                continue
            for d in r.json().get("response", {}).get("docs", []):
                counts[d["bibcode"]] = d.get("citation_count") or 0
            time.sleep(0.5)

    # Iterate rather than an IN clause: SQLite caps bound variables, and this
    # list runs to five figures.
    with session() as s:
        for paper in s.scalars(select(Paper).where(Paper.bibcode.is_not(None))):
            if paper.bibcode in counts:
                paper.citation_count = counts[paper.bibcode]
                stats.citations_updated += 1


def backfill_ceilings(stats: MetricStats, rate_delay: float = 0.4) -> None:
    """Per-event ADS mention ceiling: the union over all its designations."""
    with session() as s:
        by_event: dict = {}
        for eid, desig in s.execute(
                select(EventDesignation.event_id, EventDesignation.designation)).all():
            by_event.setdefault(eid, set()).add(desig)
        cats = "(" + " OR ".join(f'arxiv_class:"{c}"' for c in CONFIG.categories) + ")"

    ceilings: dict = {}
    with httpx.Client(timeout=120.0, headers=_headers()) as c:
        for eid, desigs in by_event.items():
            union = "(" + " OR ".join(f'full:"{d}"' for d in sorted(desigs)) + ")"
            r = c.get(CONFIG.ads_base + "/search/query",
                      params={"q": f"{union} AND {cats}", "fl": "bibcode", "rows": 0})
            stats.queries += 1
            if r.status_code != 200:
                stats.errors += 1
                continue
            ceilings[eid] = r.json()["response"]["numFound"]
            time.sleep(rate_delay)

    with session() as s:
        for event in s.scalars(select(Event)):
            if event.id in ceilings:
                event.ads_ceiling = ceilings[event.id]
                stats.ceilings_updated += 1


def backfill(citations: bool = True, ceilings: bool = True) -> MetricStats:
    stats = MetricStats()
    if citations:
        backfill_citations(stats)
    if ceilings:
        backfill_ceilings(stats)
    return stats


def backfill_references(only_missing: bool = True) -> dict:
    """Store each paper's ADS reference list (bigquery, 2,000 bibcodes a request).

    Replaces a paper's rows on refresh. A paper ADS returns with no `reference`
    field is marked fetched with no rows; one ADS does not return at all is
    left unfetched and counted.
    """
    from datetime import datetime, timezone
    with session() as s:
        q = select(Paper.bibcode).where(Paper.bibcode.is_not(None), ~Paper.withdrawn)
        if only_missing:
            q = q.where(Paper.references_fetched_at.is_(None))
        bibs = [b for (b,) in s.execute(q).all()]

    refs: dict = {}
    stats = {"papers": len(bibs), "queries": 0, "with_references": 0,
             "no_references": 0, "not_returned": 0, "references": 0}
    with httpx.Client(timeout=180.0) as c:
        for i in range(0, len(bibs), BIGQUERY_CHUNK):
            chunk = bibs[i:i + BIGQUERY_CHUNK]
            r = c.post(
                CONFIG.ads_base + "/search/bigquery",
                params={"q": "*:*", "fl": "bibcode,reference",
                        "rows": len(chunk), "fq": "{!bitset}"},
                content="bibcode\n" + "\n".join(chunk),
                headers={**_headers(), "Content-Type": "big-query/csv"})
            stats["queries"] += 1
            r.raise_for_status()
            for d in r.json().get("response", {}).get("docs", []):
                refs[d["bibcode"]] = sorted(set(d.get("reference") or []))
            time.sleep(0.5)

        # bigquery does not follow bibcode aliases: an arXiv bibcode ADS has
        # since replaced (2022arXiv220903790D -> 2022PhDT.......133D) comes
        # back missing. The search API's identifier field does follow them.
        missing = [b for b in bibs if b not in refs]
        for i in range(0, len(missing), 40):
            chunk = missing[i:i + 40]
            r = c.get(CONFIG.ads_base + "/search/query", headers=_headers(), params={
                "q": "identifier:(" + " OR ".join(f'"{b}"' for b in chunk) + ")",
                "fl": "bibcode,identifier,reference", "rows": 200})
            stats["queries"] += 1
            r.raise_for_status()
            for d in r.json().get("response", {}).get("docs", []):
                for b in set(chunk) & set(d.get("identifier") or []):
                    refs[b] = sorted(set(d.get("reference") or []))
            time.sleep(0.5)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    wanted = set(bibs)
    # Iterate rather than an IN clause: SQLite caps bound variables.
    with session() as s:
        for paper in s.scalars(select(Paper).where(Paper.bibcode.is_not(None))):
            if paper.bibcode not in wanted:
                continue
            if paper.bibcode not in refs:
                stats["not_returned"] += 1
                continue
            s.query(PaperReference).filter(PaperReference.paper_id == paper.id).delete()
            s.add_all(PaperReference(paper_id=paper.id, bibcode=b) for b in refs[paper.bibcode])
            paper.references_fetched_at = now
            stats["references"] += len(refs[paper.bibcode])
            stats["with_references" if refs[paper.bibcode] else "no_references"] += 1
    return stats
