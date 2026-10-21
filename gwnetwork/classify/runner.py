"""Classification: rules first, LLM for the remainder.

Two cost decisions are baked into the shape of this module (DESIGN.md 7a):

1. One call per PAPER, not per (paper, event) pair. A GWTC population paper
   mentioning 90 events is a handful of calls, not 90 near-identical ones.
   Papers with more than MAX_EVENTS_PER_CALL events are split into chunks:
   asked about 130 events at once, Haiku 5.5 returned 19 and dropped the rest.
2. Pairs the rules settle confidently never reach the model at all.

Prompt caching is not used. It was ruled out under Haiku 4.5, whose 4096-token
minimum cacheable prefix exceeds this ~1k-token system prompt; Haiku 5.5's
minimum is 512, so it would now engage (DESIGN.md 7a). The Batch API's 50% is
the real lever.
"""
from __future__ import annotations

import functools
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, update

from ..config import CONFIG
from ..db import session
from ..models import (
    EventDesignation,
    USES, AnalysisRun, Event, EventUsage, Mention, Paper, deepest_use, normalise_uses,
)
from ..citations import citing_sentences
from ..counterparts import counterpart_names, is_counterpart
from .prompts import PROMPT_SHA256, SYSTEM, build_fulltext_prompt, build_user_prompt
from .rules import classify_engagement
from .schemas import SCHEMA

PIPELINE_VERSION = "classify-0.4"

MAX_EXCERPTS_PER_EVENT = 3
MAX_EXCERPTS_TOTAL = 40
MAX_EVENTS_PER_CALL = 25
# Full text asks more of each answer; at 25 events per call the model dropped
# 12 of 498 events on the gold set, so large papers are split more finely.
MAX_EVENTS_PER_CALL_FULLTEXT = 10
MAX_RETRIES = 2
# What the classifier reads. "excerpts": sentences around each mention (cheap,
# but misses collective statements such as "we reanalyse all events in Table
# 1"). "fulltext": the whole paper, as the gold-set labellers read it.
CONTEXTS = ("excerpts", "fulltext")
# A sanity cap, not a budget: ~150k tokens covers ~99% of the corpus.
MAX_FULLTEXT_CHARS = 600_000
# The Batch API takes at most 256 MB / 100k requests per batch.
BATCH_MAX_BYTES = 150_000_000
BATCH_MAX_REQUESTS = 50_000
MAX_CITED_REFS_PER_EVENT = 2
# Haiku 5.5 thinks by default and thinking counts toward this cap; at 4000,
# 7/20 large papers were truncated, 2 of them before writing any answer.
MAX_TOKENS = 16000
# Higher effort means more thinking, which also counts toward max_tokens.
MAX_TOKENS_HIGH_EFFORT = 32000
EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass
class ClassifyStats:
    papers_considered: int = 0
    papers_skipped_no_fulltext: int = 0
    pairs_total: int = 0
    pairs_by_rule: int = 0
    pairs_to_llm: int = 0
    papers_called: int = 0
    usages_written: int = 0
    est_input_tokens: int = 0       # full-text mode: rough paper-text tokens to send
    retried: int = 0                # events re-requested after a short or failed answer
    errors: list = field(default_factory=list)

    def __str__(self) -> str:
        saved = (100.0 * self.pairs_by_rule / self.pairs_total) if self.pairs_total else 0.0
        skip = (f"  ({self.papers_skipped_no_fulltext} papers skipped: abstract only)"
                if self.papers_skipped_no_fulltext else "")
        return (f"{self.papers_considered} papers{skip}, {self.pairs_total} pairs: "
                f"{self.pairs_by_rule} by rule ({saved:.0f}% of LLM spend avoided), "
                f"{self.pairs_to_llm} to LLM in {self.papers_called} calls, "
                f"{self.usages_written} usages written"
                + (f" ({self.retried} events retried)" if self.retried else "")
                + (f", ~{self.est_input_tokens / 1e6:.1f}M paper-text input tokens"
                   if self.est_input_tokens else ""))


