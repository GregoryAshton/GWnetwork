# Known issues and follow-ups

Open items, roughly by area. Measured numbers are from the gold-set pilots and
the first full classify run (classify-0.4, 2026-10-21); see DESIGN.md §13.

## Extraction

- **Authors who name the wrong event** (1903.01998: "GW190521" for
  GW190521_074359) are invisible to the matcher; one is fixed in
  `data/mention_overrides.yaml`, others will only surface by hand-reading.
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
- ~~**Release v1**: tag `v1.0-paper`, export, deposit on Zenodo.~~ Done
  2026-10-23: DOI 10.5281/zenodo.23603658, published 2026-10-26 and linked
  from the site's front page.
