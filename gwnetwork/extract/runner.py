"""Run mention extraction over papers. Deterministic, no LLM, no API cost."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update

from ..db import session
from ..models import AnalysisRun, Mention, Paper, UnknownDesignation
from .matcher import DesignationMatcher, normalise
from .sections import SectionIndex, sentence_around

PIPELINE_VERSION = "extract-0.1"


@dataclass
class ExtractStats:
    papers: int = 0
    mentions: int = 0
    unknown: int = 0
    no_text: int = 0

    def __str__(self) -> str:
        return (f"{self.papers} papers  {self.mentions} mentions  "
                f"{self.unknown} unknown designations  {self.no_text} without text")


def _paper_text(paper: Paper) -> Optional[str]:
    if paper.fulltext_path:
        from pathlib import Path
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
        run = AnalysisRun(stage="extract", pipeline_version=PIPELINE_VERSION,
                          config={"matcher": "regex-longest-first"})
        s.add(run)
        s.flush()
        # Only one extract run is "current" at a time; the rest stay for diffing.
        s.execute(
            update(AnalysisRun)
            .where(AnalysisRun.stage == "extract", AnalysisRun.id != run.id)
            .values(is_current=False)
        )

        matcher = DesignationMatcher.from_db(s)

        q = select(Paper)
        if paper_ids:
            q = q.where(Paper.id.in_(paper_ids))
        if limit:
            q = q.limit(limit)

        for paper in s.scalars(q):
            text = _paper_text(paper)
            if not text:
                stats.no_text += 1
                continue
            stats.papers += 1
            index = SectionIndex(text)

            for m in matcher.find(text):
                sentence = sentence_around(text, m.start, m.end)
                section = index.label_at(m.start)
                if m.event_id is not None:
                    s.add(Mention(
                        paper_id=paper.id, event_id=m.event_id, analysis_run_id=run.id,
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
