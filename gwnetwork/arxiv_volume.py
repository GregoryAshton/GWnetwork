"""arXiv submission volume per quarter, to normalise the corpus's growth.

Two fetches from the arXiv query API, both kept as files under
`data/arxiv_volume/` with the queries and fetch dates, so the numbers are
reproducible as of the fetch:

- a **listing** of every paper in the tracked archives (astro-ph, gr-qc,
  hep-ph, hep-th, nucl-th; one deduplicated union query per quarter): arXiv
  id, first-submission time, primary category and all categories. Every
  count is derived from it locally -- cross-list-inclusive or primary-only,
  per archive or per subcategory (astro-ph.HE), and the deduplicated union --
  so a new breakdown needs no new fetch.
- **API totals** (`opensearch:totalResults`) for each archive, the union and
  all of arXiv. All of arXiv is too large to list; the rest are a check
  that the listing is complete.

Dates are `submittedDate`, the UTC time of version 1 (the API's
`published`), matching the corpus's `submitted_at`. arxiv.org/stats counts
by the month in the arXiv id, which follows the announcement day in US
Eastern time, so about 1% of papers near a quarter boundary fall on the
other side there.

API quirks found 2026-11-25: `cat:astro-ph` matches nothing (the
subcategories need `cat:astro-ph*`); `max_results=0` returns HTTP 500, so
totals use `max_results=1`; plain http is no longer served.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx

from .config import CONFIG

API = "https://export.arxiv.org/api/query"
# The API answers HTTP 500 for any page starting at offset 10,000 or more.
MAX_OFFSET = 10_000
ARCHIVES = ["astro-ph", "gr-qc", "hep-ph", "hep-th", "nucl-th"]
PAGE = 2000
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
_ID = re.compile(r"abs/(.+?)(v\d+)?$")


def default_dir() -> Path:
    return CONFIG.data_dir / "arxiv_volume"


# --- quarters and queries ---------------------------------------------------

@dataclass(frozen=True)
class Quarter:
    year: int
    q: int

    @classmethod
    def parse(cls, s: str) -> "Quarter":
        m = re.fullmatch(r"(\d{4})Q([1-4])", s)
        if not m:
            raise ValueError(f"quarter must look like 2015Q1, got {s!r}")
        return cls(int(m.group(1)), int(m.group(2)))

    def __str__(self) -> str:
        return f"{self.year}Q{self.q}"

    def next(self) -> "Quarter":
        return Quarter(self.year + self.q // 4, self.q % 4 + 1)

    @property
    def start(self) -> date:
        return date(self.year, 3 * self.q - 2, 1)

    @property
    def end(self) -> date:
        return self.next().start

    def date_clause(self) -> str:
        return date_clause(self.start, self.end)

    def month_clauses(self) -> list:
        starts = [date(self.year, 3 * self.q - 2 + i, 1) for i in range(3)] + [self.end]
        return [date_clause(a, b) for a, b in zip(starts, starts[1:])]


def date_clause(start: date, end: date) -> str:
    """`submittedDate:[start TO end)`, written with an inclusive upper bound
    one minute earlier. Adjacent ranges add up exactly (checked against a
    half-year query)."""
    last = datetime(end.year, end.month, end.day) - timedelta(minutes=1)
    return f"submittedDate:[{start:%Y%m%d}0000 TO {last:%Y%m%d%H%M}]"


def quarters(first: str, last: str) -> list:
    q, end, out = Quarter.parse(first), Quarter.parse(last), []
    while (q.year, q.q) <= (end.year, end.q):
        out.append(q)
        q = q.next()
    return out


# Archives split into subcategories (astro-ph.HE); `cat:astro-ph` alone
# matches none of their papers.
SUBDIVIDED = {"astro-ph", "cond-mat", "physics", "math", "cs", "q-bio", "q-fin",
              "stat", "eess", "econ", "nlin"}


def cat_term(archive: str) -> str:
    """API search term for an archive or a single category."""
    return f"cat:{archive}*" if archive in SUBDIVIDED else f"cat:{archive}"


def union_term(archives: Iterable[str] = ARCHIVES) -> str:
    return "(" + " OR ".join(cat_term(a) for a in archives) + ")"


def archive_of(category: str) -> str:
    return category.split(".")[0]


# --- HTTP -------------------------------------------------------------------

class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__(f"HTTP 429 (retry-after {retry_after})")
        try:
            self.retry_after = float(retry_after) if retry_after else None
        except ValueError:
            self.retry_after = None


class Client:
    """The arXiv API at one call per `delay` seconds, retrying 5xx, timeouts
    and the API's occasional short or empty page."""

    def __init__(self, delay: float = 3.0, retries: int = 6, log: Callable = print):
        self.delay, self.retries, self.log = delay, retries, log
        self._last = 0.0
        self._c = httpx.Client(timeout=120.0, follow_redirects=True,
                               headers={"User-Agent": CONFIG.user_agent})

    def close(self) -> None:
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get(self, query: str, start: int = 0, max_results: int = 1,
            expect: Optional[int] = None) -> ET.Element:
        params = {"search_query": query, "start": start, "max_results": max_results,
                  "sortBy": "submittedDate", "sortOrder": "ascending"}
        for attempt in range(self.retries):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self._c.get(API, params=params)
                if r.status_code == 429:
                    raise RateLimited(r.headers.get("retry-after"))
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request,
                                                response=r)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                if expect is not None and len(root.findall("a:entry", NS)) < expect:
                    raise ValueError(f"short page: {len(root.findall('a:entry', NS))} "
                                     f"of {expect}")
                return root
            except RateLimited as e:
                backoff = max(float(e.retry_after or 0), 60.0 * (attempt + 1))
                self.log(f"    rate limited; waiting {backoff:.0f}s")
                time.sleep(backoff)
            except (httpx.TransportError, httpx.HTTPStatusError, ET.ParseError,
                    ValueError) as e:
                backoff = self.delay * 2 ** (attempt + 1)
                self.log(f"    retry {attempt + 1}/{self.retries} in {backoff:.0f}s: {e}")
                time.sleep(backoff)
        raise RuntimeError(f"arXiv API failed {self.retries} times: {query} start={start}")

    def total(self, query: str) -> int:
        return _total(self.get(query))


