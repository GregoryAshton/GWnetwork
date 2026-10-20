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
  strain_analysis    Analyses the strain data for this event. The original
                     discovery or catalogue analysis and any independent
                     analysis count alike.

ROLES -- what THIS event is used FOR in the paper. Zero or more of:
{chr(10).join('  ' + r for r in ROLES)}

Roles belong to the event, not to the paper: judge each event separately. A
paper following up GW170817 gives GW170817 counterpart_followup, but a
GW150914 it mentions in passing gets no roles. An event that is only
cited_only normally has no roles.
  counterpart_followup  The paper observes, or searches for, this event's
                        counterpart (electromagnetic, neutrino, ...),
                        including non-detections. Joint interpretation of
                        multimessenger results without new observations is
                        multimessenger.

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
- single_event vs population: ask whether the paper produces a result about
  this event on its own. single_event if it reports something specific to the
  event (its parameters, a test or measurement on it alone) -- so a catalogue
  that estimates each event's parameters gives each event single_event.
  population if the event is one input to a combined inference (rates, mass or
  spin distributions, a stacked or hierarchical test, a combined H0). Both if
  the paper reports per-event results and also combines them.
- When torn between cited_only and qualitative, choose qualitative.
- An excerpt whose heading says it "names the counterpart" mentions the
  event's electromagnetic counterpart (e.g. GRB 170817A, AT2017gfo), not its
  GW signal. Engagement measures use of the GW results: a paper that only
  discusses or analyses the counterpart is cited_only or qualitative for the
  event, unless other excerpts show it using GW results. If it observes the
  counterpart itself, give the role counterpart_followup.
- Using a released sky map or alert localisation (e.g. to plan or interpret
  follow-up observations) is published_results, not posterior_samples.
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
