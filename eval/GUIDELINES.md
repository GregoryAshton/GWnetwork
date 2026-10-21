# Gold-set labelling guide

Version 3, frozen 2026-10-21 (classifier `classify-0.4`). Changes are listed
at the end. The usage questions
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
| Counterpart only, discussed or interpreted (2305.12974 interprets the kilonova) | Q1-Q3 no; Q4 yes; role multimessenger |
| Counterpart data only as an input, event not discussed (2605.25201 scales yields by the AT2017gfo Sr measurement; 2104.09727 plots the AT2017gfo light curve as a reference) | all no -> cited_only |
| Excluded from an analysis because of a property: low significance (2105.02879 GW190719_215514), an outlier (2404.03166 GW190814) | Q4 yes |
| Named only as the occasion for something else: instruments that followed it up (2105.12268 GW190814), a sensitivity time marker (2511.19586) | all no -> cited_only |
| A one-line property in a list of events: "weaker signal, no EM counterpart" (2110.08281 GW190425) | Q4 yes |
| Only in a reference title, even when the cited work is about GW findings (2402.10755 GW150914, Lorentz-violation constraints) | all no -> cited_only |
| A bound used without restating its numbers: "within the GW170817 constraint" (2406.05267); EOSs chosen to satisfy GW170817's tidal limits (2304.12356) | Q3 yes |
| A bound another paper derived from the event, compared with the paper's own (2407.08825, f(R)) | Q3 yes |
| Released posterior plotted against forecasts and its stated uncertainty compared (2608.11673 GW231123) | Q2 and Q3 yes |

Mixed cases: answer each question on its own. A paper that quotes "~1.4 Msun"
in the introduction *and* uses the PE samples later is Q2 yes; the rounded
value adds nothing once a data product is used.

## Roles: what this event is used for

Zero or more of: `single_event`, `population`, `multimessenger`, `counterpart_followup`, `test_of_gr`, `cosmology`, `lensing`, `formation_channels`, `waveform_systematics`, `dense_matter_eos`, `exotic_compact_object`, `detector_characterisation`, `methods_demo`, `review`, `forecast`.

Roles belong to the event, not the paper: a GW170817 follow-up
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

**Version 3** (after the two-model labelling of rows 101-234 and adjudication):
- Counterpart-only: Q4 yes if the counterpart or event is discussed or
  interpreted; cited_only if counterpart data is only taken as an input.
- Q4 boundaries from adjudication: exclusion for a property and a one-line
  property in a list are qualitative; naming an event only as the occasion
  for something else, or only in reference titles, is cited_only.
- Q3: a bound used without restating its numbers, or a third-party bound
  derived from the event, counts when used.
- Full role list added. These rules are also in the classifier prompt.
