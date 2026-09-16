# GWnetwork — Design

A database and website mapping gravitational-wave events to the literature that
discusses or uses them, with an automated pipeline that keeps it current.

**Status:** design, pre-implementation.

---

## 1. Goals

- For any GW event: which papers use it, how deeply, and for what science.
- For any paper: which events it touches, with quoted evidence.
- Keep current automatically; support full backfill and incremental daily updates
  through the same code path.
- Accept events from outside GWOSC (independent pipelines, preprint-only
  candidates) without schema changes.

## 2. Non-goals (v1)

- Citation-graph analysis (ADS/INSPIRE already do this well).
- Replacing GWOSC as a data source. We link, we do not mirror strain data.
- Automatic merging of events across providers without human review.

---

## 3. Architecture

Four layers. The database is the only contract between them; no layer imports
another.

    events/    providers -> event, event_designation, event_record
    corpus/    ADS discovery + arXiv fetch -> paper, fulltext on disk
    extract/   designation matcher -> mention          (deterministic, cheap)
    classify/  LLM -> event_usage + evidence           (expensive, narrow)
    web/       read-only projection

Rationale: re-run the classifier without re-downloading the corpus; re-ingest
events without touching papers; rebuild the site from scratch at any time.

---

## 4. Event identity

GWOSC keys events as `GW250119_190238-v1`, with `commonName` and
`catalog.shortName` as separate fields. The same physical event therefore appears
many times across catalogs and versions (GW150914 in GWTC-1, GWTC-2.1,
GWTC-4.0 ...). Identity is modelled explicitly in three tables.

### `event`
One row per physical astrophysical event. Deliberately thin.

    id              UUID  PK
    canonical_name  TEXT  -- e.g. GW170817
    created_at, updated_at

### `event_designation`
Every string that has ever referred to the event.

    id, event_id -> event.id
    designation   TEXT   -- GW170817, AT2017gfo, GRB 170817A, SSS17a, S190425z
    kind          ENUM   -- gw_name | superevent | em_counterpart | catalog_alias
    source        TEXT   -- gwosc | gracedb | manual
    precedence    INT
    valid_from    DATE   -- for name changes (short -> long form at GWTC-2.1)
    is_ambiguous  BOOL

### `event_record`
One claim about an event, from one source. Never overwritten; always appended.

    id, event_id -> event.id
    source        TEXT   -- gwosc | 4-ogc | ias-hm | manual
    catalog       TEXT   -- GWTC-4.0
    version       INT
    gps           DOUBLE
    parameters    JSON   -- masses, spins, distance, FAR, p_astro, ...
    reference_doi TEXT
    retrieved_at  TIMESTAMP

### Known traps

- **`GW190521` != `GW190521_030229`.** Distinct events. The matcher must prefer
  the longest match, and flag any short-form `GWYYMMDD` with more than one
  long-form candidate as ambiguous. Resolve by publication date (pre-GWTC-2.1
  papers mean the short form) or route to human review.
- **Provenance beats truth.** Display precedence picks which `event_record` the
  site shows; history stays queryable. This is what answers "did this paper use
  GWTC-2.1 or GWTC-4.0 posteriors?"

### Provider plugin interface

    class EventProvider(Protocol):
        name: str
        def fetch(self, since: datetime | None) -> Iterable[EventRecord]: ...

v1 implementations:

- `GWOSCProvider` — walks `/eventapi/json/<catalog>/` for each release.
- `ManualProvider` — reads YAML from `data/events/*.yaml`, checked into the repo.
  Non-GWOSC events enter by reviewed pull request, not by database poke.

Later, with no schema change: `GraceDBProvider`, independent-catalog providers
(4-OGC, IAS-HM).

**Cross-provider reconciliation is explicit.** Match candidates on GPS time
within a tolerance window, propose a merge, require a recorded human decision in
an `event_merge` table. Never auto-merge.

---

## 5. Corpus

### Discovery: ADS-mediated, per-event

The loop is inverted relative to the obvious design. Rather than scanning all of
arXiv, query ADS once per known designation:

    full:"GW190521"

~300 events x a few pages each is a few thousand queries: a complete backfill
inside one day's rate limit. Scope becomes a predicate on returned bibcodes
rather than a constraint on crawl volume.

**Known limitation:** discovery keyed on known names cannot find papers about
events we have never heard of. Two mitigations, neither complete:

