from __future__ import annotations

import hashlib

from ..models import ROLES

SYSTEM = f"""You classify how physics papers use gravitational-wave events.

For each event listed, decide two independent things.

ENGAGEMENT -- how deeply the paper touches the event. Exactly one of:
  cited_only         Cited in passing only. The event is named or cited as
                     background, or as an example that detections happen, and
                     the paper draws on nothing it showed.
                     e.g. "Gravitational waves have been observed [1]."
  qualitative        Uses what the event showed, in words: its findings,
                     implications or properties are discussed, or used to
                     motivate, compare with, interpret or argue for the
                     paper's own work. Quoting a value as context still counts
                     here, as long as it does not feed the paper's own
                     calculation. Read this broadly: anything beyond a bare
                     in-passing citation is at least qualitative.
                     e.g. "The detection revealed that black holes of ~30 solar
                     masses exist in binaries."
  published_results  The event's published parameters (masses, spins,
                     distance, sky position, ...) are inputs to the paper's own
                     calculation, analysis, figure or table.
  posterior_samples  Uses released posterior/PE samples for this event.
  strain_reanalysis  Reanalyses the strain data for this event.

ROLES -- what the event is used FOR. Zero or more of:
{chr(10).join('  ' + r for r in ROLES)}

These are independent. A paper can perform a population-level test of general
relativity: engagement=posterior_samples, roles=[population, test_of_gr].

Rules:
- Judge only from the text given. Do not use outside knowledge of the paper.
- Every event needs at least one VERBATIM quote from the supplied text as
  evidence. Never paraphrase a quote. If you cannot quote it, the engagement is
  cited_only.
- is_primary_subject is true only when the event is a main subject of the paper,
  not merely one of many in a sample.
- Confidence reflects the text's clarity, not how plausible the claim feels.
- A paper analysing 90 events in a population is population, not single_event.
- When torn between cited_only and qualitative, choose qualitative.
- An excerpt headed "cites reference [N], which names EVENT" is a sentence where
  the paper cites a reference whose title or entry names the event. Judge the
  event from how that sentence uses the reference.
"""


def build_user_prompt(title: str, abstract: str, event_names: list, excerpts: list) -> str:
    """excerpts: list of {section, text} around mentions."""
    parts = [f"TITLE: {title}", "", f"ABSTRACT: {abstract}", "",
             "EVENTS TO CLASSIFY: " + ", ".join(event_names), "",
             "EXCERPTS FROM THE PAPER:"]
    for e in excerpts:
        parts.append(f"\n[{e['section']}] {e['text']}")
    return "\n".join(parts)


PROMPT_SHA256 = hashlib.sha256(SYSTEM.encode()).hexdigest()
