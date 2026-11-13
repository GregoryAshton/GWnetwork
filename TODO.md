# Known issues and follow-ups

Open items, roughly by area. Measured numbers are from the gold-set pilots and
the full classify-0.4 runs (2026-10-21 to 2026-11-12); see DESIGN.md §13.

## Extraction

- **Authors who name the wrong event** (1903.01998: "GW190521" for
  GW190521_074359; 1710.05909: "GW170818" for GW170817; 1611.08052:
  "GW151216" for GW151226) are invisible to the matcher. Known cases are
  fixed in `data/mention_overrides.yaml`; others will only surface by
  hand-reading.
- **Extraction recall is unmeasured.** The gold set was drawn from extracted
  pairs, so it cannot see events the matcher missed. Needs hand-read papers.

## Classification (Haiku 5.5, excerpts)

Gold agreement (engagement) over four full classify-0.4 runs with the same
configuration: 80.7% (2026-10-21), 82.1% (v1, 2026-10-22), 75.6%
(2026-11-12 13:45), 81.2% (2026-11-12 20:15, current for v2); mean
79.9% +/- 2.9. Person-labelled pairs: 75.4 / 80.3 / 72.4 / 75.6, mean
75.9% +/- 3.3. The 13:45 run is a low draw, not drift: all 31 of its gold
flips are in papers whose inputs did not change.

- **Run-to-run label instability is ~13%.** Any two of the four runs agree
  on 86.7-88.1% of shared pairs (~35,200). The spread in gold agreement
  (~3 points) is as large as the binomial error on 234 gold pairs, so quote
  the mean and spread of the runs, never one run's score, and never promote
  a run because it scored best on gold (selection on the eval set). Policy:
  the latest full run is current.
- **Agreement across runs is a usable uncertainty score; self-reported
  confidence is not.** 77.8% of pairs get the same label in all four runs
  and those are 88% correct on gold; split pairs' modal label is 52%
  correct. Majority vote scores 80.7% (no better than one run, but less
  variable). `EventUsage.confidence` separates right from wrong only weakly
  (AUROC 0.63). Next for the paper: prediction-powered inference
  (Angelopoulos et al. 2023) for headline fractions, using the gold set
  (weighted for its per-paper sampling) and, optionally, conformal label
  sets scored by run agreement. `gwn eval runs` reproduces all of the
  above.
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
- **Release v2: non-LVK catalogues, IAS and OGC** (paper question A6).
  Ingested, extracted and classified 2026-11-12 (35 new events; criteria,
  merge decisions and the per-catalogue table in `data/catalogues/README.md`).
  Remaining:
  - Recheck "GW170817A" mentions in papers added by the cutoff extension
    (most mean GW170817 and need a `mention_overrides.yaml` entry).
  - Note for the paper: `analysis/data.py` sets `lvk_catalogued` from
    GWTC-* and O*_Discovery_Papers only, so 200114_020818 (O3 IMBH
    marginal only; IAS-HM's GW200114_020818) counts as not LVK there,
    while PLAN's A6 definition (any LVK record, confident or marginal)
    excludes it.
  - Re-export the site with v2 after the cutoff extension.
