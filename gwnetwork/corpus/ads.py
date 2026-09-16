"""ADS-mediated discovery.

The loop is inverted relative to the obvious design: rather than scanning all of
arXiv, we ask ADS once per known designation which papers mention it in their
full text. ~430 events x a few pages is a complete backfill inside one day's
rate limit.

Known limitation, recorded rather than hidden: discovery keyed on names we
already know cannot find papers about events we have never heard of. The
generic-regex pass in extract/ recovers most of that, because novel events are
nearly always discussed alongside known ones.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

import httpx

from ..config import CONFIG

FIELDS = "bibcode,title,abstract,author,doi,identifier,pubdate,year,arxiv_class,entry_date"


@dataclass
class AdsPaper:
    bibcode: str
    title: str = ""
    abstract: str = ""
    authors: list = field(default_factory=list)
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    categories: list = field(default_factory=list)
    pubdate: Optional[str] = None


class AdsError(RuntimeError):
    pass


class AdsClient:
    def __init__(self, token: Optional[str] = None, rate_delay: float = 0.6):
        self.token = token or CONFIG.ads_token
        if not self.token:
            raise AdsError(
                "No ADS token. Get one at https://ui.adsabs.harvard.edu/user/settings/token "
                "and export ADS_DEV_KEY=..."
            )
        self.rate_delay = rate_delay
        self.queries = 0
        self.rate_remaining: Optional[int] = None
        self.truncated: list = []       # designations whose results were cut short
        self._client = httpx.Client(
            base_url=CONFIG.ads_base, timeout=60.0,
            headers={"Authorization": f"Bearer {self.token}",
                     "User-Agent": CONFIG.user_agent},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def count(self, query: str) -> int:
        """numFound without pulling documents. One query, cheap."""
        r = self._client.get("/search/query", params={"q": query, "fl": "bibcode", "rows": 0})
        r.raise_for_status()
        self.queries += 1
        self._note_limits(r)
        return r.json().get("response", {}).get("numFound", 0)

    def _note_limits(self, r) -> None:
        remaining = r.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            self.rate_remaining = int(remaining)

    def search(self, query: str, rows: int = 200,
               max_rows: Optional[int] = None, label: str = "") -> Iterator[AdsPaper]:
        """Page through a query.

        max_rows defaults to None (no cap). A cap here truncates SILENTLY, and
        the biggest events are exactly the ones that would be cut: GW170817
        alone matches over 9000 papers in our category scope, so the old default
        of 2000 would have dropped three quarters of its literature without
        saying so. When a cap is set and hit, the designation is recorded in
        `self.truncated` so the caller can report it.
        """
        start = 0
        while max_rows is None or start < max_rows:
            r = self._client.get("/search/query", params={
                "q": query, "fl": FIELDS, "rows": rows, "start": start,
                "sort": "date desc",
            })
            if r.status_code == 429:
                raise AdsError("ADS rate limit reached; resume tomorrow or use a second token")
            r.raise_for_status()
            self.queries += 1
            self._note_limits(r)
            body = r.json().get("response", {})
            docs = body.get("docs", [])
            num_found = body.get("numFound", 0)
            if not docs:
                return
            for d in docs:
                yield _to_paper(d)
            start += len(docs)
            if len(docs) < rows or start >= num_found:
                return
            if max_rows is not None and start >= max_rows:
                self.truncated.append((label or query, num_found, max_rows))
                return
            time.sleep(self.rate_delay)

    def papers_mentioning(self, designation: str, categories: Optional[list] = None,
                          **kw) -> Iterator[AdsPaper]:
        q = f'full:"{designation}"'
        cats = categories if categories is not None else CONFIG.categories
        if cats:
            q += " AND (" + " OR ".join(f'arxiv_class:"{c}"' for c in cats) + ")"
        return self.search(q, label=designation, **kw)


def _to_paper(d: dict) -> AdsPaper:
    arxiv_id = None
    for ident in d.get("identifier", []) or []:
        if ident.lower().startswith("arxiv:"):
            arxiv_id = ident.split(":", 1)[1]
            break
    doi = (d.get("doi") or [None])[0]
    title = (d.get("title") or [""])[0]
    return AdsPaper(
        bibcode=d["bibcode"], title=title,
        abstract=d.get("abstract") or "",
        authors=d.get("author") or [],
        doi=doi, arxiv_id=arxiv_id,
        categories=d.get("arxiv_class") or [],
        pubdate=d.get("pubdate"),
    )
