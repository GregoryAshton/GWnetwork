from __future__ import annotations

import hashlib

from ..models import ROLES

# The usage questions, shared verbatim by the classifier prompt, the labelling
# UI and eval/GUIDELINES.md so that humans and model answer the same thing.
# The three data-product questions are independent: answer every one.
DATA_QUESTIONS = [
    ("strain_analysis",
     "Does the paper analyse the strain data for this event?",
     "Its own analysis of the detector data. The original discovery or "
     "catalogue analysis and any independent analysis count alike."),
    ("posterior_samples",
     "Does it use released samples for this event?",
     "Released posterior/PE samples, or sample sets conditioned on the event "
     "(e.g. EOS samples informed by GW170817), used in its own analysis."),
    ("published_results",
     "Does it use the event's published measurements with their uncertainties?",
     "Credible intervals, ranges or bounds for masses, spins, distance, sky "
     "position, H0, ... that the paper USES: as inputs to a calculation, figure "
     "or table, or to compare its own results against, even in the text (e.g. "
     "\"exceeds the 1.61-2.52 solar mass primary of GW190425\"). Merely quoting "
     "them as background (e.g. listing an H0 value among other measurements in "
     "the introduction) does not count. A released sky map or alert "
     "localisation counts here."),
]
FALLBACK_QUESTION = (
    "qualitative",
    "If none of those: does it use anything the event showed?",
    "Its findings, implications or properties are discussed, or used to "
    "motivate, compare with, interpret or argue for the paper's own work; a "
    "central or rounded value (\"~1.4 solar masses\", \"black holes of ~30 "
    "solar masses exist in binaries\") counts. Read this broadly. If not -- the "
    "event is only named or cited as background, as in \"gravitational waves "
    "have been observed [1]\" -- it is cited_only.")


def _wrap(text: str, indent: str) -> str:
    import textwrap
    return textwrap.fill(text, 78, initial_indent=indent, subsequent_indent=indent)


def _questions_block() -> str:
    lines = []
    for i, (key, q, detail) in enumerate(DATA_QUESTIONS, 1):
        lines += [f"  Q{i}. {key}: {q}", _wrap(detail, "      ")]
    key, q, detail = FALLBACK_QUESTION
    lines += [f"  Q4. {key}: {q}", _wrap(detail, "      ")]
    return "\n".join(lines)


SYSTEM = f"""You classify how physics papers use gravitational-wave events.

For each event listed, answer the usage questions and choose roles.

USAGE -- answer Q1-Q3 independently with true/false; a paper can do several
(e.g. analyse the strain AND compare with the released samples). Q4 only
matters when Q1-Q3 are all false.
{_questions_block()}

ROLES -- what THIS event is used FOR in the paper. Zero or more of:
{chr(10).join('  ' + r for r in ROLES)}

Roles belong to the event, not to the paper: judge each event separately. A
paper following up GW170817 gives GW170817 counterpart_followup, but a
GW150914 it mentions in passing gets no roles. An event that is only cited in
passing normally has no roles.
  counterpart_followup  The paper observes, or searches for, this event's
                        counterpart (electromagnetic, neutrino, ...),
                        including non-detections. Joint interpretation of
                        multimessenger results without new observations is
                        multimessenger.
  single_event vs population: does the paper produce a result about this
                        event on its own? single_event if it reports something
                        specific to the event (its parameters, a test or
                        measurement on it alone), even when the paper does the
                        same for many events -- a catalogue that estimates
                        each event's parameters gives each event single_event.
                        population if the event is one input to a combined
                        inference (rates, mass or spin distributions, a stacked
                        or hierarchical test, a combined H0). Both if it does
                        both.

Usage and roles are independent. A population-level test of general
relativity using released samples: posterior_samples=true,
roles=[population, test_of_gr].

Rules:
- Judge only from the text given. Do not use outside knowledge of the paper.
- Every event needs at least one VERBATIM quote from the supplied text as
  evidence. Never paraphrase a quote. Answer true only for uses you can quote;
  if you cannot quote anything beyond a passing mention, all answers are false.
- is_primary_subject is true only when the event is a main subject of the paper,
  not merely one of many in a sample.
- Confidence reflects the text's clarity, not how plausible the claim feels.
- When torn on Q4, answer true.
- An excerpt whose heading says it "names the counterpart" mentions the
  event's electromagnetic counterpart (e.g. GRB 170817A, AT2017gfo), not its
  GW signal. The usage questions are about the GW results: a paper that only
  discusses or analyses the counterpart answers Q1-Q3 false for the event,
  unless other excerpts show it using GW results. Q4 is then true if it
  discusses or interprets the counterpart or the event, but false if it only
  takes counterpart data as an input (e.g. a kilonova abundance used to scale
  yields) without discussing the event. If it observes the counterpart
  itself, give the role counterpart_followup.
- An excerpt headed "cites reference [N], which names EVENT" is a sentence where
  the paper cites a reference whose title or entry names the event. Judge the
  event from how that sentence uses the reference. If the event appears only in
  such titles, answer Q4 false even when the cited work is about GW findings.
- Boundary cases for Q4: an event excluded from an analysis because of a
  property (low significance, an outlier) is true; an event named only as the
  occasion for something else (instruments that followed it up, a date or
  sensitivity marker) is false; a one-line property in a list of events
  ("weaker signal, no EM counterpart") is true.
- Boundary cases for Q3: a published bound used without restating its numbers
  ("lies within the GW170817 constraint", equations of state chosen to satisfy
  GW170817's tidal limits) is true, as is a bound another paper derived from
  the event when it is used or compared. Plotting released samples is Q2; also
  comparing against their stated uncertainty makes Q3 true too.
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
