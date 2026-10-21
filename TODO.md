# Known issues and follow-ups

Open items, roughly by area. Measured numbers are from the gold-set pilots and
the first full classify run (classify-0.4, 2026-10-21); see DESIGN.md §13.

## Extraction

- **Bare-number marginal candidates match coincidental numbers.** ~20 GWOSC
  marginal candidates have no `GW` prefix (`170405`, `170208`, `161202`, ...).
  Many matches are genuine (GWTC-1's trigger tables), but a bare six-digit
  number also matches dates and identifiers: `170405` is "mentioned" by 46
  papers, including ones from 1999 and 2004, and these account for all 23
  paper-event pairs still dated before their event. Options: require trigger
  context nearby (GPS/UTC time, FAR, "trigger", "candidate"), or drop matches
  in papers whose latest version predates the candidate.
- **Short name resolved to the wrong event.** 1903.01998 writes "GW190521" with
  masses of GW190521_074359; the longest-match/ambiguity handling missed it.
- **Extraction recall is unmeasured.** The gold set was drawn from extracted
  pairs, so it cannot see events the matcher missed. Needs hand-read papers.
- **New papers need version dates.** `gwn corpus update-versions` has to be run
  after `gwn corpus fetch`; fold it into the fetch step.

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