1. Run the generic `GW\d{6}(?:_\d{6})?` regex over the full text of every paper
   we fetch. Novel events are nearly always discussed alongside known ones, so
   they surface anyway.
2. A periodic broad sweep (`full:"gravitational wave event"`, recent date range)
   as a backstop.

Unmatched `GW......` strings land in an `unknown_designation` review queue, from
which a human promotes them to `event` rows. This is the loop that connects the
literature crawler back to the event database.

### Categories (config, not hardcoded)

    gr-qc
    astro-ph.HE, astro-ph.CO, astro-ph.GA, astro-ph.SR, astro-ph.IM, astro-ph.EP
    hep-th, hep-ph
    nucl-th          # GW170817 equation-of-state literature

### `paper`

    id, arxiv_id, version, bibcode, doi
    title, abstract, authors JSON, primary_category, categories JSON
    submitted_at, updated_at
    fulltext_path TEXT, fulltext_sha256 TEXT, fulltext_source ENUM

Full text lives on disk or object storage; only path + hash in the database.

### Incremental metadata

arXiv OAI-PMH supports `from`/`until` datestamps and set restriction. The daily
job is "everything with a datestamp since my watermark." Resumption tokens and
watermarks live in `harvest_state`.

---

## 6. Extraction (deterministic, no LLM)

Answers only: which papers mention which events.

The matcher is **compiled from the database**, not hardcoded — an Aho-Corasick
automaton built from `event_designation` rows, plus the generic `GW` pattern.
Consequence: adding a non-GWOSC event via YAML makes the entire existing corpus
searchable for it on the next extraction run.

### `mention`

    id, paper_id, event_id (nullable), analysis_run_id
    matched_text, char_start, char_end
    section, sentence

Offsets are mandatory. Everything downstream, and all UI trust, depends on being
able to point at the actual text.

---

## 7. Classification (LLM, narrow)

Runs only on `(paper, event)` pairs with at least one mention.

### Two orthogonal fields, not one enum

Analysis types are not mutually exclusive — a paper can do a population-level
test of GR.

`engagement` (ordinal — how deeply the paper touches the event):

    cited_only          appears in intro/discussion, no analysis
    published_results   uses quoted catalog parameters
    posterior_samples   uses released PE samples
    strain_reanalysis   reanalyses the strain data itself

`roles` (multi-label — what it is used for):

    single_event, population, multimessenger, test_of_gr, cosmology, lensing,
    formation_channels, waveform_systematics, exotic_compact_object,
    detector_characterisation, methods_demo, review, forecast

### `event_usage`

    id, paper_id, event_id, analysis_run_id
    engagement ENUM
    roles JSON
    is_primary_subject BOOL
    confidence FLOAT
    evidence JSON   -- [{quote, char_start, char_end, section}]  REQUIRED

**A classification without an evidence span is unverifiable, undebuggable and
unpublishable.** Schema requirement, not convention.

### One call per paper, not per pair

**Architecture decision, driven by cost.** A single structured-output call
classifies every event in a paper at once. Per-pair calls would re-send
near-identical context once per event -- a GWTC population paper mentioning 90
events would cost 90 calls. Per-paper batching cuts call count ~5x on average
and far more on catalog papers.

### Document chunking

Do not send whole papers. Per call: abstract + a window around each mention
(all events) + methods/data section headers. Cap total windows on papers with
very many mentions; prefer methods/data windows over introduction windows when
truncating.

---

## 7a. Cost model

Pricing (2026-06): Haiku 4.5 $1/$5 per MTok in/out; Sonnet 5 $2/$10;
Opus 5 $5/$25. **Batch API is 50% off** and this workload is entirely
latency-insensitive -- always use it.

Assumptions: ~20k GW-mentioning papers for a full backfill; ~3.5k input and
~600 output tokens per paper-call.

| Model | Standard | With batch |
|---|---|---|
| Haiku 4.5 | $130 | **$65** |
| Sonnet 5 | $260 | $130 |
| Opus 5 | $650 | $325 |

**Default: Haiku 4.5 + Batch API.**

### Cost levers, in order

1. **Rule-based `cited_only` pre-filter.** One mention, introduction only, no
   data/methods reference -> classify deterministically, never send to the LLM.
   Plausibly ~60% of the corpus. Backfill drops to ~$26.
2. **One call per paper** (above), ~5x.
3. **Batch API**, 2x.
4. **Escalation, not uniform upgrade.** Route to Sonnet 5 only where Haiku
   returns low confidence or a high event count. Expect <10% of papers.