def _excerpts(mentions: list, names: Optional[dict] = None,
              text: Optional[str] = None,
              counterparts: frozenset = frozenset()) -> list:
    per_event: dict = defaultdict(list)
    for m in mentions:
        per_event[m.event_id].append(m)
    out = []
    for event_id, ms in per_event.items():
        # Prefer substantive sections when trimming -- an introduction sentence
        # is the least informative excerpt we could spend tokens on -- and,
        # within a section, the GW name over a counterpart name.
        ms = sorted(ms, key=lambda m: (
            m.section in ("introduction", "front_matter", "references"),
            is_counterpart(getattr(m, "matched_text", ""), counterparts)))
        for m in ms[:MAX_EXCERPTS_PER_EVENT]:
            section = m.section or "unknown"
            matched = getattr(m, "matched_text", "")
            if is_counterpart(matched, counterparts):
                section += (f"; names the counterpart {matched}, not the GW signal of "
                            f"{(names or {}).get(event_id, '?')}")
            out.append({"section": section, "text": m.sentence})
        if text is not None:
            out += _citation_excerpts(ms, (names or {}).get(event_id, "?"), text)
    return out[:MAX_EXCERPTS_TOTAL]


def _citation_excerpts(ms: list, event_name: str, text: str) -> list:
    """Where the paper cites references whose entries name the event.

    A bibliography hit is a cited paper's title ("GW150914: Implications for
    the stochastic background..."); how the citing paper uses the event is in
    the sentence that cites it, which never contains the event's name.
    """
    out, seen = [], set()
    for m in ms:
        if m.section != "references" or m.char_start is None:
            continue
        key, sentences = citing_sentences(text, m.char_start)
        if key is None or key in seen:
            continue
        seen.add(key)
        for sent in sentences:
            out.append({"section": f"cites reference {key}, which names {event_name}",
                        "text": sent})
        if len(seen) >= MAX_CITED_REFS_PER_EVENT:
            break
    return out


def _plan(s, limit: Optional[int], require_fulltext: bool = True,
          only_arxiv: Optional[set] = None) -> tuple:
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
    if only_arxiv is not None:
        # e.g. the gold-set papers, to measure agreement before a full run.
        # Every event in each paper is still classified, exactly as in a full
        # run, so the calls (and the scores) are representative.
        scoped = {pid for (pid,) in s.execute(
            select(Paper.id).where(Paper.arxiv_id.in_(only_arxiv))).all()}
        allowed = scoped if allowed is None else allowed & scoped

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
            engagement=verdict.engagement, uses=normalise_uses([verdict.engagement]),
            roles=[], is_primary_subject=False,
            confidence=verdict.confidence, evidence=verdict.evidence, method="rule",
        ))
        n += 1
    return n


def _fulltext(paper: Paper) -> Optional[str]:
    if not paper.fulltext_path:
        return None
    from pathlib import Path
    p = Path(paper.fulltext_path)
    return p.read_text(errors="replace") if p.exists() else None


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _chunks(needs_llm: list, size: int = MAX_EVENTS_PER_CALL) -> list:
    return [needs_llm[i:i + size] for i in range(0, len(needs_llm), size)]


def _request_for(paper: Paper, needs_llm: list, names: dict,
                 counterparts: frozenset = frozenset(), context: str = "excerpts",
                 aliases: Optional[dict] = None,
                 effort: Optional[str] = None) -> Optional[dict]:
    if not needs_llm:
        return None
    event_ids = [eid for eid, _ in needs_llm if eid in names]
    if not event_ids:
        return None
    if context == "fulltext":
        text = _fulltext(paper) or f"Abstract\n\n{paper.abstract}"
        content = build_fulltext_prompt(
            paper.title, [(names[eid], (aliases or {}).get(eid, [])) for eid in event_ids],
            text[:MAX_FULLTEXT_CHARS], truncated=len(text) > MAX_FULLTEXT_CHARS)
    else:
        all_mentions = [m for _, ms in needs_llm for m in ms]
        content = build_user_prompt(
            paper.title, paper.abstract[:2000], [names[eid] for eid in event_ids],
            _excerpts(all_mentions, names, _fulltext(paper), counterparts))
    output_config = {"format": {"type": "json_schema", "schema": SCHEMA}}
    if effort:
        output_config["effort"] = effort    # model default (medium on Haiku 5.5) otherwise
    return {
        "model": CONFIG.classify_model,
        "max_tokens": (MAX_TOKENS_HIGH_EFFORT if effort in ("high", "xhigh", "max")
                       else MAX_TOKENS),
        "system": SYSTEM,
        "messages": [{"role": "user", "content": content}],
        "output_config": output_config,
    }


