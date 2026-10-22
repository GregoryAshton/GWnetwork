"""Run mention extraction over papers. Deterministic, no LLM, no API cost."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update

from ..config import CONFIG
from ..db import session
from ..models import AnalysisRun, Event, Mention, Paper, UnknownDesignation
from .matcher import DesignationMatcher, normalise
from .sections import SectionIndex, sentence_around

# 0.3: bare-number candidates need trigger context; reviewed per-paper
# reassignments from data/mention_overrides.yaml; withdrawn papers skipped.
PIPELINE_VERSION = "extract-0.3"


@dataclass
class ExtractStats:
    papers: int = 0
    mentions: int = 0
    unknown: int = 0
    no_text: int = 0
    withdrawn: int = 0
    reassigned: int = 0

    def __str__(self) -> str:
        return (f"{self.papers} papers  {self.mentions} mentions  "
                f"{self.unknown} unknown designations  {self.no_text} without text  "
                f"{self.withdrawn} withdrawn  {self.reassigned} reassigned by override")


def load_mention_overrides(path: Optional[Path] = None) -> dict:
    """Reviewed reassignments: {arxiv_id: {from_event: to_event}} by canonical name.

    For a paper that names one event but means another, which no matcher rule
    can see: 1903.01998 writes "GW190521" throughout, but its masses are
    GW190521_074359's. The mention keeps its text and offsets; only the event
    changes. Every entry needs a reason.
    """
    import yaml
    path = path or CONFIG.data_dir / "mention_overrides.yaml"
    if not path.exists():
        return {}
    out = {}
    for arxiv_id, entries in (yaml.safe_load(path.read_text()) or {}).items():
        for e in entries:
            if not (e.get("from") and e.get("to") and e.get("reason")):
                raise ValueError(f"{path}: {arxiv_id} entries need from, to and reason")
            out.setdefault(str(arxiv_id), {})[e["from"]] = e["to"]
    return out


def _paper_text(paper: Paper) -> Optional[str]:
    if paper.fulltext_path:
        p = Path(paper.fulltext_path)
        if p.exists():
            return p.read_text(errors="replace")
    if paper.abstract:
        # Abstract-only is a legitimate degraded mode: it still yields correct
        # mentions, just fewer of them. Section labels stay meaningful.
        return f"Abstract\n\n{paper.abstract}"
    return None


def extract_all(limit: Optional[int] = None, paper_ids: Optional[list] = None) -> ExtractStats:
    stats = ExtractStats()
    with session() as s:
        overrides = load_mention_overrides()
        run = AnalysisRun(stage="extract", pipeline_version=PIPELINE_VERSION,
                          config={"matcher": "regex-longest-first",
                                  "mention_overrides": overrides})
        s.add(run)
        s.flush()
        # Only one extract run is "current" at a time; the rest stay for diffing.
        s.execute(
            update(AnalysisRun)
            .where(AnalysisRun.stage == "extract", AnalysisRun.id != run.id)
            .values(is_current=False)
        )

        matcher = DesignationMatcher.from_db(s)
        ids = dict(s.execute(select(Event.canonical_name, Event.id)).all())
        remaps = {}
        for arxiv_id, pairs in overrides.items():
            for a, b in pairs.items():
                if a not in ids or b not in ids:
                    raise ValueError(f"mention override for {arxiv_id}: unknown event {a} or {b}")
                remaps.setdefault(arxiv_id, {})[ids[a]] = ids[b]

        q = select(Paper)
        if paper_ids:
            q = q.where(Paper.id.in_(paper_ids))
        if limit:
            q = q.limit(limit)

        for paper in s.scalars(q):
            if paper.withdrawn:
                stats.withdrawn += 1
                continue
            text = _paper_text(paper)
            if not text:
                stats.no_text += 1
                continue
            stats.papers += 1
            index = SectionIndex(text)
            remap = remaps.get(paper.arxiv_id, {})

            for m in matcher.find(text):
                sentence = sentence_around(text, m.start, m.end)
                section = index.label_at(m.start)
                if m.event_id is not None:
                    event_id = remap.get(m.event_id, m.event_id)
                    stats.reassigned += event_id != m.event_id
                    s.add(Mention(
                        paper_id=paper.id, event_id=event_id, analysis_run_id=run.id,
                        matched_text=m.text, char_start=m.start, char_end=m.end,
                        section=section, sentence=sentence, is_ambiguous=m.is_ambiguous,
                    ))
                    stats.mentions += 1
                else:
                    canonical = normalise(m.text)
                    row = s.scalar(select(UnknownDesignation).where(
                        UnknownDesignation.designation == canonical,
                        UnknownDesignation.analysis_run_id == run.id))
                    if row is None:
                        s.add(UnknownDesignation(
                            designation=canonical, analysis_run_id=run.id,
                            first_seen_paper_id=paper.id, example_sentence=sentence,
                        ))
                        stats.unknown += 1
                    else:
                        row.occurrences += 1

        run.completed_at = datetime.utcnow()
    return stats
