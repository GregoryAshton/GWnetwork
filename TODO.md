# Known issues and follow-ups

Open items, roughly by area. Measured numbers are from the gold-set pilots and
the full classify-0.4 runs (2026-10-21 to 2026-11-13); see DESIGN.md §13.

## Extraction

- **Authors who name the wrong event** (1903.01998: "GW190521" for
  GW190521_074359; 1710.05909: "GW170818" for GW170817; 1611.08052:
  "GW151216" for GW151226) are invisible to the matcher. Known cases are
  fixed in `data/mention_overrides.yaml`; others will only surface by
  hand-reading.
- **Extraction recall is unmeasured.** The gold set was drawn from extracted
  pairs, so it cannot see events the matcher missed. Needs hand-read papers.

## Classification (Haiku 5.5, excerpts)

Gold agreement (engagement) over five full classify-0.4 runs with the same
configuration: 80.7% (2026-10-21), 82.1% (v1, 2026-10-22), 75.6%
(2026-11-12 13:45), 81.2% (2026-11-12 20:15), 77.4% (2026-11-13 12:04, v2,
current); mean 79.4% +/- 2.7. Person-labelled pairs: 75.4 / 80.3 / 72.4 /
75.6 / 72.4, mean 75.2% +/- 3.2. The 13:45 run is a low draw, not drift:
all 31 of its gold flips are in papers whose inputs did not change.

- **Run-to-run label instability is ~13%.** Any two of the five runs agree
  on 86.7-88.3% of shared pairs (~35,200). The spread in gold agreement
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
  totals; show them separately on the site rather than dropping them (many
  are deeper than cited-only; see the paper item below). Derivable from
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
- **Literature cutoff 2026-10-01** (v2; v1 used 2026-06-01) is applied in
  the analysis, not by deleting papers. Since 2026-10-28 it applies to first appearance (first
  submission, or the revision that first names the event), not to the
  fetched version's date.
- **Release v2: harvest done 2026-11-13** (755 new papers, submitted up
  to 2026-11-11; extraction and classification re-run). Papers on the 161
  GWTC-5.0-only events: 254 in v1, 404 now. Analysis cutoff set to
  2026-10-01, six weeks before the harvest, since ADS full-text indexing
  lags by weeks (`../GWnetwork_paper/PLAN.md`). Still to do: deposit v2
  on Zenodo and re-export the site.
- **Super-gold set: expert labels from collaborators** (noted 2026-11-14,
  for the paper's error correction, `../GWnetwork_paper/analysis/
  correction.py` and PLAN "Gold-set error correction"). The correction
  imputes true labels from P(true | predicted) on the gold set; its bands
  are wide where gold columns are thin (only 9 independent gold pairs
  labelled `posterior_samples`), and 107 of the 234 current labels are
  model consensus, which agrees with the classifier more than people do
  (83% against 72%; partly because consensus rows are easier). Collect
  person-only labels in a new gold version, shipped in a new release (v3).
  - **Labellers:** collaborators, each briefed on `eval/GUIDELINES.md`
    (frozen version; any rule change is a new guide version) and the
    labelling UI. Labels by people only; a model may pre-highlight
    evidence but not propose labels.
  - **Sample, stratified (valid for P(true | predicted)):**
    - by predicted engagement: 40-50 independent pairs in each class, so
      `posterior_samples` and `published_results` are no longer thin;
    - by era: at least a third of papers submitted before 2020 (agreement
      there is 69% on 39 pairs against 81% after; the correction's
      posterior-sample share for 2016-17 looks too high);
    - catalogue and population papers (many events, under-sampled at 8
      pairs per paper; 35% of corpus pairs), LVK papers included as their
      own stratum (they err differently: strain under-called for the events
      a catalogue analyses);
    - record each paper's stratum and sampling probability in the gold
      file, so the analysis can weight exactly.
  - **Size:** about 80-120 papers, all pairs in each (no 8-pair cap, or
    record the cap). At 10-15 minutes a paper, a few hours each for 5-8
    people.
  - **Double-label about a third** of papers to measure agreement between
    people (the ceiling for the classifier's 77%).
  - **Extraction recall in the same pass:** each labeller lists every
    event the paper names (including forms the matcher may miss: bare
    dates, typos, counterpart names), from the full text, before seeing
    the extracted pairs. The only way to measure recall.
  - Keep the existing gold rows (with `labelled_by`) and add the new ones
    with a `gold_version`; the analysis selects by version.
  - **Tooling built 2026-11-18** (`gwn eval supergold draw|assign|site|
    import|report`, `eval/supergold/README.md`): static site + Apps Script
    Sheet, no roles, no model output shown, 3-level confidence, recall
    listing on the uniform slice only, opt-in acknowledgements kept apart
    from answers (no token-to-person record). New labels go to
    `eval/supergold/labels.jsonl` (multi-reviewer), not gold.jsonl. Trial
    draw with defaults (2026-11-20, for ~10 labellers): 181 papers, 642
    pairs, 20 per stratum, 50 uniform. Still to do: draw for real, deploy,
    pilot, send.
- **Release v3: same cutoff, waits for the full super-gold set** (decided
  2026-11-20, `../GWnetwork_paper/PLAN.md` "Deposit contents"). No new
  harvest: the paper keeps v2's literature cutoff (2026-10-01) until Greg
  says otherwise. Cut v3 once the super-gold labels are all in, and
  include:
  - the super-gold labels (above), with `gold_version`;
  - the per-paper Part B flags: B1 instrument attribution and B2 GWOSC
    citation, including the strain-text flags the paper repo now derives
    locally (`data/gwosc_text_flags-v2.csv`);
  - the full multi-run database (all classify-0.4 runs, not just the one
    v2 ships), so run-to-run instability is reproducible;
  - quarterly arXiv submission counts (below).
  Part B stays rates and trends in the paper; the flags are data, not a
  list in the text.
- **arXiv volume per quarter, to normalise the corpus** (asked
  2026-11-25, for v3). The paper wants to compare corpus growth with
  arXiv's: over the whole decade (are event papers a growing share of
  the field?), for the recent rise (2025Q3 on; is it a general
  productivity boom, e.g. AI-assisted writing?), and in the reach and
  open-data figures. Fetch submission counts per calendar quarter,
  2015Q1 to 2026Q3 (the cutoff is 2026-10-01), from the arXiv API
  (`search_query=cat:<cat> AND submittedDate:[.. TO ..]`,
  `max_results=0`, read `opensearch:totalResults`; 3 s between calls):
  - astro-ph (all subcategories), gr-qc, hep-ph, hep-th, nucl-th;
  - all of arXiv, for the overall trend.
  Record both cross-list-inclusive counts (`cat:` matches any listed
  category, so the categories overlap) and, if the API or the OAI-PMH
  metadata allows, primary-category counts, since the corpus's reach
  figure uses primary categories. Also counts of the union astro-ph OR
  gr-qc, deduplicated, as the natural denominator for the corpus. Store
  as a table with the query, fetch date and API used, so the numbers are
  reproducible as of the fetch; check a couple of quarters against
  arxiv.org/stats. A first look in the paper repo (2026-11-25) found the
  2025Q3 rise is entirely papers naming O4 events (13 to 115 a quarter;
  papers naming no O4 event flat at ~310), so the comparison is for
  context, not to rescue that claim.
