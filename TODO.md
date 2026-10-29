# Known issues and follow-ups

Open items, roughly by area. Measured numbers are from the gold-set pilots and
the first full classify run (classify-0.4, 2026-10-21); see DESIGN.md §13.

## Extraction

- **Authors who name the wrong event** (1903.01998: "GW190521" for
  GW190521_074359) are invisible to the matcher; one is fixed in
  `data/mention_overrides.yaml`, others will only surface by hand-reading.
  Another (found 2026-10-28): 1710.05909, on GRB170817A and submitted
  2017-10-13, calls the NS-NS merger it ties to GRB170817A "GW170818" in
  five sentences ("the event has been named GW170818"): it means GW170817
  (GW170818 was not public until GWTC-1). It is GW170818's earliest
  mention, so the paper analysis dates GW170818's announcement 13 months
  early. Add an override for v2.
- **`LVT151012` is not a designation** for GW151012, so its 2016-2018
  literature (including the LVK O1 papers) is not linked and its first
  mention falls on an outside paper (1609.01668). Add the alias for v2.
- **Extraction recall is unmeasured.** The gold set was drawn from extracted
  pairs, so it cannot see events the matcher missed. Needs hand-read papers.

## Classification (Haiku 5.5, excerpts)

Overall engagement agreement 81% (kappa 0.75); 76% on person-labelled pairs.
The v1 run (2026-10-22, same configuration): 82.1% (kappa 0.76); 80.3% on
person-labelled pairs.

- **Run-to-run label instability is ~13%.** Two classify-0.4 runs with the same
  configuration (2026-10-21 and the v1 run of 2026-10-22, inputs differing
  only for ~120 re-fetched papers) agree on 30,634 of 35,207 shared pairs
  (87%), while each agrees with gold at ~81-82%. Report it as an uncertainty
  alongside the confusion-matrix correction; two runs can bound it.
- **Released-sample and strain uses undercounted** (recall 0.46 and ~0.85, both
  precision 1.00): collective statements ("we use the released samples for all
  events") name no event, so they are never in the excerpts. Full-text mode
  fixes recall (samples F1 0.91) but over-calls Q3; ~$60-80 per full run.
- **Used vs. quoted numbers (Q3) and qualitative vs. cited-only (Q4)** are the
  noisy boundaries; neither prompt changes nor higher effort moved them. A
  stronger model is the untested lever (Sonnet 5.5 full text ~$700-800 per run;
  or Sonnet only on Haiku's Q3/Q4 calls).
- **Roles under-assigned** (recall ~0.55).
- **Strain precision is not 1.00 in the wild.** Two `strain_analysis`
  labels found by the paper's uptake check (2026-10-28), for the gold-set
  error review: 2507.08778 / GW250114_082203 (a catalogue built from public
  low-latency alerts; quotes the alert's chirp mass and SNR, and O4c strain
  was not public) and 2507.17551 / GW231123_135430 (formation scenarios
  from population-informed spin priors; reads as posterior samples or
  published results). Both are excerpt-mode calls.

## Site

- **Pairs found only in reference titles** (e.g. 1802.04969 cites "...after
  GW170817" papers for the Vainshtein mechanism) still count towards event
  totals; show them separately or exclude by default. Derivable from
  `Mention.section`, no new label needed.
- **Show the full set of uses**, not only the derived deepest engagement.
- **Loudness chart: fixed-window comparison.** It now plots papers per year
  since publication, which still favours young events in their post-announcement
  burst and extrapolates the trend to GW250114's unusually high SNR. Counting
  papers within the first N years (young events hollow, out of the fit) would
  compare like with like. The corpus also lags the newest literature (ADS
  full-text ingestion), undercounting recent events.

## Paper prerequisites

For the meta-study paper (plan in `../GWnetwork_paper/PLAN.md`). Data is
frozen in versioned Zenodo releases (`gwn release export`, `release/README.md`);
each is exactly the data some analysis was computed from. Questions emerge
from the analysis; a label upgrade a question needs is a new release.

- **Pairs found only in reference titles** (4,589 of 35,316, 13%) are not
  noise to drop at extraction: 635 are classified deeper than cited-only,
  through the in-text citation (`citations.py`). Derive the flag from
  `Mention.section` in the analysis and report with and without them.
- **Literature cutoff 2026-06-01** is applied in the analysis, not by
  deleting papers. Since 2026-10-28 it applies to first appearance (first
  submission, or the revision that first names the event), not to the
  fetched version's date.
- **Release v2: extend the cutoff, probably to 2027-01-01** (noted
  2026-10-28). GWTC-5.0 (O4b, 147 events) went out 2026-05-26, five days
  before the v1 cutoff, so v1 has none of its follow-up; the O4c data
  release is due December 2026. Harvest and fetch well past the new cutoff
  before exporting, so papers submitted near it are indexed in ADS and
  fetched (full-text ingestion lags). See "Open decisions" in
  `../GWnetwork_paper/PLAN.md`.
- **Release v2: non-LVK catalogues, IAS and OGC** (for paper question A6,
  decided 2026-10-29; see "Decided" in `../GWnetwork_paper/PLAN.md`). v1
  has only GWOSC's IAS-O3a and GW151216 (`data/events/ias-o1.yaml`).
  - Ingest every event each catalogue lists as a detection by its own
    criterion, through the manual provider (or the planned 4-OGC/IAS-HM
    providers, DESIGN.md). Keep events also in LVK catalogues too, merged
    by GPS time through `event_merge` (human decision, never automatic);
    the paper selects those with no LVK record.
  - Record per catalogue its detection criterion as the paper states it
    (quote and section), and per event its reported p_astro, FAR (or
    IFAR) and table tier (main or marginal), so the paper can filter
    uniformly later. Criteria below are from memory and **must be
    checked against each paper**:

    | Catalogue | Paper | Criterion (to verify) |
    |---|---|---|
    | IAS O1 | 1902.10341, 1902.10331 | p_astro > 0.5 (GW151216) |
    | IAS O2 | 1904.07214; also 1910.09528 (GW170817A) | p_astro > 0.5 |
    | IAS O3a | 2201.02252 (GWOSC IAS-O3a) | p_astro > 0.5 |
    | IAS O3b | 2311.06061 | p_astro > 0.5 |
    | IAS O3 higher harmonics | 2312.06631 | p_astro > 0.5 |
    | 1-OGC | 1811.01921 | FAR threshold; check whether any new event |
    | 2-OGC | 1910.05331 | p_astro > 0.5 (e.g. GW151205, GW170121, GW170304, GW170727) |
    | 3-OGC | 2105.09151 | p_astro > 0.5 |
    | 4-OGC | 2112.06878 | p_astro > 0.5 |

  - Designations: add every name a catalogue used, including forms
    without the GW prefix and both short and `_hhmmss` forms, as aliases.
    Do not match bare dates (false positives); accept lower recall for
    events never given a GW name and record which those are.
  - Mention override: GW151216's first mention is 1611.08052 (DES
    kilonova search), which writes "GW151216" for another trigger and
    dates its announcement 2017-03-10 instead of 2019-02-27 (1902.10331).
    Same kind of fix as GW170818's.
  - Then re-run extraction and classification for the new names over the
    whole corpus.
- ~~**Release v1**: tag `v1.0-paper`, export, deposit on Zenodo.~~ Done
  2026-10-23: DOI 10.5281/zenodo.23603658, published 2026-10-26 and linked
  from the site's front page.
