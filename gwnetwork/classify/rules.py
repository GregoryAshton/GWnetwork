"""Rule-based engagement classification.

Originally the rules settled the large `cited_only` majority without an LLM
call. That stopped being safe when `qualitative` was split out of
`cited_only` (classify-0.3): a single introduction sentence can be either "GWs
have been observed [1]" or "GW150914 revealed ~30 Msun black holes exist in
binaries", and only reading tells them apart. So every pair with a mention now
goes to the LLM; the rules only attach a hint and evidence (DESIGN.md 7a).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import Engagement

# Sections where a mention implies the paper actually did something with the
# event, as opposed to nodding at it.
SUBSTANTIVE_SECTIONS = {"data", "methods", "results", "appendix"}
PASSING_SECTIONS = {"introduction", "front_matter", "discussion", "conclusion",
                    "references", "acknowledgements"}

_STRAIN = re.compile(
    r"\b(?:re-?analys|re-?process|strain data|time series|whiten|"
    r"glitch|deglitch|spectrogram|matched[- ]filter(?:ing)?|"
    r"raw data|GWOSC|open science center|frame files?)\b", re.I)

_POSTERIOR = re.compile(
    r"\b(?:posterior samples?|posterior distributions?|PE samples?|"
    r"samples? released|released samples?|reweight|data release|"
    r"zenodo|dcc|parameter estimation samples?)\b", re.I)

_PUBLISHED = re.compile(
    r"\b(?:median|credible interval|90%|reported (?:value|mass|spin)|"
    r"catalog(?:ue)? value|quoted|tabulated|as measured|inferred mass)\b", re.I)


@dataclass
class RuleVerdict:
    engagement: Optional[str]
    confidence: float
    evidence: list = field(default_factory=list)
    needs_llm: bool = True
    reason: str = ""


def classify_engagement(mentions: list) -> RuleVerdict:
    """Decide engagement for one (paper, event) pair from its mentions.

    `mentions` is a list of objects with .section and .sentence.
    """
    if not mentions:
        return RuleVerdict(None, 0.0, needs_llm=False, reason="no mentions")

    sections = {m.section for m in mentions}
    blob = " ".join(m.sentence for m in mentions)
    substantive = sections & SUBSTANTIVE_SECTIONS

    def ev(ms):
        return [{"quote": m.sentence, "section": m.section,
                 "char_start": getattr(m, "char_start", None),
                 "char_end": getattr(m, "char_end", None)} for m in ms[:3]]

    # Strongest signal first: the paper touched the data itself.
    if substantive and _STRAIN.search(blob):
        return RuleVerdict(Engagement.strain_analysis.value, 0.7, ev(mentions),
                           needs_llm=True, reason="strain language in a substantive section")

    if substantive and _POSTERIOR.search(blob):
        return RuleVerdict(Engagement.posterior_samples.value, 0.7, ev(mentions),
                           needs_llm=True, reason="posterior-sample language")

    # Few mentions in passing sections is *probably* cited_only, but it may be
    # qualitative, so it still goes to the LLM.
    if not substantive and len(mentions) <= 2 and sections <= PASSING_SECTIONS:
        return RuleVerdict(Engagement.cited_only.value, 0.6, ev(mentions),
                           needs_llm=True,
                           reason="few mentions, no substantive section")

    if substantive and _PUBLISHED.search(blob):
        return RuleVerdict(Engagement.published_results.value, 0.5, ev(mentions),
                           needs_llm=True, reason="quoted published values")

    return RuleVerdict(None, 0.0, ev(mentions), needs_llm=True,
                       reason="ambiguous; defer to LLM")