### Do NOT use prompt caching here

Haiku 4.5 has a **4096-token minimum cacheable prefix**; below it, caching
silently does not engage (no error, `cache_creation_input_tokens: 0`) while
still billing the 1.25x write premium. The constant prompt here is ~1k tokens.
Batch already provides the 2x. Revisit only if the model changes -- Opus 5's
minimum is 512 tokens.

### The recurring cost is re-runs

Every pipeline version bump re-enqueues the corpus. At ~$26/run, prompt
iteration is free in practice; at Opus prices each experiment is ~$325 and
iteration stops. **This is the real argument for Haiku -- iteration speed, not
unit cost.**

### Zero-cost paths

- **Phase 2 ships without any LLM.** Regex mentions + rule-based `engagement`
  gives a complete, evidence-backed event<->paper graph for $0. Only the `roles`
  multi-label genuinely needs a model.
- **Local model** (Ollama, 7-8B, Apple Silicon) will process 20k papers
  overnight for electricity. Notably worse on `roles`, comparable on
  `engagement`. Given the numbers above, not recommended -- but it is a real
  fallback.

### Request settings

Batch API; `max_tokens` ~1500 (output is bounded structured JSON); structured
outputs via `output_config.format` with the Pydantic schema; no extended
thinking (this is classification, not reasoning-heavy).

---

## 8. Jobs and versioning

A single `job` table keeps backfill and incremental updates on the same path.

    id, kind, target_id, pipeline_version, status, attempts, error,
    claimed_at, completed_at

    kind = discover | fetch_fulltext | extract | classify

- Work unit is `(target, pipeline_version)`. Bumping the classifier version
  re-enqueues the corpus; a backfill is just a large enqueue.
- Workers claim rows atomically, are idempotent, safe to kill mid-run.

### `analysis_run`

    id, pipeline_version, model_id, prompt_sha256, config JSON,
    started_at, completed_at

Every `mention` and `event_usage` row points at its run. Results are never
overwritten; the site reads a "current run" view. This makes prompt improvements
diffable against a fixed evaluation set.

---

## 9. Website

Read-only projection.

- **Event page** — parameters, catalog/version history, papers grouped by
  engagement and role, publication timeline.
- **Paper page** — events used, with evidence quotes.
- **Cross-cutting** — event co-occurrence graph, role trends over time,
  most-reanalysed events.

FastAPI + server-rendered templates, or static generation. The database boundary
means this choice is not load-bearing.

---

## 10. Build order

| Phase | Deliverable | Verified by |
|---|---|---|
| 0 | Schema, migrations, GWOSC provider, CLI | `gwn events list` shows every GWTC event with full version history |
| 1 | ADS discovery + arXiv fetch, one year | N papers with full text on disk |
| 2 | Designation matcher + mention extraction | **Hand-label ~100 papers; measure precision/recall** |
| 3 | LLM classification + evidence | Agreement against the hand-labelled set |
| 4 | Incremental daily job | Runs unattended for a week |
| 5 | Website | |
| 6 | Backfill to 2015 | |

Phase 2's hand-labelled set is the highest-value step and the easiest to skip.
It is the only thing that tells you whether the premise works before spending
real money on LLM calls. It also becomes the permanent regression suite for
every later prompt change.

---

## 11. Stack

- Python, Typer CLI
- SQLite for the prototype; SQLAlchemy + Alembic so the Postgres move is
  mechanical. Move when you want real full-text search and concurrent workers.
- Pydantic schemas shared between LLM structured output and DB models.
- Secrets: ADS token and LLM key from environment, never committed.

## 12. Layout

    gwnetwork/
      core/       models, migrations, config, db session
      events/     base.py, gwosc.py, manual.py, reconcile.py
      corpus/     ads.py, arxiv.py, store.py, harvest.py
      extract/    matcher.py, sections.py
      classify/   prompts.py, schemas.py, runner.py
      jobs/       queue.py, worker.py
      web/        api, templates
      cli.py
    data/events/  manual event YAML
    tests/
    eval/         hand-labelled gold set

---

## 13. Findings from the prototype

Things the real data taught us that the design did not anticipate.

### Event identity held up

433 distinct events from 671 catalogue records across 17 GWOSC catalogues.
`commonName` is a stable identity key -- GWOSC never renamed the short forms, so
`GW190521` and `GW190521_074359` coexist as separate events 4.7 hours apart
(likewise `GW190814` / `GW190814_192009`). Grouping by `commonName` is correct;
**longest-match-first in the matcher is mandatory**, and a prefix relationship
between two names carries no meaning.

