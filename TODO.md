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

For the meta-study paper (plan in `../GWnetwork_paper/PLAN.md`). The Zenodo
snapshot must be exactly the data the paper is computed from, so everything
here happens before the freeze. Which data fixes are needed depends on the
final question list.

- **`refetch --verify`**: rebuild `data/fulltext/` from arXiv at the recorded
  `fulltext_version` and report per-paper hash matches (hash raw bytes: some
  PDF texts contain `\r`), so upstream and converter changes are visible.
- **Lock the environment at the freeze.** `pypdf` is pinned and the PDF
  converter string records fontTools and the XObject limit; still commit a
  full `pip freeze` and the Python version (3.9) with the tag.
- **Slim export for Zenodo**: current runs only (events, papers, mentions with
  sentences, usages with evidence quotes). The full 571 MB multi-run database
  is optional alongside it. Full text is not deposited (arXiv licensing).
- **Pairs found only in reference titles** (4,589 of 35,316, 13%) are not
  noise to drop at extraction: 635 are classified deeper than cited-only,
  through the in-text citation (`citations.py`). Derive the flag from
  `Mention.section` in the analysis and report with and without them.
- **Literature cutoff 2026-06-01** is applied in the analysis, not by
  deleting papers.
- **Detector participation per event** (for the Virgo attribution study):
  check whether the stored GWOSC records carry it; otherwise add it.
- **ADS reference lists per paper** (for the open-data citation study), as a
  pipeline step writing to the database so the numbers are reproducible from
  the deposit.
- **Tag the frozen state** (e.g. `v1.0-paper`) and deposit on Zenodo.
