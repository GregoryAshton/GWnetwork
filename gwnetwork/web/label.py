"""Local labelling UI for the gold set (`gwn eval label`).

A separate app from the read-only site on purpose: it writes to
eval/gold.jsonl, and nothing that writes may end up in the static export.

The gold file is re-read on every request so hand edits in an editor are never
clobbered, and a save rewrites only the one line it changes -- comments, field
order of other rows and unlabelled rows all survive byte-for-byte.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..citations import find_citations
from ..classify.prompts import DATA_QUESTIONS, FALLBACK_QUESTION
from ..config import ROOT
from ..counterparts import counterpart_names, is_counterpart
from ..db import session
from ..evalset import GOLD_PATH
from ..models import (
    FALLBACKS, ROLES, USES, AnalysisRun, Event, Mention, Paper, deepest_use,
    normalise_uses,
)

GUIDE_PATH = ROOT / "eval" / "GUIDELINES.md"

app = FastAPI(title="GWnetwork gold-set labeller")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

CONTEXT_CHARS = 320
CITE_CONTEXT_CHARS = 240
MAX_MENTIONS = 40
MAX_CITES = 8


# --- gold file --------------------------------------------------------------

def _load(path: Path) -> tuple[list[str], list[tuple[int, dict]]]:
    """All raw lines, plus (line index, row) for every data line."""
    lines = path.read_text().splitlines()
    rows = [(i, json.loads(l)) for i, l in enumerate(lines)
            if l.strip() and not l.lstrip().startswith("#")]
    return lines, rows


def _save_line(path: Path, line_idx: int, row: dict) -> None:
    lines = path.read_text().splitlines()
    lines[line_idx] = json.dumps(row)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, path)


def _is_labelled(row: dict) -> bool:
    return bool(row.get("engagement"))


def _next_unlabelled(rows, after: int = -1) -> Optional[int]:
    order = list(range(after + 1, len(rows))) + list(range(0, after + 1))
    return next((k for k in order if not _is_labelled(rows[k][1])), None)


# --- paper context ----------------------------------------------------------

def _fulltext(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    path = Path(p)
    if not path.is_absolute():
        path = ROOT / path
    return path.read_text(errors="replace") if path.exists() else None


def _context(arxiv_id: str, event: str) -> dict:
    with session() as s:
        paper = s.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
        run = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
        if paper is None or run is None:
            return {"paper": None, "mentions": [], "n_mentions": 0, "n_counterpart": 0}
        ms = s.execute(
            select(Mention.char_start, Mention.char_end, Mention.section,
                   Mention.sentence, Mention.matched_text)
            .join(Event, Event.id == Mention.event_id)
            .where(Mention.paper_id == paper.id, Mention.analysis_run_id == run.id,
                   Event.canonical_name == event)
            .order_by(Mention.char_start)).all()
        info = {"title": paper.title, "abstract": paper.abstract,
                "source": paper.fulltext_source}
        text = _fulltext(paper.fulltext_path)
        counterparts = counterpart_names(s)

    mentions = []
    n_counterpart = sum(is_counterpart(mt, counterparts) for *_, mt in ms)
    for start, end, section, sentence, matched in ms[:MAX_MENTIONS]:
        if text and 0 <= start < end <= len(text):
            m = {"section": section or "?", **_snippet(text, start, end, CONTEXT_CHARS),
                 "counterpart": is_counterpart(matched, counterparts)}
            # A bibliography hit is a cited paper's title; what matters is
            # where the citing paper cites it.
            if section == "references":
                cite = find_citations(text, start)
                m["cite"] = cite and {
                    "key": cite.key, "n": len(cite.spans),
                    "spans": [_snippet(text, a, b, CITE_CONTEXT_CHARS)
                              for a, b in cite.spans[:MAX_CITES]]}
                m["cite_lookup"] = True
            mentions.append(m)
        else:  # no text on disk: fall back to the stored sentence
            mentions.append({"section": section or "?", "pre": "", "hit": "",
                             "post": sentence})
    return {"paper": info, "mentions": mentions, "n_mentions": len(ms),
            "n_counterpart": n_counterpart}


def _snippet(text: str, start: int, end: int, width: int) -> dict:
    a = max(0, start - width)
    b = min(len(text), end + width)
    return {"pre": ("…" if a else "") + text[a:start], "hit": text[start:end],
            "post": text[end:b] + ("…" if b < len(text) else "")}


# --- routes -----------------------------------------------------------------

def _progress(rows) -> dict:
    done = sum(_is_labelled(r) for _, r in rows)
    return {"done": done, "total": len(rows)}


@app.get("/")
def index():
    _, rows = _load(GOLD_PATH)
    k = _next_unlabelled(rows)
    return RedirectResponse(f"/row/{k if k is not None else 0}", status_code=303)


@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    text = GUIDE_PATH.read_text() if GUIDE_PATH.exists() else "eval/GUIDELINES.md is missing."
    return templates.TemplateResponse(request, "label_guide.html", {"text": text})


@app.get("/list", response_class=HTMLResponse)
def listing(request: Request):
    _, rows = _load(GOLD_PATH)
    return templates.TemplateResponse(request, "label_list.html", {
        "rows": [r for _, r in rows], "progress": _progress(rows)})


@app.get("/row/{k}", response_class=HTMLResponse)
def row_page(request: Request, k: int, saved: int = 0):
    _, rows = _load(GOLD_PATH)
    if not 0 <= k < len(rows):
        raise HTTPException(404)
    row = rows[k][1]
    siblings = [(j, r["event"], _is_labelled(r)) for j, (_, r) in enumerate(rows)
                if r["arxiv_id"] == row["arxiv_id"]]
    return templates.TemplateResponse(request, "label.html", {
        "k": k, "row": row, "progress": _progress(rows), "saved": saved,
        "n_rows": len(rows), "next_unlabelled": _next_unlabelled(rows, k),
        "siblings": siblings, "data_questions": DATA_QUESTIONS,
        "fallback_question": FALLBACK_QUESTION, "roles": ROLES,
        "row_uses": row.get("uses") or [],
        "fallback": (None if not _is_labelled(row) or set(row.get("uses") or []) & set(USES)
                     else ("qualitative" if "qualitative" in (row.get("uses") or [])
                           else "cited_only")),
        **_context(row["arxiv_id"], row["event"])})


@app.post("/row/{k}")
async def row_save(request: Request, k: int,
                   arxiv_id: str = Form(...), event: str = Form(...),
                   fallback: str = Form("")):
    form = await request.form()
    _, rows = _load(GOLD_PATH)
    if not 0 <= k < len(rows):
        raise HTTPException(404)
    line_idx, row = rows[k]
    # The file may have been edited since the page loaded; never write a label
    # onto a different pair than the one the user was looking at.
    if (row["arxiv_id"], row["event"]) != (arxiv_id, event):
        raise HTTPException(409, "gold.jsonl changed under this page; reload it")
    data = [u for u in form.getlist("uses") if u in USES]
    if not data and fallback not in FALLBACKS:
        raise HTTPException(400, "Answer Q4 (qualitative or cited only) when Q1-Q3 are all no")
    uses = normalise_uses(data + ([fallback] if not data else []))
    roles = [r for r in form.getlist("roles") if r in ROLES]
    row = {**row, "engagement": deepest_use(uses), "uses": uses, "roles": roles,
           "unsure": bool(form.get("unsure"))}
    _save_line(GOLD_PATH, line_idx, row)
    rows[k] = (line_idx, row)

    if form.get("action") == "stay":
        return RedirectResponse(f"/row/{k}?saved=1", status_code=303)
    nxt = _next_unlabelled(rows, k)
    return RedirectResponse(f"/row/{nxt if nxt is not None else k}?saved=1",
                            status_code=303)
