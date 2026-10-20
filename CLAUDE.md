# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

GWnetwork maps gravitational-wave events to the papers that discuss or use them.
[DESIGN.md](DESIGN.md) is the authoritative design doc (schema, cost model, and
§13–15 on what the real data taught us); read the relevant section before
changing a pipeline stage.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,web]"

.venv/bin/python -m pytest                              # all tests
.venv/bin/python -m pytest tests/test_matcher.py -k name # one test

gwn status          # counts across the whole pipeline
gwn serve --reload  # http://127.0.0.1:8000 (templates always reload; Python only with --reload)
```

Pipeline, in order: `gwn events ingest` → `gwn corpus discover` (ADS, needs
`ADS_DEV_KEY`) or `gwn corpus add-arxiv <ids>` → `gwn corpus fetch` →
`gwn extract` → `gwn classify` (costs money; use `--dry-run` first) →
`gwn export --out docs`. Other useful commands: `gwn events rebuild-view`,
`gwn unknown`, `gwn events propose-aliases`, `gwn eval template|score`.

Credentials come from `.env` in the repo root (see `.env.example`); real env
vars override it. `GWN_DB_URL` defaults to the local `gwnetwork.db` (~180 MB,
gitignored); full text lives in `data/fulltext/` (gitignored).

## Architecture

Layers communicate only through the database; no layer imports another's logic.

- `events/` — providers (`gwosc.py`, `manual.py` reading `data/events/*.yaml`)
  → `Event` / `EventDesignation` / `EventRecord`. One `Event` per physical
  event, keyed on GWOSC `commonName`; records are appended per catalogue
  version, never overwritten. `events/view.py` builds `event_view`, a derived,
  rebuildable flat table for filtering; `CATALOG_RANK` there decides which
  record is authoritative, with per-field fallback to lower-ranked records.
- `corpus/` — ADS discovery (one full-text query per designation), arXiv fetch
  (HTML → ar5iv → PDF fallback), citation metrics.
- `extract/` — deterministic matcher compiled **from the database** at run time
  (so a new YAML event makes the whole corpus searchable, no code change) →
  `Mention` rows with exact char offsets, plus `UnknownDesignation` queue.
- `classify/` — rule hints (`rules.py`; since classify-0.3 they no longer
  settle any pair, see DESIGN.md §7a), then one LLM call **per paper** (not per pair) via the Batch API, structured
  output from `schemas.py` → `EventUsage` with mandatory evidence quotes.
- `web/` — FastAPI + Jinja2 read-only site; `charts.py` renders SVG server-side;
  `export.py` renders the same routes through a TestClient into static HTML for
  GitHub Pages (`docs/`), rewriting links to relative paths.

### Invariants that are easy to break

- **Analytical tables are append-only and run-scoped.** Every `extract` or
  `classify` run creates an `AnalysisRun` and marks prior runs of that stage
  non-current. Every query touching `Mention`, `EventUsage` or
  `UnknownDesignation` must filter on the current run (`current_run()` /
  `mentions_in()` in `web/app.py`); otherwise counts multiply across historical
  runs. `tests/test_web.py` guards this.
- Bump `PIPELINE_VERSION` in `extract/runner.py` or `classify/runner.py` when
  changing that stage's output semantics.
- The role taxonomy is `ROLES` in `models.py`; changing it or the prompt changes
  `PROMPT_SHA256`, which is recorded on the run so results stay diffable.
  `EventUsage.method` records `rule` or `llm`; hand-produced classifications
  (e.g. from a Claude Code session) are marked `interactive` so they stay
  distinguishable from automated runs.
- Matcher: longest match first (`GW190521` ≠ `GW190521_030229`/`_074359`), a
  short name with multiple long-form candidates is ambiguous, one separator char
  is tolerated (PDF text turns `_` into a space) — handled in the matcher, never
  by rewriting stored text, so offsets stay valid. ~23 GWOSC marginal
  candidates have no `GW` prefix; canonicalisation must not assume one.
- Physical parameters can be NULL (only ~68% of events have masses); never
  treat NULL as zero in filters or charts.
- Cross-provider event merges are proposals only (`EventMergeProposal`); never
  auto-merge.
- Prompt caching is not enabled for the classifier. The original reason (Haiku
  4.5's 4096-token minimum) no longer applies on Haiku 5.5; see DESIGN.md §7a
  before turning it on.

## Eval

`eval/gold.jsonl` is the hand-labelled set; `engagement: null` rows are skipped.
A template-derived gold set measures classification quality only, not
extraction recall (see caveat in `gwnetwork/evalset.py`).

## Publishing the static site

Commit code first, then `gwn export --out docs`, then commit `docs/` as a
separate "Rebuild static site" commit. The footer stamps the building commit;
exporting from a dirty tree marks it `+local`.
