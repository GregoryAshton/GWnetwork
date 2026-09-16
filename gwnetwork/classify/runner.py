"""Classification: rules first, LLM for the remainder.

Two cost decisions are baked into the shape of this module (DESIGN.md 7a):

1. One call per PAPER, not per (paper, event) pair. A GWTC population paper
   mentioning 90 events is one call, not 90 near-identical ones.
2. Pairs the rules settle confidently never reach the model at all.

Prompt caching is deliberately NOT used: Haiku 4.5 has a 4096-token minimum
cacheable prefix and this system prompt is ~1k tokens, so a cache_control marker
would bill the 1.25x write premium and never produce a read. The Batch API's 50%
is the real lever.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, update

from ..config import CONFIG
from ..db import session
from ..models import AnalysisRun, Event, EventUsage, Mention, Paper
from .prompts import PROMPT_SHA256, SYSTEM, build_user_prompt
from .rules import classify_engagement
from .schemas import SCHEMA

PIPELINE_VERSION = "classify-0.1"

MAX_EXCERPTS_PER_EVENT = 3
MAX_EXCERPTS_TOTAL = 40
MAX_TOKENS = 4000


@dataclass
class ClassifyStats:
    papers_considered: int = 0
    papers_skipped_no_fulltext: int = 0
    pairs_total: int = 0
    pairs_by_rule: int = 0
    pairs_to_llm: int = 0
    papers_called: int = 0
    usages_written: int = 0
    errors: list = field(default_factory=list)

    def __str__(self) -> str:
        saved = (100.0 * self.pairs_by_rule / self.pairs_total) if self.pairs_total else 0.0
        skip = (f"  ({self.papers_skipped_no_fulltext} papers skipped: abstract only)"
                if self.papers_skipped_no_fulltext else "")
        return (f"{self.papers_considered} papers{skip}, {self.pairs_total} pairs: "
                f"{self.pairs_by_rule} by rule ({saved:.0f}% of LLM spend avoided), "
                f"{self.pairs_to_llm} to LLM in {self.papers_called} calls, "
                f"{self.usages_written} usages written")


def _excerpts(mentions: list) -> list:
    per_event: dict = defaultdict(list)
    for m in mentions:
        per_event[m.event_id].append(m)
    out = []
    for ms in per_event.values():
        # Prefer substantive sections when trimming -- an introduction sentence
        # is the least informative excerpt we could spend tokens on.
        ms = sorted(ms, key=lambda m: (m.section in ("introduction", "front_matter",
                                                     "references"),))
        for m in ms[:MAX_EXCERPTS_PER_EVENT]:
            out.append({"section": m.section or "unknown", "text": m.sentence})
    return out[:MAX_EXCERPTS_TOTAL]


def _plan(s, limit: Optional[int], require_fulltext: bool = True) -> tuple:
    """Group current mentions by paper; split pairs into rule-settled and LLM."""
    run = s.scalar(select(AnalysisRun).where(
        AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
    if run is None:
        raise RuntimeError("No current extract run. Run `gwn extract` first.")

    rows = s.scalars(select(Mention).where(Mention.analysis_run_id == run.id)).all()

    # Classifying from an abstract alone is a different, much weaker task than
    # classifying from full text: engagement is essentially undecidable without
    # the methods and data sections, and the rule filter cannot fire either. We
    # refuse to spend money on it by default rather than produce confident
    # labels from evidence that cannot support them.
    allowed = None
    if require_fulltext:
        allowed = {pid for (pid,) in s.execute(
            select(Paper.id).where(Paper.fulltext_path.is_not(None))).all()}

    by_paper: dict = defaultdict(lambda: defaultdict(list))
    for m in rows:
        if allowed is not None and m.paper_id not in allowed:
            continue
        by_paper[m.paper_id][m.event_id].append(m)

    names = dict(s.execute(select(Event.id, Event.canonical_name)).all())
    plans = []
    for paper_id, per_event in list(by_paper.items())[: limit or None]:
        rule_settled, needs_llm = [], []
        for event_id, ms in per_event.items():
            verdict = classify_engagement(ms)
            if verdict.needs_llm or verdict.engagement is None:
                needs_llm.append((event_id, ms))
            else:
                rule_settled.append((event_id, verdict))
        plans.append((paper_id, rule_settled, needs_llm))
    return plans, names


def _write_rule_usages(s, run_id, paper_id, rule_settled) -> int:
    n = 0
    for event_id, verdict in rule_settled:
        s.add(EventUsage(
            paper_id=paper_id, event_id=event_id, analysis_run_id=run_id,
            engagement=verdict.engagement, roles=[], is_primary_subject=False,
            confidence=verdict.confidence, evidence=verdict.evidence, method="rule",
        ))
        n += 1
    return n


def _request_for(paper: Paper, needs_llm: list, names: dict) -> Optional[dict]:
    if not needs_llm:
        return None
    all_mentions = [m for _, ms in needs_llm for m in ms]
    event_names = [names[eid] for eid, _ in needs_llm if eid in names]
    if not event_names:
        return None
    return {
        "model": CONFIG.classify_model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": build_user_prompt(
            paper.title, paper.abstract[:2000], event_names, _excerpts(all_mentions))}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }


def _apply_response(s, run_id, paper_id, needs_llm, names, payload: dict) -> int:
    by_name = {names[eid]: eid for eid, _ in needs_llm if eid in names}
    n = 0
    for item in payload.get("events", []):
        event_id = by_name.get(item.get("event_name"))
        if event_id is None:
            continue
        s.add(EventUsage(
            paper_id=paper_id, event_id=event_id, analysis_run_id=run_id,
            engagement=item["engagement"], roles=item.get("roles", []),
            is_primary_subject=bool(item.get("is_primary_subject")),
            confidence=float(item.get("confidence", 0.0)),
            evidence=item.get("evidence", []), method="llm",
        ))
        n += 1
    return n


def classify(limit: Optional[int] = None, dry_run: bool = False,
             use_batch: bool = True, require_fulltext: bool = True) -> ClassifyStats:
    stats = ClassifyStats()
    with session() as s:
        plans, names = _plan(s, limit, require_fulltext=require_fulltext)
        if require_fulltext:
            run = s.scalar(select(AnalysisRun).where(
                AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
            with_mentions = s.scalar(select(func.count(func.distinct(Mention.paper_id)))
                                     .where(Mention.analysis_run_id == run.id)) or 0
            stats.papers_skipped_no_fulltext = max(with_mentions - len(plans), 0)
        papers = {p.id: p for p in s.scalars(
            select(Paper).where(Paper.id.in_([p[0] for p in plans]))).all()}

        run = AnalysisRun(
            stage="classify", pipeline_version=PIPELINE_VERSION,
            model_id=CONFIG.classify_model, prompt_sha256=PROMPT_SHA256,
            config={"batch": use_batch, "dry_run": dry_run},
        )
        s.add(run)
        s.flush()

        requests = []
        for paper_id, rule_settled, needs_llm in plans:
            stats.papers_considered += 1
            stats.pairs_total += len(rule_settled) + len(needs_llm)
            stats.pairs_by_rule += len(rule_settled)
            stats.pairs_to_llm += len(needs_llm)
            if not dry_run:
                stats.usages_written += _write_rule_usages(s, run.id, paper_id, rule_settled)
            req = _request_for(papers[paper_id], needs_llm, names)
            if req is not None:
                requests.append((paper_id, needs_llm, req))

        stats.papers_called = len(requests)

        if dry_run or not requests:
            run.completed_at = datetime.utcnow()
            if dry_run:
                s.rollback()
            return stats

        results = (_run_batch(requests) if use_batch else _run_sync(requests))
        for paper_id, needs_llm, payload, err in results:
            if err:
                stats.errors.append(f"{paper_id}: {err}")
                continue
            stats.usages_written += _apply_response(
                s, run.id, paper_id, needs_llm, names, payload)

        s.execute(update(AnalysisRun)
                  .where(AnalysisRun.stage == "classify", AnalysisRun.id != run.id)
                  .values(is_current=False))
        run.completed_at = datetime.utcnow()
    return stats


def _client():
    import anthropic
    if not CONFIG.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=CONFIG.anthropic_api_key)


def _text_of(message) -> str:
    return next(b.text for b in message.content if b.type == "text")


def _run_sync(requests: list) -> list:
    client = _client()
    out = []
    for paper_id, needs_llm, req in requests:
        try:
            msg = client.messages.create(**req)
            out.append((paper_id, needs_llm, json.loads(_text_of(msg)), None))
        except Exception as exc:  # noqa: BLE001
            out.append((paper_id, needs_llm, {}, str(exc)))
    return out


def _run_batch(requests: list, poll_seconds: int = 30) -> list:
    """Batch API: 50% cheaper, and nothing here is latency-sensitive."""
    import time

    from anthropic.types.messages.batch_create_params import Request

    client = _client()
    index = {f"p{i}": (pid, needs) for i, (pid, needs, _) in enumerate(requests)}
    batch = client.messages.batches.create(requests=[
        Request(custom_id=f"p{i}", params=req)
        for i, (_, _, req) in enumerate(requests)
    ])
    while True:
        status = client.messages.batches.retrieve(batch.id)
        if status.processing_status == "ended":
            break
        time.sleep(poll_seconds)

    out = []
    # Results arrive in arbitrary order -- key by custom_id, never by position.
    for result in client.messages.batches.results(batch.id):
        paper_id, needs_llm = index[result.custom_id]
        if result.result.type != "succeeded":
            out.append((paper_id, needs_llm, {}, result.result.type))
            continue
        try:
            out.append((paper_id, needs_llm,
                        json.loads(_text_of(result.result.message)), None))
        except Exception as exc:  # noqa: BLE001
            out.append((paper_id, needs_llm, {}, str(exc)))
    return out
