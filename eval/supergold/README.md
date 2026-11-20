# Super-gold set (gold version 3)

Person-only labels from collaborators, collected from a static website and
stored under anonymous reviewer tokens. It is a probability sample of the corpus,
so it can calibrate the paper's error correction (P(true | predicted)) and
answer later questions by weighting. Design and rationale: the docstring of
`gwnetwork/supergold.py`.

Choices made for this round (2026-11-18):

- **Usage only, no roles.** The correction uses `uses`/`engagement`. Role
  recall can still be quoted from `eval/gold.jsonl`.
- **No model output shown.** Labellers never see the classifier's answer,
  because anchoring on it would inflate exactly the agreement being measured.
- **Confidence** per pair: `clear` / `probably` / `ambiguous`. `ambiguous` is
  the old `unsure` and is written as `unsure: true`.
- **Guide 3.1, frozen.** Any rule change is a new guide version.

## Running it

```bash
gwn eval supergold draw                 # sample.json: ~180 papers, strata, pi, frozen predictions
gwn eval supergold assign 50            # assignments.json: tokens and queues (spares cost nothing)
gwn eval supergold site --out ../gwn-labelling            # test mode first
gwn eval supergold site --out ../gwn-labelling --endpoint "https://script.google.com/macros/s/…/exec"
```

1. **Sheet and endpoint.** Create a Google Sheet. In *Extensions > Apps
   Script*, paste `apps_script.gs`, then *Deploy > New deployment > Web app*
   with *Execute as: Me* and *Who has access: Anyone*. Pass the `/exec` URL as
   `--endpoint`.
2. **Host.** Push the site directory to its own GitHub repository with
   Pages enabled. Never use `docs/`. The pages are `noindex` and robots
   disallows everything, but the site is public to anyone holding the URL.
   Pages show mention snippets and arXiv links only, never full text.
3. **Pilot.** Label about 10 papers yourself, plus one colleague, using two
   of the tokens. Then download the Sheet as CSV and run
   `gwn eval supergold import sheet.csv && gwn eval supergold report`.
4. **Send.** Give tokens out **in the order `assign` printed them**: the
   first is yours (pilot). Queues start at spread-out points of one shuffled
   order, so whoever takes part (the first m tokens, for any m) labels
   different papers, and queue r overlaps with queue r-1. Unused tokens at
   the end cost nothing. **Don't
   record who got which token.** Each person still needs a different link.
   The link is `https://<host>/?r=<token>`. `assignments.json` is gitignored
   until collection closes, because anyone holding a token can submit under
   it.
5. **Thanks.** The start page has an optional "add my name" form. Names go to
   the Sheet's `acknowledgements` tab with no token and no timestamp, so
   nobody can join them to answers. That tab is your thank-you list.
6. **Collect.** Download the Sheet as CSV whenever you like, then run
   `gwn eval supergold import sheet.csv [more.json …]`. Import rebuilds
   `labels.jsonl` and `papers.jsonl` from scratch each time. Collaborators
   whose submissions failed can send their *Download my answers* file; pass
   it to the same command.

`labels.jsonl` rows load with `evalset.load_gold`. Each row carries
`reviewer`, `kind` (`sample` or `calibration`), `confidence`, `stratum`,
`pi`, `uniform`, `pair_pi`, the frozen `predicted_uses` /
`predicted_engagement`, `reference_only` and `author`. When weighting,
exclude `author: true` rows and `kind: calibration`.

## Design in one paragraph

Papers are stratified into 9 groups. LVK papers form one stratum. Every other
paper is classed by its rarest predicted data product (samples > strain >
published > other) and split by era (before 2020 or not). Every stratum gets
at least `--min-per-stratum` papers. A uniform slice of `--uniform` papers is
spread across strata in proportion to their size and is self-weighting. On
those papers, labellers also list every event they find, which measures
extraction recall. Queues are fixed and randomly ordered, so "do as many as
you like" does not let people choose which papers they label. Completed papers
are then a random subset of each stratum, and `report` post-stratifies on what
was completed. A third of papers are double-labelled, interleaved from the
start. Every queue begins with 3 calibration papers from the hand-labelled
gold set, which show the reference answer after submission.

## Email template

> Subject: Help measure how papers use GW events (as much or as little as you like)
>
> We're checking how well an automated classifier recognises how papers
> use gravitational-wave events: whether they analyse the strain, use
> released samples, use published measurements, discuss an event
> qualitatively, or just cite it. Your link: https://<host>/?r=<token>
>
> Please read the short guide linked on the start page first. The first 3
> papers are practice and show the reference answer. After that, do as many
> papers as you like, in the order given; a paper takes about 10 minutes.
> Your answers are stored under the anonymous ID in the link; nobody,
> including me, can tell which answers are yours. If you'd like to be
> thanked in the paper, add your name on the start page; it is stored
> separately and isn't linked to your answers. If you are an author of a
> paper, tick the box (or skip it). Thank you!