`IAS-O3a` is already a GWOSC catalogue, so non-LVK pipelines are in scope from
day one rather than hypothetically.

### PDF extraction mangles the separator

The large LVK collaboration papers (GWTC-2, GWTC-3) **fail LaTeXML conversion
outright** -- ar5iv returns "No content available" -- and they are the highest
value documents in the corpus (92 and 47 events respectively). A PDF fallback is
mandatory, not a nicety.

But PDF text extraction turns `GW200115_042309` into `GW200115 042309`. Before
this was handled, GWTC-3 yielded 10 events instead of 92 and flooded the review
queue with 75 bare prefixes. Fixing it in the *matcher* (tolerating one
separator character) rather than by rewriting stored text keeps evidence offsets
exact against the file on disk.

Effect: 708 -> 1553 mentions, 75 -> 7 unknown designations.

### 23 GWOSC events have no `GW` prefix

Marginal candidates are named `200214_224526`, not `GW200214_224526`. Any
normalisation that assumes the prefix silently routes every marginal candidate
into the unknown queue. The generic *discovery* pattern still requires the
prefix -- bare `\d{6}_\d{6}` would match table numbers -- but canonicalisation
must not.

### The discovery channel works

On an 8-paper test corpus, `gwn unknown` surfaced `GW170121` -- an IAS candidate
absent from every GWTC catalogue -- from a sentence describing it as an
independent observation. That is the non-GWOSC requirement satisfied by the
literature itself, which was the intended design but not a guaranteed one.

### Run-scoping applies to the review queue too

`unknown_designation` was initially global, so artefacts from a superseded
pipeline version kept haunting the review list after the bug that caused them
was fixed. Anything analytical must carry `analysis_run_id`.

### The 60% rule-filter estimate is still untested

The 8-paper sample gives 8%, but it is unrepresentative by construction: these
are catalogue and analysis papers where 90% of mentions fall in
appendix/data/methods/results, and it contains almost no "cited once in the
introduction" papers. That estimate can only be tested against an
ADS-discovered corpus. **Do not treat 8% as evidence against the cost model.**

The other lever is confirmed, though: 181 pairs collapsed into 8 LLM calls, a
22x reduction from one-call-per-paper.

---

## 14. Event view and filtering

### `event_view`: a materialised projection

Parameters live in `EventRecord.parameters` as JSON, one record per catalogue
version, so filtering on mass or SNR meant unpacking JSON across several rows
per event. `event_view` flattens that into indexed columns, one row per event.

It is derived, never authoritative: `gwn events rebuild-view` drops and rebuilds
it. `EventRecord` remains the source of truth, and `primary_record_id` /
`params_from_catalog` keep every displayed number traceable.

### Two rules written down here, made nowhere else

**Catalogue precedence** (`CATALOG_RANK` in `events/view.py`) decides which
record is authoritative: confident GWTC releases > discovery papers >
independent pipelines > preliminary and marginal listings.

**Field-level fallback.** The highest-ranked record is not always the most
complete -- an O4 discovery listing may carry SNR but no masses while an earlier
record has full PE. Each field is taken from the highest-ranked record that has
it. On real data this recovers parameters for 11 events that a naive
top-record-wins rule would have dropped.

### Nullability is load-bearing

Only 68% of events have a mass, 66% a spin. A numeric filter can only match
events where the quantity was measured, so the UI reports how many events were
excluded for lack of data rather than letting them vanish. Treating NULL as zero
would be a correctness bug, not a cosmetic one.

`mass_class` (BBH / BNS / NSBH) uses a 3.0 M_sun threshold. That is a
convention, not a measurement: it ignores uncertainty entirely, and events near
the line (GW190814's 2.6 M_sun secondary) are genuinely ambiguous. It is a
browsing aid, never a physical claim.

## 15. Templates

The web layer moved from f-string concatenation to Jinja2
(`gwnetwork/web/templates/`). Autoescaping replaces the hand-rolled `esc()`
helper, and the routes shrank to queries plus a context dict.

**Every query touching `Mention` or `EventUsage` must filter on the current
`analysis_run`.** Analytical tables are append-only, so the database holds every
superseded run: before this was enforced through the shared `current_run()`
helper, mention counts on the site were 5x too large with five extract runs
present. `tests/test_web.py` seeds five runs and asserts the count is 1.
