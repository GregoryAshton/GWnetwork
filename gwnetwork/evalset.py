"""Gold-set harness for measuring extraction and classification quality.

This is the step the build plan calls highest-value and easiest to skip. It is
the only thing that says whether the premise works before any LLM spend, and it
becomes the permanent regression suite for every later prompt change.

Format: eval/gold.jsonl, one JSON object per line

    {"arxiv_id": "2006.12611", "event": "GW190814",
     "uses": ["strain_analysis"], "engagement": "strain_analysis",
     "roles": ["single_event"], "unsure": false}

`labelled_by` is "human" (labelled or adjudicated by a person) or
"ai-consensus" (two independent model labellings agreed); `gwn eval score`
reports the human subset separately.

`uses` is the set of answers to the usage questions (models.USES, or
["qualitative"] when none applies, [] for cited_only); `engagement` is derived
from it and also marks a row as labelled. eval/GUIDELINES.md is the labelling
guide; `gwn eval compare` measures agreement between two labellers.

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
from .models import (
    USES, AnalysisRun, Event, EventUsage, Mention, Paper, deepest_use,
    normalise_uses,
)

GOLD_PATH = ROOT / "eval" / "gold.jsonl"


def write_template(path: Optional[Path] = None, n_papers: int = 100,
                   max_pairs_per_paper: int = 8, seed: int = 0) -> int:
    """Append unlabelled rows for a random sample of papers to the gold set.

    Never rewrites existing rows -- they carry hand labels -- and skips papers
    already in the file, so it can be re-run to grow the set. Papers are drawn
    uniformly from those with full text and a current-run mention, so the set
    reflects the corpus rather than the event-dense LVK papers that dominate
    any ordering by mention count. Papers with more than `max_pairs_per_paper`
    events get a random subset of them.
    """
    import random

    path = Path(path or GOLD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    seen = {json.loads(line)["arxiv_id"] for line in existing.splitlines()
            if line.strip() and not line.startswith("#")}

    with session() as s:
        run = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
        if run is None:
            raise RuntimeError("no current extract run")
        rows = s.execute(
            select(Paper.arxiv_id, Paper.title, Event.canonical_name,
                   Mention.section, Mention.sentence)
            .join(Mention, Mention.paper_id == Paper.id)
            .join(Event, Event.id == Mention.event_id)
            .where(Mention.analysis_run_id == run.id,
                   Paper.fulltext_path.is_not(None))
            .order_by(Paper.arxiv_id, Mention.char_start)).all()

    titles: dict = {}
    per_paper: dict = defaultdict(lambda: defaultdict(list))
    for arxiv_id, title, event, section, sentence in rows:
        if arxiv_id in seen:
            continue
        titles[arxiv_id] = title
        per_paper[arxiv_id][event].append((section, sentence))

    rng = random.Random(seed)
    sample = rng.sample(sorted(per_paper), min(n_papers, len(per_paper)))

    written = 0
    with path.open("a") as fh:
        if not existing:
            fh.write("# Gold set. `engagement: null` rows are unlabelled and "
                     "skipped by the scorer.\n")
        elif not existing.endswith("\n"):
            fh.write("\n")
        for arxiv_id in sorted(sample):
            events = sorted(per_paper[arxiv_id])
            if len(events) > max_pairs_per_paper:
                events = sorted(rng.sample(events, max_pairs_per_paper))
            for event in events:
                ms = per_paper[arxiv_id][event]
                fh.write(json.dumps({
                    "arxiv_id": arxiv_id,
                    "event": event,
                    "engagement": None,      # derived from uses when labelled
                    "uses": [],              # <- human fills these in
                    "roles": [],
                    "unsure": False,
                    "_title": titles[arxiv_id][:150],
                    "_n_mentions": len(ms),
                    "_n_events_in_paper": len(per_paper[arxiv_id]),
                    "_sections": sorted({sec or "" for sec, _ in ms}),
                    "_example": ms[0][1][:200],
                }) + "\n")
                written += 1
    return written


def row_uses(row: dict) -> list:
    """A labelled row's uses; rows from before the uses split derive them."""
    if row.get("uses") is not None:
        return normalise_uses(row["uses"])
    return normalise_uses([row.get("engagement")])


def load_gold(path: Optional[Path] = None, include_unsure: bool = True) -> list:
    path = Path(path or GOLD_PATH)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            row = json.loads(line)
            if not row.get("engagement"):     # unlabelled rows are skipped
                continue
            if row.get("unsure") and not include_unsure:
                continue
            out.append(row)
    return out


def _prf(tp: int, fp: int, fn: int) -> str:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return f"precision {p:.2f} recall {r:.2f} F1 {f:.2f}  (tp {tp}, fp {fp}, fn {fn})"


