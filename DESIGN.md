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

`uses` (multi-label since classify-0.4 -- which GW data products the paper
uses for the event; one yes/no question each, answered independently):

    strain_analysis     analyses the strain data itself (original or independent)
    posterior_samples   uses released samples (PE, or sets conditioned on it)
    published_results   uses published measurements with their uncertainties,
                        in a calculation or a comparison

Only when none applies: `qualitative` (uses what the event showed, in words or
rounded values, read broadly) or `cited_only` (`uses` is empty).

`engagement` (ordinal, derived = the deepest use; kept so the site can group and
sort):

    cited_only < qualitative < published_results < posterior_samples < strain_analysis

Why a set: a paper can analyse the strain *and* compare with the released
samples, and a single ordinal value forced labellers to drop one. The question
text lives once in `classify/prompts.py` (`DATA_QUESTIONS`) and is shared by the
prompt, the labelling UI and `eval/GUIDELINES.md`.

`roles` (multi-label — what *this event* is used for in the paper; judged per
event, so a GW170817 follow-up paper gives GW170817 `counterpart_followup` but
gives a GW150914 cited in its introduction no roles):

    single_event, population, multimessenger, counterpart_followup, test_of_gr,
    cosmology, lensing, formation_channels, waveform_systematics,
    dense_matter_eos, exotic_compact_object, detector_characterisation,
    methods_demo, review, forecast