def _total(root: ET.Element) -> int:
    return int(root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults").text)


def parse_entries(root: ET.Element) -> list:
    rows = []
    for e in root.findall("a:entry", NS):
        m = _ID.search(e.find("a:id", NS).text.strip())
        rows.append({
            "arxiv_id": m.group(1),
            "submitted": e.find("a:published", NS).text.strip(),
            "primary": e.find("x:primary_category", NS).get("term"),
            "categories": " ".join(c.get("term") for c in e.findall("a:category", NS)),
        })
    return rows


# --- listing ----------------------------------------------------------------

LISTING_FIELDS = ["arxiv_id", "submitted", "primary", "categories"]


def fetch_range(client: Client, query: str) -> tuple:
    """All entries for one query, paged. Returns ({id: row}, api_total)."""
    first = client.get(query, 0, PAGE)
    total = _total(first)
    if total > MAX_OFFSET:
        raise RuntimeError(f"{total} results; the API cannot page past {MAX_OFFSET}: {query}")
    by_id = {r["arxiv_id"]: r for r in parse_entries(first)}
    start = PAGE
    while start < total:
        page = client.get(query, start, PAGE, expect=min(PAGE, total - start))
        for r in parse_entries(page):
            by_id[r["arxiv_id"]] = r
        start += PAGE
    return by_id, total


def fetch_quarter(client: Client, quarter: Quarter, archives=ARCHIVES) -> tuple:
    """All papers in the union of `archives` first submitted in `quarter`,
    queried month by month (recent quarters exceed the API's paging limit).

    Returns (rows, api_total, queries). Paging is by submission date; papers
    that shift between pages are caught by deduplication and the final check
    that the unique count equals the summed API totals.
    """
    by_id, total, queries = {}, 0, []
    for clause in quarter.month_clauses():
        query = f"{union_term(archives)} AND {clause}"
        rows, n = fetch_range(client, query)
        by_id.update(rows)
        total += n
        queries.append(query)
    rows = sorted(by_id.values(), key=lambda r: (r["submitted"], r["arxiv_id"]))
    return rows, total, queries


def fetch_listing(first: str, last: str, out: Optional[Path] = None, delay: float = 3.0,
                  force: bool = False, log: Callable = print) -> dict:
    """Write `listing/<quarter>.csv.gz` per quarter; skip quarters already
    complete unless `force`. A quarter whose unique count misses the API
    total is retried once, then recorded with the shortfall."""
    out = out or default_dir()
    (out / "listing").mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(out)
    done = manifest.setdefault("listing", {})
    with Client(delay=delay, log=log) as client:
        for q in quarters(first, last):
            path = out / "listing" / f"{q}.csv.gz"
            if path.exists() and not force and done.get(str(q), {}).get("complete"):
                continue
            for attempt in (1, 2):
                rows, total, queries = fetch_quarter(client, q)
                if len(rows) == total:
                    break
                log(f"  {q}: {len(rows)} unique of {total}; "
                    + ("retrying" if attempt == 1 else "keeping the shortfall"))
            _write_rows(path, rows)
            done[str(q)] = {"queries": queries, "api_total": total, "rows": len(rows),
                            "complete": len(rows) == total,
                            "fetched_at": _now()}
            _save_manifest(out, manifest)
            log(f"  {q}: {len(rows)} papers")
    return done


def _write_rows(path: Path, rows: list) -> None:
    tmp = path.with_suffix(".tmp")
    # mtime=0: the same rows give the same bytes, so a re-fetch diffs cleanly.
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, LISTING_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def read_listing(out: Optional[Path] = None) -> list:
    """All listing rows, with `quarter` added."""
    out = out or default_dir()
    rows = []
    for path in sorted((out / "listing").glob("*.csv.gz")):
        q = path.name.split(".")[0]
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                r["quarter"] = q
                rows.append(r)
    return rows


# --- API totals -------------------------------------------------------------

def total_queries(archives=ARCHIVES) -> dict:
    """{name: search term}; each is ANDed with the quarter's date clause.
    Without a category term the date clause alone counts all of arXiv."""
    named = {a: cat_term(a) for a in archives}
    named["union"] = union_term(archives)
    named["all"] = ""
    return named


def fetch_totals(first: str, last: str, out: Optional[Path] = None, delay: float = 3.0,
                 force: bool = False, log: Callable = print) -> list:
    out = out or default_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / "api_totals.csv"
    have = {}
    if path.exists() and not force:
        with open(path, newline="") as f:
            have = {(r["quarter"], r["name"]): r for r in csv.DictReader(f)}
    with Client(delay=delay, log=log) as client:
        for q in quarters(first, last):
            for name, term in total_queries().items():
                if (str(q), name) in have:
                    continue
                query = f"{term} AND {q.date_clause()}" if term else q.date_clause()
                have[(str(q), name)] = {"quarter": str(q), "name": name, "query": query,
                                        "total": client.total(query), "fetched_at": _now()}
                _write_totals(path, have)
            log(f"  {q}: " + ", ".join(f"{n} {have[(str(q), n)]['total']}"
                                       for n in total_queries()))
    return list(have.values())


def _write_totals(path: Path, have: dict) -> None:
    rows = sorted(have.values(), key=lambda r: (r["quarter"], r["name"]))
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, ["quarter", "name", "query", "total", "fetched_at"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


# --- derived counts ---------------------------------------------------------

def derive_counts(rows: list, archives=ARCHIVES) -> list:
    """Long table: quarter, group, basis, n.

    `group` is an archive (astro-ph), a category (astro-ph.HE, gr-qc) or
    `union`. `basis` is `any` (the paper lists it, cross-lists included; the
    archives overlap) or `primary` (its primary category). Only groups
    inside the tracked archives are counted: the listing holds a paper from
    elsewhere only when it is cross-listed into one of them, so a count for
    hep-ex would be partial.
    """
    tracked = set(archives)
    c: Counter = Counter()
    for r in rows:
        q = r["quarter"]
        cats = r["categories"].split()
        for g in {*cats, *(archive_of(x) for x in cats)}:
            if archive_of(g) in tracked:
                c[(q, g, "any")] += 1
        p = r["primary"]
        for g in {p, archive_of(p)}:
            if archive_of(g) in tracked:
                c[(q, g, "primary")] += 1
        c[(q, "union", "any")] += 1
        if archive_of(p) in tracked:
            c[(q, "union", "primary")] += 1
    return [{"quarter": q, "group": g, "basis": b, "n": n}
            for (q, g, b), n in sorted(c.items())]


def write_counts(out: Optional[Path] = None) -> tuple:
    """Derive `counts.csv` from the listing and check it against the API
    totals. Returns (n_rows, mismatches)."""
    out = out or default_dir()
    counts = derive_counts(read_listing(out))
    with open(out / "counts.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["quarter", "group", "basis", "n"], lineterminator="\n")
        w.writeheader()
        w.writerows(counts)
    return len(counts), check_against_totals(counts, out)


def check_against_totals(counts: list, out: Path) -> list:
    """Listing-derived `any` counts against the API's own totals. They are
    two queries at slightly different times, so a paper or two may differ."""
    path = out / "api_totals.csv"
    if not path.exists():
        return []
    derived = {(r["quarter"], r["group"]): r["n"] for r in counts if r["basis"] == "any"}
    bad = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            key = (r["quarter"], r["name"])
            if r["name"] == "all" or key[0] not in {k[0] for k in derived}:
                continue
            n = derived.get(key, 0)
            if n != int(r["total"]):
                bad.append({"quarter": key[0], "group": key[1], "listing": n,
                            "api": int(r["total"])})
    return bad


STATS_URL = "https://arxiv.org/stats/get_monthly_submissions"


def fetch_monthly_stats(out: Optional[Path] = None) -> Path:
    """arXiv's own monthly submission counts (by arXiv-id month), as an
    independent check on the API's all-of-arXiv totals."""
    out = out or default_dir()
    out.mkdir(parents=True, exist_ok=True)
    r = httpx.get(STATS_URL, headers={"User-Agent": CONFIG.user_agent},
                  follow_redirects=True, timeout=60.0)
    r.raise_for_status()
    path = out / "arxiv_stats_monthly.csv"
    path.write_text(r.text)
    manifest = _load_manifest(out)
    manifest["arxiv_stats_monthly"] = {"url": STATS_URL, "fetched_at": _now()}
    _save_manifest(out, manifest)
    return path


def compare_with_stats(out: Optional[Path] = None) -> list:
    """Per quarter: the API's `all` total against the monthly statistics.
    Expect agreement to about 1% (different date conventions)."""
    out = out or default_dir()
    stats_path, totals_path = out / "arxiv_stats_monthly.csv", out / "api_totals.csv"
    if not (stats_path.exists() and totals_path.exists()):
        return []
    by_q: Counter = Counter()
    with open(stats_path, newline="") as f:
        for r in csv.DictReader(f):
            y, m = int(r["month"][:4]), int(r["month"][5:7])
            by_q[f"{y}Q{(m - 1) // 3 + 1}"] += int(r["submissions"])
    rows = []
    with open(totals_path, newline="") as f:
        for r in csv.DictReader(f):
            if r["name"] == "all" and r["quarter"] in by_q:
                api, st = int(r["total"]), by_q[r["quarter"]]
                rows.append({"quarter": r["quarter"], "api": api, "stats": st,
                             "diff_pct": round(100 * (api - st) / st, 2)})
    return rows


# --- manifest ---------------------------------------------------------------

def _load_manifest(out: Path) -> dict:
    path = out / "manifest.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"api": API, "archives": ARCHIVES, "date_field": "submittedDate (v1, UTC)",
            "user_agent": CONFIG.user_agent}


def _save_manifest(out: Path, manifest: dict) -> None:
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
