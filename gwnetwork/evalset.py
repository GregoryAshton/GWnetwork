"""Gold-set harness for measuring extraction and classification quality.

This is the step the build plan calls highest-value and easiest to skip. It is
the only thing that says whether the premise works before any LLM spend, and it
becomes the permanent regression suite for every later prompt change.

Format: eval/gold.jsonl, one JSON object per line

    {"arxiv_id": "2006.12611", "event": "GW190814",
     "engagement": "strain_reanalysis", "roles": ["single_event"]}

`gwn eval template` writes a pre-filled skeleton from current extraction output;
a human edits the labels. `gwn eval score` compares the current runs against it.

IMPORTANT CAVEAT. A template-derived gold set contains only pairs the extractor
already found, so `mention recall` against it is 100% by construction and means
nothing. It measures classification quality only. To measure true extraction
recall you must add rows by READING papers independently and recording every
(paper, event) pair present -- including ones the extractor missed. Those rows
are the only ones that can fail the recall metric, and they are the ones worth
the effort.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .config import ROOT
from .db import session
from .models import AnalysisRun, Event, EventUsage, Mention, Paper

GOLD_PATH = ROOT / "eval" / "gold.jsonl"


def write_template(path: Optional[Path] = None, limit_papers: int = 20,
                   max_pairs_per_paper: int = 8) -> int:
    """Pre-fill a gold-set skeleton from the current extract run."""
    path = Path(path or GOLD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with session() as s:
        run = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
        if run is None:
            raise RuntimeError("no current extract run")
        rows = s.execute(
            select(Paper.arxiv_id, Event.canonical_name, Mention.section, Mention.sentence)
            .join(Mention, Mention.paper_id == Paper.id)
            .join(Event, Event.id == Mention.event_id)
            .where(Mention.analysis_run_id == run.id)).all()

    per_paper: dict = defaultdict(lambda: defaultdict(list))
    for arxiv_id, event, section, sentence in rows:
        per_paper[arxiv_id][event].append((section, sentence))

    written = 0
    with path.open("w") as fh:
        for arxiv_id in list(per_paper)[:limit_papers]:
            for event, ms in list(per_paper[arxiv_id].items())[:max_pairs_per_paper]:
                fh.write(json.dumps({
                    "arxiv_id": arxiv_id,
                    "event": event,
                    "engagement": None,      # <- human fills these in
                    "roles": [],
                    "_n_mentions": len(ms),
                    "_sections": sorted({sec for sec, _ in ms}),
                    "_example": ms[0][1][:200],
                }) + "\n")
                written += 1
    return written


def load_gold(path: Optional[Path] = None) -> list:
    path = Path(path or GOLD_PATH)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            row = json.loads(line)
            if row.get("engagement"):     # unlabelled rows are skipped
                out.append(row)
    return out


@dataclass
class Score:
    n: int = 0
    mention_recall_hits: int = 0
    engagement_correct: int = 0
    engagement_scored: int = 0
    role_tp: int = 0
    role_fp: int = 0
    role_fn: int = 0
    confusion: dict = field(default_factory=lambda: defaultdict(int))

    def report(self) -> str:
        if not self.n:
            return ("No labelled gold rows. Run `gwn eval template`, fill in the "
                    "engagement/roles fields, then re-run.")
        lines = [f"gold pairs: {self.n}",
                 f"mention recall: {self.mention_recall_hits}/{self.n} "
                 f"({100.0*self.mention_recall_hits/self.n:.1f}%)"]
        if self.mention_recall_hits == self.n:
            lines.append("  note: 100% may be an artefact of a template-derived gold "
                         "set (it only contains pairs the extractor already found). "
                         "Add hand-read pairs to measure real recall.")
        if self.engagement_scored:
            lines.append(f"engagement accuracy: {self.engagement_correct}/"
                         f"{self.engagement_scored} "
                         f"({100.0*self.engagement_correct/self.engagement_scored:.1f}%)")
            p = self.role_tp / (self.role_tp + self.role_fp) if (self.role_tp + self.role_fp) else 0.0
            r = self.role_tp / (self.role_tp + self.role_fn) if (self.role_tp + self.role_fn) else 0.0
            f1 = 2 * p * r / (p + r) if (p + r) else 0.0
            lines.append(f"roles: precision {p:.2f} recall {r:.2f} F1 {f1:.2f}")
            worst = sorted(self.confusion.items(), key=lambda kv: -kv[1])[:5]
            if worst:
                lines.append("top confusions (gold -> predicted):")
                lines += [f"  {g} -> {pr}: {c}" for (g, pr), c in worst]
        else:
            lines.append("engagement: no classify run to score against "
                         "(extraction-only scoring)")
        return "\n".join(lines)


def score(path: Optional[Path] = None) -> Score:
    gold = load_gold(path)
    sc = Score(n=len(gold))
    if not gold:
        return sc

    with session() as s:
        erun = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
        crun = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "classify", AnalysisRun.is_current == True))  # noqa: E712

        mentioned = set()
        if erun is not None:
            mentioned = {
                (a, e) for a, e in s.execute(
                    select(Paper.arxiv_id, Event.canonical_name)
                    .join(Mention, Mention.paper_id == Paper.id)
                    .join(Event, Event.id == Mention.event_id)
                    .where(Mention.analysis_run_id == erun.id)).all()}

        predicted = {}
        if crun is not None:
            for a, e, eng, roles in s.execute(
                    select(Paper.arxiv_id, Event.canonical_name,
                           EventUsage.engagement, EventUsage.roles)
                    .join(EventUsage, EventUsage.paper_id == Paper.id)
                    .join(Event, Event.id == EventUsage.event_id)
                    .where(EventUsage.analysis_run_id == crun.id)).all():
                predicted[(a, e)] = (eng, set(roles or []))

    for row in gold:
        key = (row["arxiv_id"], row["event"])
        if key in mentioned:
            sc.mention_recall_hits += 1
        if key in predicted:
            pred_eng, pred_roles = predicted[key]
            sc.engagement_scored += 1
            if pred_eng == row["engagement"]:
                sc.engagement_correct += 1
            else:
                sc.confusion[(row["engagement"], pred_eng)] += 1
            gold_roles = set(row.get("roles") or [])
            sc.role_tp += len(gold_roles & pred_roles)
            sc.role_fp += len(pred_roles - gold_roles)
            sc.role_fn += len(gold_roles - pred_roles)
    return sc