@dataclass
class Agreement:
    """Agreement between a reference labelling and another (model or human)."""
    n: int = 0
    unsure: int = 0
    engagement_same: int = 0
    use_counts: dict = field(default_factory=lambda: {u: [0, 0, 0] for u in USES})
    fallback_same: int = 0
    fallback_n: int = 0
    role_tp: int = 0
    role_fp: int = 0
    role_fn: int = 0
    confusion: dict = field(default_factory=lambda: defaultdict(int))
    pairs: list = field(default_factory=list)       # (ref engagement, other engagement)
    disagreements: list = field(default_factory=list)

    def add(self, key, ref: dict, other_uses: list, other_roles: set, unsure=False):
        self.n += 1
        self.unsure += bool(unsure)
        ru, ou = row_uses(ref), normalise_uses(other_uses)
        re_, oe = deepest_use(ru), deepest_use(ou)
        self.pairs.append((re_, oe))
        self.engagement_same += re_ == oe
        if re_ != oe:
            self.confusion[(re_, oe)] += 1
        for u in USES:
            c = self.use_counts[u]
            c[0] += u in ru and u in ou
            c[1] += u not in ru and u in ou
            c[2] += u in ru and u not in ou
        if not set(ru) & set(USES) and not set(ou) & set(USES):
            self.fallback_n += 1
            self.fallback_same += re_ == oe
        rr = set(ref.get("roles") or [])
        self.role_tp += len(rr & other_roles)
        self.role_fp += len(other_roles - rr)
        self.role_fn += len(rr - other_roles)
        if ru != ou or rr != other_roles:
            self.disagreements.append({"pair": key, "ref": [ru, sorted(rr)],
                                       "other": [ou, sorted(other_roles)],
                                       "unsure": bool(unsure)})

    def kappa(self) -> float:
        """Cohen's kappa on the derived engagement."""
        if not self.pairs:
            return 0.0
        n = len(self.pairs)
        po = sum(a == b for a, b in self.pairs) / n
        cats = {c for p in self.pairs for c in p}
        pe = sum((sum(a == c for a, _ in self.pairs) / n) *
                 (sum(b == c for _, b in self.pairs) / n) for c in cats)
        return (po - pe) / (1 - pe) if pe < 1 else 1.0

    def report(self, other_name: str = "predicted") -> str:
        if not self.n:
            return "no overlapping labelled pairs"
        lines = [f"pairs compared: {self.n}" + (f" ({self.unsure} marked unsure)" if self.unsure else ""),
                 f"engagement (deepest use) agreement: {self.engagement_same}/{self.n} "
                 f"({100.0 * self.engagement_same / self.n:.1f}%), Cohen's kappa {self.kappa():.2f}"]
        for u in USES:
            lines.append(f"  {u:<18} " + _prf(*self.use_counts[u]))
        if self.fallback_n:
            lines.append(f"  qualitative vs cited_only (no data product on either side): "
                         f"{self.fallback_same}/{self.fallback_n}")
        lines.append("roles: " + _prf(self.role_tp, self.role_fp, self.role_fn))
        worst = sorted(self.confusion.items(), key=lambda kv: -kv[1])[:6]
        if worst:
            lines.append(f"top engagement confusions (reference -> {other_name}):")
            lines += [f"  {g} -> {p}: {c}" for (g, p), c in worst]
        return "\n".join(lines)


@dataclass
class Score:
    n: int = 0
    mention_recall_hits: int = 0
    agreement: Agreement = field(default_factory=Agreement)
    # Only rows a human labelled or adjudicated. AI-consensus rows come from
    # the same model family as the classifier, so this is the stricter score.
    human: Agreement = field(default_factory=Agreement)

    def report(self) -> str:
        if not self.n:
            return ("No labelled gold rows. Run `gwn eval template`, label the "
                    "rows (`gwn eval label`), then re-run.")
        lines = [f"gold pairs: {self.n}",
                 f"mention recall: {self.mention_recall_hits}/{self.n} "
                 f"({100.0*self.mention_recall_hits/self.n:.1f}%)"]
        if self.mention_recall_hits == self.n:
            lines.append("  note: 100% may be an artefact of a template-derived gold "
                         "set (it only contains pairs the extractor already found). "
                         "Add hand-read pairs to measure real recall.")
        if self.agreement.n:
            lines.append("== all gold pairs ==")
            lines.append(self.agreement.report())
            if self.human.n and self.human.n < self.agreement.n:
                lines.append("== human-labelled pairs only ==")
                lines.append(self.human.report())
        else:
            lines.append("usage: no classify run to score against "
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
            for a, e, eng, uses, roles in s.execute(
                    select(Paper.arxiv_id, Event.canonical_name, EventUsage.engagement,
                           EventUsage.uses, EventUsage.roles)
                    .join(EventUsage, EventUsage.paper_id == Paper.id)
                    .join(Event, Event.id == EventUsage.event_id)
                    .where(EventUsage.analysis_run_id == crun.id)).all():
                predicted[(a, e)] = (uses if uses else [eng], set(roles or []))

    for row in gold:
        key = (row["arxiv_id"], row["event"])
        if key in mentioned:
            sc.mention_recall_hits += 1
        if key in predicted:
            uses, roles = predicted[key]
            sc.agreement.add(key, row, uses, roles, row.get("unsure"))
            if row.get("labelled_by", "human") == "human":
                sc.human.add(key, row, uses, roles, row.get("unsure"))
    return sc


def compare(ref_path: Path, other_path: Path) -> Agreement:
    """Agreement between two labellings of the same pairs (e.g. two people)."""
    other = {(r["arxiv_id"], r["event"]): r for r in load_gold(other_path)}
    ag = Agreement()
    for row in load_gold(ref_path):
        key = (row["arxiv_id"], row["event"])
        if key in other:
            o = other[key]
            ag.add(key, row, row_uses(o), set(o.get("roles") or []),
                   row.get("unsure") or o.get("unsure"))
    return ag
