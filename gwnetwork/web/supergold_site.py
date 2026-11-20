"""Static labelling site for the super-gold set (`gwn eval supergold site`).

Plain files for any static host (a separate GitHub Pages repo or branch; never
docs/, which is the public read-only site). Nothing here runs on a server:

  index.html        landing page; reads the reviewer's token from ?r=
  q/<hash>.json     a reviewer's fixed queue (calibration papers first), named
                    by queue_file(token) so the repo never lists usable tokens
  p/<paper>.html    one page per paper: snippets, the usage questions per
                    event, confidence, author flag, skip
  guide.html        eval/GUIDELINES.md (frozen) with a note on what differs
  config.js         the submission endpoint and provenance stamps
  sg.js             all the page logic

Answers are POSTed as one JSON payload per paper to a Google Apps Script web
app (eval/supergold/apps_script.gs) that appends them to a Sheet. Pages carry
mention snippets and a link to arXiv, never the full text.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..classify.prompts import DATA_QUESTIONS, FALLBACK_QUESTION
from ..config import ROOT
from ..supergold import CONFIDENCE, SKIP_REASONS, load_assignments, load_sample
from ..version import build_info

TEMPLATES = Path(__file__).parent / "templates"
GUIDE_PATH = ROOT / "eval" / "GUIDELINES.md"

# Shorter than the local labeller's: enough to judge, not the paper.
MAX_MENTIONS = 12
CONTEXT_CHARS = 260
CITE_CONTEXT_CHARS = 200


def queue_file(token: str) -> str:
    """The queue's file name: a hash, so hosting the site (or its repo) in
    public does not reveal the tokens. sg.js computes the same in the browser."""
    import hashlib
    return hashlib.sha256(f"gwn-supergold:{token}".encode()).hexdigest()[:20]


def slug(arxiv_id: str) -> str:
    return arxiv_id.replace("/", "_")


def build_site(out_dir: Path, endpoint: str = "", sample: Optional[dict] = None,
               assignments: Optional[dict] = None) -> dict:
    from .label import _context

    sample = sample or load_sample()
    assignments = assignments or load_assignments()
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "p").mkdir(parents=True)
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
    for p, kind in pages:
        events = []
        title = abstract = None
        for pair in p["pairs"]:
            ctx = _context(p["arxiv_id"], pair["event"], max_mentions=MAX_MENTIONS,
                           width=CONTEXT_CHARS, cite_width=CITE_CONTEXT_CHARS)
            if ctx["paper"]:
                title, abstract = ctx["paper"]["title"], ctx["paper"]["abstract"]
            events.append({"event": pair["event"], **ctx})
        page = {"arxiv_id": p["arxiv_id"], "kind": kind,
                "uniform": bool(p.get("uniform")),
                "events": [e["event"] for e in events]}
        if kind == "calibration":
            page["answers"] = {r["event"]: r["uses"] for r in p["pairs"]}
        html = env.get_template("sg_paper.html").render(
            **common, arxiv_id=p["arxiv_id"], title=title or p["arxiv_id"],
            abstract=abstract, events=events, page=page)
        (out_dir / "p" / f"{slug(p['arxiv_id'])}.html").write_text(html)

    for r in assignments["reviewers"]:
        (out_dir / "q" / f"{queue_file(r['token'])}.json").write_text(json.dumps(
            {"queue": [{"arxiv_id": a, "slug": slug(a)} for a in r["queue"]]}))

    (out_dir / "index.html").write_text(env.get_template("sg_index.html").render(**common))
    guide = GUIDE_PATH.read_text() if GUIDE_PATH.exists() else ""
    (out_dir / "guide.html").write_text(
        env.get_template("sg_guide.html").render(**common, text=guide))
    (out_dir / "config.js").write_text("window.SG_CONFIG = " + json.dumps({
        "endpoint": endpoint, "site": site, "guide": sample["guide_version"],
        "gold_version": sample["gold_version"]}) + ";\n")
    shutil.copy(TEMPLATES / "sg.js", out_dir / "sg.js")
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (out_dir / ".nojekyll").write_text("")
    return {"papers": len(pages), "reviewers": len(assignments["reviewers"]),
            "site": site, "endpoint": endpoint or "(none: test mode)"}