`single_event` vs `population`: does the paper produce a result about this
event on its own (a catalogue's per-event PE counts), or is the event one input
to a combined inference (rates, distributions, stacked tests)? Both when it
reports per-event results and combines them.

`counterpart_followup` is observing or searching for the event's EM/neutrino
counterpart, non-detections included; `multimessenger` is joint interpretation
without new observations. A follow-up's GW input is usually the alert sky map,
which counts as `published_results`.

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

Papers with more than 25 LLM-bound events are split into chunks of 25
(`MAX_EVENTS_PER_CALL`). Asked about 130 events in one call, Haiku 5.5
returned 19 and silently dropped the rest; the runner now reports any event
the model fails to return. This takes the full corpus from 1709 to 1896 calls.

### Document chunking

Do not send whole papers. Per call: abstract + a window around each mention
(all events) + methods/data section headers. Cap total windows on papers with
very many mentions; prefer methods/data windows over introduction windows when
truncating.

---

## 7a. Cost model

Pricing (2026-10): Haiku 5.5 $0.10/$0.50 per MTok in/out (prompts <=100k
tokens); Haiku 4.5 $1/$5; Sonnet 5 $2/$10;
Opus 5 $5/$25. **Batch API is 50% off** and this workload is entirely
latency-insensitive -- always use it.

Assumptions: ~20k GW-mentioning papers for a full backfill; ~3.5k input and
~600 output tokens per paper-call.

| Model | Standard | With batch |
|---|---|---|
| Haiku 5.5 | $13 | **$6.50** |
| Haiku 4.5 | $130 | $65 |
| Sonnet 5 | $260 | $130 |
| Opus 5 | $650 | $325 |

**Default: Haiku 5.5 + Batch API.** (Haiku 4.5 was the original default; 5.5
is 10x cheaper and better at instruction following.)

### Cost levers, in order

1. ~~**Rule-based `cited_only` pre-filter.**~~ Retired in classify-0.3. It
   settled pairs with <=2 mentions in passing sections without an LLM call
   (36% of pairs on the full corpus), but once `qualitative` was split out of
   `cited_only` a single introduction sentence can be either -- "GWs have been
   observed [1]" vs "GW150914 revealed ~30 Msun black holes exist in binaries"
   -- and no rule tells them apart. Every pair now goes to the LLM; at Haiku
   5.5 batch prices that costs a few dollars per full run.
2. **One call per paper** (above), ~5x.
3. **Batch API**, 2x.
4. **Escalation, not uniform upgrade.** Route to Sonnet 5 only where Haiku
   returns low confidence or a high event count. Expect <10% of papers.

### Prompt caching: ruled out under Haiku 4.5, open again under 5.5

Haiku 4.5 has a **4096-token minimum cacheable prefix**; below it, caching
silently does not engage (no error, `cache_creation_input_tokens: 0`). The
constant prompt here is ~1k tokens, so caching was left off and Batch provided
the 2x. Haiku 5.5's minimum is **512 tokens**, so the system prompt would now
cache. Caching is still not enabled: at Haiku 5.5 prices the saving is cents
per run. If it is turned on, verify `cache_read_input_tokens > 0` on a batch.

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
  most-analysed events.

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

**Resolved (2026-10).** On the full ADS corpus the filter settled 36% of pairs,
not 60% -- and it was then retired anyway (§7a): once `qualitative` was split
out of `cited_only`, a passing-section mention is no longer safely decidable
by rule.

### arXiv HTML opens with a table of contents

Native arXiv HTML and ar5iv pages start with site navigation and a TOC whose
entries look exactly like headings, ending in "References". The section
indexer read them as real headings, so everything up to the first heading it
recognised -- the title, the abstract, in one review 212k characters of body
-- was labelled `references`. About 1 in 8 sampled `references` mentions were
really title or body text. The indexer now ignores headings before the
`arXiv:NNNN.NNNNNvN [cat]` stamp line that marks where the paper begins
(present in every HTML file sampled).

### ar5iv redirects unconverted papers to the abstract page

208 of 291 "ar5iv" full texts were the arXiv abstract page: ar5iv redirects
papers it never converted, and the page is long enough to pass a length check.
They included the GW170817 discovery paper. The fetcher now rejects them
(`is_abstract_page`) and falls through to the PDF.

### Fetches are pinned to an arXiv version

Mention offsets index into the stored text, so a re-fetch must return the same
bytes. The fetcher used to request the unversioned URL, which serves whatever
is latest: a paper revised since would silently come back as different text.
It now refreshes the version from the arXiv API and requests `/html/{id}vN`,
then `/pdf/{id}vN`. Each paper records the version fetched, that version's
date, the fetch time and the converter (`html_to_text/1`, `pypdf/<version>`),
and `paper_date` uses the fetched version's date, so a later revision does not
move a paper's date away from the words we matched.

ar5iv is dropped from the chain: it cannot serve a given version and its pages
do not say which version they rendered. arXiv's own HTML exists for versioned
URLs of old papers too.

Texts fetched before pinning are pinned retroactively (`gwn corpus
pin-versions`): an unversioned fetch served the latest version at the time, so
if the latest version's date predates the file's modification time, the text
is that version. The rest (revised since, ar5iv, "cached") are re-fetched with
`gwn corpus fetch --unpinned`.

### The latest version is not always the best rendering

Pinning exposed renderings that lose events silently. arXiv's HTML for every
version of GWTC-2 leaves its `\IfEqCase` event-table macros unexpanded, so
designations run together (`...AGW190413AGW190413_052954GW190719...`) and the
matcher finds 6 of 47 events; the PDF finds all 47. The GWTC-4.0 cosmology
paper's v3 has no HTML, and pypdf stopped at its default limit of 5,000 form
XObjects per page, silently dropping content (39 events against 143 from the
v2 HTML).

Three responses, none of them automatic version-shopping:

- `arxiv.text_issues` records signs of a bad rendering on the paper
  (`fulltext_issues`): designations glued together (>=100; a PDF's figure
  labels give a few), raw TeX control macros (>=20), and pypdf's truncation
  warning. `gwn corpus check-text` runs the content checks over stored texts.
- A flagged paper is fixed by a reviewed entry in
  `data/fulltext_overrides.yaml` pinning a version and format, with a reason;
  `fetch` applies new entries. Its version date comes from OAI-PMH, since the
  query API only gives the first and latest.
- The PDF converter raises pypdf's limit to 100,000, and its recorded name
  carries everything that determines the text:
  `pypdf/6.19.0;fonttools=none;xform-limit=100000` (fontTools, if installed,
  changes font decoding). `pypdf` is pinned exactly in `pyproject.toml`.

### Bare-number candidates need trigger context

About 20 GWOSC marginal candidates are named by date alone (`170405`,
`161202`). A bare six-digit number is also a Phys. Rev. Lett. article number
("87, 170405 (2001)"), part of a source name (`3XMM J181923.7-170616`) or a
table entry: `170405` was "mentioned" by 46 papers back to 1999, and such
matches were every paper-event pair dated before its event. Genuine uses sit
in trigger lists and tables, so a bare six-digit match now needs, within 200
characters, trigger vocabulary (trigger, marginal, GstLAL, PyCBC, cWB, FAR,
SNR, a GW name) or a *different* candidate (a DOI repeats the same number),
and must not be glued to a coordinate or decimal (`.7-170616`, `170423.688`).
A table's spaced dash (`0.034 -  161217`) is not a coordinate. Long forms
(`200214_224526`) are specific enough to need no context.

### An author can name the wrong event

1903.01998 writes "GW190521" throughout, but its masses (42.1 and 32.7 Msun)
are GW190521_074359's. No matcher rule can see that, so
`data/mention_overrides.yaml` holds reviewed per-paper reassignments with the
evidence; mentions keep their text and offsets, and the run's config records
the overrides applied.

### Raw facts for the attribution and citation studies

Stored as facts, with definitions left to the analysis:

- **Detectors per event version** (`gwn events detectors`, GWOSC v2 API):
  `EventRecord.detectors` (GWOSC's summary field), `strain_detectors`
  (detectors with released strain files) and `data_doi`. The two detector
  fields disagree on 27 of 671 versions, and the summary is not "detected
  by": it lists H1 for GW190425 (Hanford was off; strain is L1+V1) and V1 for
  GW170823 (strain H1+L1). Strain files exist only for O1-O3 versions; the
  O4 catalogues (GWTC-4.0/4.1/5.0) have none, and there the summary is the
  only per-event source (O4a correctly shows no Virgo). A later catalogue
  version can carry no files of its own (GW150914's GWTC-2.1 version), so an
  event's strain detectors are the union over its versions.
- **Detecting instruments** (`gwn events instruments`): the authoritative
  source is the instruments column of each GWTC results paper's candidate
  table (GWTC-2, 2.1, 3, 4.0, 5.0), parsed from our pinned full text into
  `EventRecord.instruments` with the paper and version it came from. 502
  rows, 377 events, no paper disagrees with another. GWTC-1 has no such
  column, and the GWTC-4.1 additions appear in no results paper.
  `event_view.instruments` resolves one value per event: table, then strain
  files (O1/O2, matching the known facts), then the GWOSC summary (12
  events, mostly GWTC-4.1 additions in O4a, when Virgo was off);
  `instruments_basis` says which. IAS and other non-LVK events get the
  summary, which is GWOSC data availability, not their detection network.
- **Reference lists** (`gwn corpus references`, ADS): `paper_reference`, one
  row per cited bibcode, 1.19M rows for 13,876 papers (452 with no references
  in ADS, 2 not found). ADS's bulk endpoint does not follow bibcode aliases
  (an arXiv bibcode superseded by a journal or thesis one), so misses are
  retried through the search API's `identifier` field.

### Withdrawn papers are excluded

arXiv has no withdrawn flag. A withdrawal is a new version with no HTML and no
PDF, and its comment is free text that cannot be relied on: of 18 withdrawals
in the corpus, 10 never use the word ("To be modified", "merged with
2004.01632"), while a live paper's comment says it was "forced to withdraw,
now re-submitted". So `Paper.withdrawn` is set when both formats of the latest
version return 404 (a server error is not evidence). The 18 are kept in the
database but skipped by extraction, so they count nowhere; a withdrawn paper's
earlier text is not evidence of how the field used an event.

### Bibliography mentions hide how an event is used

A designation found in the reference list is usually the title of a cited
paper ("GW150914: Implications for the stochastic background..."), and says
nothing about how the citing paper uses the event. The sentence that cites it
("...a stochastic GW background [84]...") does, but never contains the event's
name. `gwnetwork/citations.py` maps a bibliography offset back to its in-text
citations (numbered and author-year styles; ~90% located on a 300-mention
sample), and both the classifier and the labelling UI are given those
sentences.

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