def _apply_response(s, run_id, paper_id, needs_llm, names, payload: dict) -> set:
    """Write the usages in one response; return the event ids written."""
    by_name = {names[eid]: eid for eid, _ in needs_llm if eid in names}
    seen = set()
    for item in payload.get("events", []):
        event_id = by_name.get(item.get("event_name"))
        if event_id is None or event_id in seen:
            continue
        seen.add(event_id)
        uses = normalise_uses([k for k in USES + ["qualitative"] if item.get(k)])
        s.add(EventUsage(
            paper_id=paper_id, event_id=event_id, analysis_run_id=run_id,
            engagement=deepest_use(uses), uses=uses, roles=item.get("roles", []),
            is_primary_subject=bool(item.get("is_primary_subject")),
            confidence=float(item.get("confidence", 0.0)),
            evidence=item.get("evidence", []), method="llm",
        ))
    return seen


def classify(limit: Optional[int] = None, dry_run: bool = False,
             use_batch: bool = True, require_fulltext: bool = True,
             only_arxiv: Optional[set] = None, scope: str = "all",
             context: str = "excerpts", effort: Optional[str] = None) -> ClassifyStats:
    if context not in CONTEXTS:
        raise ValueError(f"context must be one of {CONTEXTS}")
    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"effort must be one of {EFFORTS}")
    stats = ClassifyStats()
    with session() as s:
        plans, names = _plan(s, limit, require_fulltext=require_fulltext,
                             only_arxiv=only_arxiv)
        if require_fulltext and only_arxiv is None:
            run = s.scalar(select(AnalysisRun).where(
                AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
            with_mentions = s.scalar(select(func.count(func.distinct(Mention.paper_id)))
                                     .where(Mention.analysis_run_id == run.id)) or 0
            stats.papers_skipped_no_fulltext = max(with_mentions - len(plans), 0)
        counterparts = counterpart_names(s)
        papers = {p.id: p for p in s.scalars(
            select(Paper).where(Paper.id.in_([p[0] for p in plans]))).all()}

        run = AnalysisRun(
            stage="classify", pipeline_version=PIPELINE_VERSION,
            model_id=CONFIG.classify_model, prompt_sha256=PROMPT_SHA256,
            config={"batch": use_batch, "dry_run": dry_run, "scope": scope,
                    "context": context, "effort": effort},
        )
        s.add(run)
        s.flush()

        aliases: dict = defaultdict(list)
        if context == "fulltext":
            for eid, d, kind in s.execute(select(EventDesignation.event_id,
                                                 EventDesignation.designation,
                                                 EventDesignation.kind)):
                aliases[eid].append((d, kind))

        # Requests are built lazily: in full-text mode the whole corpus is over
        # a gigabyte of text, so each is materialised only when it is sent.
        requests = []
        for paper_id, rule_settled, needs_llm in plans:
            stats.papers_considered += 1
            stats.pairs_total += len(rule_settled) + len(needs_llm)
            stats.pairs_by_rule += len(rule_settled)
            stats.pairs_to_llm += len(needs_llm)
            if not dry_run:
                stats.usages_written += _write_rule_usages(s, run.id, paper_id, rule_settled)
            size = MAX_EVENTS_PER_CALL_FULLTEXT if context == "fulltext" else MAX_EVENTS_PER_CALL
            for chunk in _chunks(needs_llm, size):
                if not any(eid in names for eid, _ in chunk):
                    continue
                paper = papers[paper_id]
                requests.append((paper_id, chunk, functools.partial(
                    _request_for, paper, chunk, names, counterparts, context, aliases, effort)))
                if context == "fulltext" and paper.fulltext_path:
                    stats.est_input_tokens += min(
                        _file_size(paper.fulltext_path), MAX_FULLTEXT_CHARS) // 4

        stats.papers_called = len(requests)

        if dry_run or not requests:
            run.completed_at = datetime.utcnow()
            if dry_run:
                s.rollback()
            return stats

        # Models occasionally return fewer events than asked (1-15 of 498 on
        # the gold set), and single requests can error. Re-ask for exactly
        # what is missing, a couple of times, before reporting it.
        pending = requests
        for attempt in range(1 + MAX_RETRIES):
            results = (_run_batch(pending) if use_batch else _run_sync(pending))
            pending = []
            for paper_id, needs_llm, payload, err in results:
                written = set() if err else _apply_response(
                    s, run.id, paper_id, needs_llm, names, payload)
                stats.usages_written += len(written)
                missing = [(eid, ms) for eid, ms in needs_llm
                           if eid in names and eid not in written]
                if not missing:
                    continue
                if attempt < MAX_RETRIES:
                    stats.retried += len(missing)
                    pending.append((paper_id, missing, functools.partial(
                        _request_for, papers[paper_id], missing, names, counterparts,
                        context, aliases, effort)))
                else:
                    stats.errors.append(
                        f"{paper_id}: {len(missing)} events unclassified after "
                        f"{MAX_RETRIES} retries" + (f" ({err})" if err else ""))
            if not pending:
                break

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
    if message.stop_reason != "end_turn":
        raise ValueError(f"stop_reason={message.stop_reason} "
                         f"after {message.usage.output_tokens} output tokens")
    text = next((b.text for b in message.content if b.type == "text"), None)
    if text is None:
        raise ValueError("response has no text block")
    return text


def _run_sync(requests: list) -> list:
    client = _client()
    out = []
    for paper_id, needs_llm, make in requests:
        try:
            msg = client.messages.create(**make())
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

    # Split under the API's per-batch size limit, building each request only
    # when its batch is sent.
    batch_ids, pending, size = [], [], 0

    def flush():
        nonlocal pending, size
        if pending:
            batch_ids.append(client.messages.batches.create(requests=pending).id)
            # Logged so a run killed while polling can still be recovered:
            # results stay retrievable from the API by batch id.
            print(f"submitted batch {batch_ids[-1]} ({len(pending)} requests)", flush=True)
        pending, size = [], 0

    for i, (_, _, make) in enumerate(requests):
        params = make()
        n = len(json.dumps(params))
        if pending and (size + n > BATCH_MAX_BYTES or len(pending) >= BATCH_MAX_REQUESTS):
            flush()
        pending.append(Request(custom_id=f"p{i}", params=params))
        size += n
    flush()

    waiting = set(batch_ids)
    while waiting:
        for bid in list(waiting):
            if client.messages.batches.retrieve(bid).processing_status == "ended":
                waiting.discard(bid)
        if waiting:
            time.sleep(poll_seconds)

    out = []
    # Results arrive in arbitrary order -- key by custom_id, never by position.
    results = (r for bid in batch_ids for r in client.messages.batches.results(bid))
    for result in results:
        paper_id, needs_llm = index[result.custom_id]
        if result.result.type == "errored":
            err = result.result.error
            out.append((paper_id, needs_llm, {},
                        getattr(getattr(err, "error", err), "message", str(err))))
            continue
        if result.result.type != "succeeded":
            out.append((paper_id, needs_llm, {}, result.result.type))
            continue
        try:
            out.append((paper_id, needs_llm,
                        json.loads(_text_of(result.result.message)), None))
        except Exception as exc:  # noqa: BLE001
            out.append((paper_id, needs_llm, {}, str(exc)))
    return out
