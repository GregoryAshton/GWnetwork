"""Render the site to static HTML for GitHub Pages.

Pages serves files, not processes, so two things change from the live site:

* Event filtering moves into the browser. That is not a downgrade -- there are
  only 434 events, so filtering client-side is instant and needs no round trip.
* Paper links point at arXiv rather than local pages. Exporting all 13,904
  paper pages would add ~49 MB to the repository to host what arXiv already
  hosts better.

Everything else is the same templates and the same queries.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from ..db import session
from ..models import Event, EventView


@dataclass
class ExportStats:
    pages: int = 0
    bytes: int = 0
    warnings: list = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.pages} pages, {self.bytes/1e6:.1f} MB"


def _client(base: str):
    from fastapi.testclient import TestClient

    from .app import app
    app.state.static_base = base
    return TestClient(app)


def _rewrite(html: str, base: str, depth: int) -> str:
    """Absolute site paths -> relative file paths.

    GitHub project pages live under /<repo>/, so absolute links would break.
    Relative links work at any mount point, including file:// for local review.
    """
    import re
    up = "../" * depth or "./"
    out = html
    # Query strings are dropped: the static events page filters client-side, so
    # ?sort=m1 has no server to interpret it.
    for path, target in (("/explore", "explore.html"), ("/events", "events.html"),
                         ("/papers", "papers.html")):
        out = re.sub(rf'href="{path}(\?[^"]*)?"', f'href="{up}{target}"', out)
    out = out.replace('href="/"', f'href="{up}index.html"')
    # /event/NAME -> event/NAME.html
    out = re.sub(r'href="/event/([^"?]+)(\?[^"]*)?"',
                 lambda m: f'href="{up}event/{m.group(1)}.html"', out)
    # Local paper pages are not exported; send readers to arXiv instead.
    out = re.sub(r'href="/paper/[0-9a-f-]+"', 'href="#"', out)
    return out


def export(out_dir: Path, base: str = "") -> ExportStats:
    stats = ExportStats()
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "event").mkdir(parents=True, exist_ok=True)
    client = _client(base)

    def write(rel: str, html: str, depth: int) -> None:
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        body = _rewrite(html, base, depth)
        p.write_text(body)
        stats.pages += 1
        stats.bytes += len(body)

    for route, rel in (("/", "index.html"), ("/explore", "explore.html"),
                       ("/papers", "papers.html")):
        r = client.get(route)
        if r.status_code != 200:
            stats.warnings.append(f"{route} -> {r.status_code}")
            continue
        write(rel, r.text, depth=0)

    # The events page ships its rows as JSON and filters in the browser.
    r = client.get("/events?static=1")
    write("events.html", r.text, depth=0)

    with session() as s:
        names = [n for (n,) in s.execute(select(Event.canonical_name)).all()]
        rows = s.execute(
            select(Event.canonical_name, EventView)
            .join(EventView, EventView.event_id == Event.id)).all()

    payload = [{
        "n": n, "c": v.mass_class, "r": v.observing_run, "p": v.n_papers,
        "m1": v.mass_1_source, "m2": v.mass_2_source, "x": v.chi_eff,
        "d": v.luminosity_distance, "s": v.network_matched_filter_snr,
        "g": v.is_marginal,
    } for n, v in rows]
    (out_dir / "events.json").write_text(json.dumps(payload, separators=(",", ":")))
    stats.bytes += (out_dir / "events.json").stat().st_size

    for name in names:
        r = client.get(f"/event/{name}")
        if r.status_code != 200:
            stats.warnings.append(f"/event/{name} -> {r.status_code}")
            continue
        write(f"event/{name}.html", r.text, depth=1)

    (out_dir / ".nojekyll").touch()          # keep Jekyll off our _-free paths
    return stats
