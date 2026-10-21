# Gold-set labelling guide

Version 2, frozen 2026-10-21 (classifier `classify-0.4`). Changes from
version 1 are listed at the end. The usage questions
below are generated from `gwnetwork/classify/prompts.py`, the same text the
classifier gets; `tests/test_evalset.py` fails if they drift apart.

**Do not change these rules mid-labelling.** When a case doesn't fit, tick
*unsure* and keep going; unsure rows are collected and decided together, and any
rule change gets a new guide version.

## The unit

One row is one **(paper, event)** pair. Judge only how *this paper* uses *this
event* -- not the paper's topic, and not how it uses other events.

Read from the full text: every mention is shown with context. Two things are
flagged for you:

- **Reference-list mentions** (the event is in a cited paper's *title*): the
  box underneath shows where the paper cites that reference. Judge from those
  sentences, not from the title.
- **Counterpart names** (GRB 170817A, AT2017gfo, SSS17a; amber highlight): these
  name the EM counterpart, not the GW signal. The usage questions are about the
  **GW results**.

## Usage: answer every question

Q1-Q3 are independent -- tick every one that applies. A paper can analyse the
strain *and* compare with the released samples. Q4 only matters when Q1-Q3 are
all "no".

**Q1. Does the paper analyse the strain data for this event?** (`strain_analysis`)
Its own analysis of the detector data. The original discovery or catalogue analysis and any independent analysis count alike.

**Q2. Does it use released samples for this event?** (`posterior_samples`)
Released posterior/PE samples, or sample sets conditioned on the event (e.g. EOS samples informed by GW170817), used in its own analysis.

**Q3. Does it use the event's published measurements with their uncertainties?** (`published_results`)
Credible intervals, ranges or bounds for masses, spins, distance, sky position, H0, ... that the paper USES: as inputs to a calculation, figure or table, or to compare its own results against, even in the text (e.g. "exceeds the 1.61-2.52 solar mass primary of GW190425"). Merely quoting them as background (e.g. listing an H0 value among other measurements in the introduction) does not count. A released sky map or alert localisation counts here.

**Q4. If none of those: does it use anything the event showed?** (`qualitative` if yes, `cited_only` if no)
Its findings, implications or properties are discussed, or used to motivate, compare with, interpret or argue for the paper's own work; a central or rounded value ("~1.4 solar masses", "black holes of ~30 solar masses exist in binaries") counts. Read this broadly. If not -- the event is only named or cited as background, as in "gravitational waves have been observed [1]" -- it is cited_only.

### Worked cases

| Case | Answer |
|---|---|
| GWTC-2 (2010.14527) estimates each event's parameters | Q1 yes, for every event in it |
| 2006.12611 reweights "the GW170817-informed spectral EOS samples" | Q2 yes for GW170817 |
| 2006.12611: "exceeds the 1.61-2.52 Msun primary component of GW190425" | Q3 yes: published numbers with uncertainty, in a comparison |
| 2011.13503: GW190814 is "the merger of ~23 Msun BH and a ~2.6 Msun compact object" | Q1-Q3 no; Q4 yes (rounded values) |
| 2006.12611 compares its H0 with GW170814's H0 = 75 +40/-32 | Q3 yes: an interval used in a comparison |
| 1811.02376 lists the GW170817 standard-siren H0 +/- error among other H0 measurements in its introduction | Q3 no (quoted as background, not used); Q4 yes |
| 1808.00632 puts GW170817's GW-speed bound into its own constraint equation | Q3 yes |
| 1806.08365 builds Gaussian posteriors from published chirp-mass/redshift intervals | Q3 yes, Q2 no: published intervals, not released samples |
| 2011.13503: GW170817 "constrained the maximum NS mass to <~2.3 Msun" (introduction) | Q3 no (rounded bound, background); Q4 yes |
| Follow-up observations planned from the alert sky map | Q3 yes |
| 1502.04979 cites a GW150914 paper only for "a (stochastic) GW background [84]" | all no -> cited_only |
| 1802.04969: GW170817 only in titles of papers cited for the Vainshtein mechanism | all no -> cited_only |
| 1711.03553: "formation scenarios proposed to explain GW150914" motivate its study | Q4 yes (qualitative) -- motivation from what the event showed counts |
| 1902.02265: GW170817's speed constraint "rules out" modified-gravity theories (no numbers) | Q4 yes |
| 2105.06384 (lensing): per-event magnifications from released samples, plus joint PE / microlensing reanalyses of the strain | Q1 and Q2 yes; role single_event |
| A paper analysing only GRB 170817A data, never using GW results | Q1-Q3 no; Q4 by how it uses what GW170817 showed |

Mixed cases: answer each question on its own. A paper that quotes "~1.4 Msun"
in the introduction *and* uses the PE samples later is Q2 yes; the rounded
value adds nothing once a data product is used.

## Roles: what this event is used for

Zero or more. Roles belong to the event, not the paper: a GW170817 follow-up
paper gives GW170817 `counterpart_followup`, but a GW150914 cited in its
introduction gets no roles. A cited-in-passing event normally has no roles; a
qualitative one often does (e.g. qualitative + `formation_channels`).

- **single_event vs population** -- does the paper produce a result about this
  event *on its own*? `single_event` if it reports something specific to it
  (its parameters, a test on it alone; a catalogue's per-event PE counts).
  `population` if the event is one input to a combined inference (rates, mass
  or spin distributions, stacked or hierarchical tests, a combined H0). Both
  if it does both. A per-event result counts even when the paper does the
  same for many events (a lensing magnification for each event, say).
- **counterpart_followup vs multimessenger** -- `counterpart_followup` if the
  paper observes or searches for this event's counterpart (EM, neutrino),
  non-detections included. `multimessenger` for joint interpretation without
  new observations. A follow-up paper often has both.
- An event used only as a comparison point usually gets the role of what the
  comparison is about (e.g. GW170817 in an EOS argument -> `dense_matter_eos`).

## Unsure

Tick *unsure* when the text genuinely supports two answers under these rules --
not when the paper is merely hard to read. Unsure rows are scored separately and
reviewed together before the next guide version.

## Changes

**Version 2** (after the first inter-annotator comparison, 65% -> 87% agreement
once the guide was applied consistently):
- Q3 requires the numbers to be *used* (input or comparison); quoting them as
  background does not count.
- Q4 "read broadly" re-affirmed with examples: motivation drawn from what the
  event showed is qualitative.
- single_event: a per-event result counts in a multi-event paper.
- Fixed the GW170814 worked case (the paper does quote its H0 interval).
