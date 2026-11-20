"""Static labelling site for the super-gold set (`gwn eval supergold site`).

Plain files for any static host (a separate GitHub Pages repo or branch; never
docs/, which is the public read-only site). Nothing here runs on a server:

  index.html        landing page; reads the reviewer's token from ?r=
  q/<hash>.json     a reviewer's fixed queue (calibration papers first), named
                    by queue_file(token) so the repo never lists usable tokens
  p/<paper>.html    one page per paper: a link to the paper at the version we
                    searched, where each event is named, the usage questions,
                    confidence, author flag, skip
  guide.html        one-page labelling guide (same rules as eval/GUIDELINES.md)
  config.js         the submission endpoint and provenance stamps
  sg.js             all the page logic

Labellers read the paper itself. Excerpts are what the classifier reads, so
labelling from them would share its blind spots: "we use the released
samples for all events" names no event, so it is never in an excerpt. The
page only says where each event is named, with an optional one-line reminder
per sentence, and never reproduces the text at length.

Answers are POSTed as one JSON payload per paper to a Google Apps Script web
app (eval/supergold/apps_script.gs) that appends them to a Sheet.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..classify.prompts import DATA_QUESTIONS, FALLBACK_QUESTION
from ..supergold import CONFIDENCE, SKIP_REASONS, load_assignments, load_sample
from ..version import build_info

TEMPLATES = Path(__file__).parent / "templates"

MAX_LINES = 8          # reminder sentences per event
HALF_WINDOW = 110      # characters either side of the name in a reminder
SECTION_NAMES = {"references": "reference list", "front_matter": "title/headings"}

# Shown after a practice paper is submitted, beside the reference answer.
CALIBRATION_NOTES = {
    ("2403.16154", "GW170817"):
        "A review: it discusses what GW170817 showed and quotes its tidal "
        "deformability as background, but uses no numbers in a calculation or "
        "comparison of its own. Qualitative (Q4).",
    ("1602.03840", "GW150914"):
        "The collaboration's own parameter estimation of GW150914 from the "
        "detector data. Strain analysis (Q1).",
    ("1808.00632", "GW170814"):
        "Mentioned once for what it showed (tensor polarisations favoured), as "
        "background. Qualitative (Q4).",
    ("1808.00632", "GW170817"):
        "The bound on the speed of gravitational waves from GW170817 is put "
        "into the paper's own constraint on the tensor speed. A published "
        "result used in its own calculation counts as Q3, even when the "
        "numbers are not restated.",
}


def queue_file(token: str) -> str:
    """The queue's file name: a hash, so hosting the site (or its repo) in
    public does not reveal the tokens. sg.js computes the same in the browser."""
    return hashlib.sha256(f"gwn-supergold:{token}".encode()).hexdigest()[:20]


def slug(arxiv_id: str) -> str:
    return arxiv_id.replace("/", "_")


def paper_url(arxiv_id: str, source: Optional[str], version: Optional[int]) -> tuple:
    """(url, label, is_html) for the version of the paper we searched."""
    v = f"v{version}" if version else ""
    if source == "arxiv_html":
        return f"https://arxiv.org/html/{arxiv_id}{v}", f"arXiv HTML{', ' + v if v else ''}", True
    if source == "ar5iv":
        return f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}", "ar5iv HTML", True
    return f"https://arxiv.org/pdf/{arxiv_id}{v}", f"PDF{', ' + v if v else ''}", False


def text_fragment(url: str, text: str) -> str:
    """A link that scrolls to and highlights `text` (Chrome, Edge, Safari,
    Firefox 131+); other browsers just open the page."""
    return url + "#:~:text=" + quote(text, safe="").replace("-", "%2D")


def _window(text: str, a: int, b: int) -> str:
    lo, hi = max(0, a - HALF_WINDOW), min(len(text), b + HALF_WINDOW)
    return ("…" if lo else "") + " ".join(text[lo:hi].split()) + ("…" if hi < len(text) else "")


def _reminder(sentence: str, hit: str) -> str:
    """The sentence, cut to a window around the name."""
    sentence = " ".join((sentence or "").split())
    i = sentence.find(hit)
    if i < 0:
        i = 0
    return _window(sentence, i, i + len(hit))


def event_index(s, arxiv_id: str, events: list, extract_run_id: str) -> dict:
    """Paper link and, per event, where it is named."""
    from sqlalchemy import select

    from ..citations import find_citations
    from ..counterparts import counterpart_names, is_counterpart
    from ..models import Event, Mention, Paper
    from .label import _fulltext

    paper = s.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
    url, url_label, is_html = paper_url(arxiv_id, paper.fulltext_source, paper.fulltext_version)
    text = _fulltext(paper.fulltext_path) or ""
    counterparts = counterpart_names(s)
    out = []
    for event in events:
        ms = s.execute(
            select(Mention.char_start, Mention.char_end, Mention.section,
                   Mention.sentence, Mention.matched_text)
            .join(Event, Event.id == Mention.event_id)
            .where(Mention.paper_id == paper.id, Mention.analysis_run_id == extract_run_id,
                   Event.canonical_name == event)
            .order_by(Mention.char_start)).all()
        where = Counter(SECTION_NAMES.get(m.section or "", m.section or "other") for m in ms)
        body = [m for m in ms if m.section not in ("references", "front_matter", "abstract")]
        first = (body or ms or [None])[0]
        lines, seen = [], set()
        for m in ms:
            cp = is_counterpart(m.matched_text, counterparts)
            # A bibliography hit is a cited paper's title; what matters is
            # where the citing paper cites it.
            if m.section == "references" and text:
                cite = find_citations(text, m.char_start)
                if cite and cite.spans:
                    for a, b in cite.spans[:2]:
                        line = _window(text, a, b)
                        if line not in seen:
                            seen.add(line)
                            lines.append({"section": f"cites it as {cite.key}",
                                          "text": line, "counterpart": cp})
                    continue
            line = _reminder(m.sentence, m.matched_text)
            if line and line not in seen:
                seen.add(line)
                lines.append({"section": SECTION_NAMES.get(m.section or "", m.section or "?"),
                              "text": line, "counterpart": cp})
        out.append({
            "event": event, "n_mentions": len(ms),
            "where": sorted(where.items(), key=lambda kv: -kv[1]),
            "only_refs": bool(ms) and all(m.section == "references" for m in ms),
            "n_counterpart": sum(is_counterpart(m.matched_text, counterparts) for m in ms),
            "search": first.matched_text if first else event,
            "jump": text_fragment(url, first.matched_text) if first and is_html else None,
            "lines": lines[:MAX_LINES], "n_lines": len(lines)})
    return {"title": paper.title, "abstract": paper.abstract, "url": url,
            "url_label": url_label, "is_html": is_html, "events": out}


def build_site(out_dir: Path, endpoint: str = "", sample: Optional[dict] = None,
               assignments: Optional[dict] = None) -> dict:
    from ..db import session

    sample = sample or load_sample()
    assignments = assignments or load_assignments()
    out_dir = Path(out_dir)
    # Keep the output's .git: the site is its own repository.
    out_dir.mkdir(parents=True, exist_ok=True)
    for child in out_dir.iterdir():
        if child.name != ".git":
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    (out_dir / "p").mkdir()
    (out_dir / "q").mkdir()

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(["html"]))
    info = build_info()
    site = (info["sha"] or "unknown") + ("+local" if info["dirty"] else "")
    common = {"data_questions": DATA_QUESTIONS, "fallback_question": FALLBACK_QUESTION,
              "confidence": CONFIDENCE, "skip_reasons": SKIP_REASONS,
              "guide_version": sample["guide_version"],
              "n_calibration": len(sample["calibration"])}

    pages = [(p, "sample") for p in sample["papers"]] + \
            [(c, "calibration") for c in sample["calibration"]]
    with session() as s:
        for p, kind in pages:
            events = [pair["event"] for pair in p["pairs"]]
            idx = event_index(s, p["arxiv_id"], events, sample["extract_run_id"])
            page = {"arxiv_id": p["arxiv_id"], "kind": kind,
                    "uniform": bool(p.get("uniform")), "events": events}
            if kind == "calibration":
                page["answers"] = {r["event"]: r["uses"] for r in p["pairs"]}
                page["notes"] = {e: CALIBRATION_NOTES.get((p["arxiv_id"], e), "")
                                 for e in events}
            html = env.get_template("sg_paper.html").render(
                **common, arxiv_id=p["arxiv_id"], page=page, **idx)
            (out_dir / "p" / f"{slug(p['arxiv_id'])}.html").write_text(html)

    for r in assignments["reviewers"]:
        (out_dir / "q" / f"{queue_file(r['token'])}.json").write_text(json.dumps(
            {"queue": [{"arxiv_id": a, "slug": slug(a)} for a in r["queue"]]}))

    (out_dir / "index.html").write_text(env.get_template("sg_index.html").render(**common))
    (out_dir / "guide.html").write_text(env.get_template("sg_guide.html").render(**common))
    (out_dir / "config.js").write_text("window.SG_CONFIG = " + json.dumps({
        "endpoint": endpoint, "site": site, "guide": sample["guide_version"],
        "gold_version": sample["gold_version"]}) + ";\n")
    shutil.copy(TEMPLATES / "sg.js", out_dir / "sg.js")
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (out_dir / ".nojekyll").write_text("")
    return {"papers": len(pages), "reviewers": len(assignments["reviewers"]),
            "site": site, "endpoint": endpoint or "(none: test mode)"}
