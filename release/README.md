# GWnetwork data release v1

Which papers mention which gravitational-wave events, and how each paper uses
each event. Built by GWnetwork (code: https://github.com/GregoryAshton/GWnetwork,
release tag `v1.0-paper`).

| | |
|---|---|
| Events | 434 (GWOSC catalogues plus a few non-GWOSC candidates) |
| Papers | 13,904 arXiv papers found by ADS full-text search for any event name; 13,886 not withdrawn, all with full text |
| Paper–event pairs | 35,243, each with its mentions and one classification |
| Papers submitted | 1994-12-08 to 2026-09-13 (coverage of 2026 is incomplete: ADS full-text indexing lags) |

The corpus contains only papers that **name** a specific event. Population,
method and theory papers that never name an event are not in it.

## Files

- `gwnetwork-v1.db.gz` — SQLite database (gunzip first; ~540 MB unpacked).
- `README.md` — this file.
- `GUIDELINES.md`, `gold.jsonl` — the labelling guide and the 234 hand-labelled
  pairs used to measure classification quality.
- `fulltext_overrides.yaml`, `mention_overrides.yaml` — the reviewed manual
  interventions in this release, each with its reason.
- `requirements-lock.txt` — the Python environment that produced the data.
- `gwnetwork-v1.0-paper-src.tar.gz` — the code at the release tag.

## Tables

The database holds exactly one extraction run and one classification run.
`release_info` records the release, the code commit and the two run ids.

**Events.** `event` (one row per physical event, `canonical_name` as GWOSC
names it), `event_designation` (every name and alias that resolves to it),
`event_record` (one row per catalogue version: GPS time, parameters JSON,
detector facts), and `event_view` (one flat row per event for filtering:
masses, distance, SNR, FAR, `observing_run`, `n_papers`). Physical
parameters are NULL where no catalogue gives them; never treat NULL as zero.

`event_view.instruments` is the detecting network ("HLV", "HL", "L", ...),
and `instruments_basis` says where it came from: `table` (the instruments
column of the GWTC results paper's candidate table: GWTC-2, 2.1, 3, 4.0,
5.0), `strain` (detectors with GWOSC strain files; used for O1/O2, since
GWTC-1 has no such column) or `summary` (GWOSC's summary field, used only
where neither exists; it is not reliable as "detected by" in O3).

**Papers.** `paper`: arXiv id, bibcode, title, abstract, authors, categories,
`submitted_at` (first version), `fulltext_version` and
`fulltext_version_date` (the version whose text was searched),
`fulltext_source` (`arxiv_html` or `pdf`), `withdrawn` (excluded from
extraction), `citation_count`. `paper_reference`: each paper's ADS
reference list, one cited bibcode per row. `paper_discovery`: which ADS
event-name query found each paper.

**Mentions.** `mention`: one row per occurrence of an event name in a
paper's text: `matched_text`, `char_start`/`char_end` into the stored text,
`section` (abstract, introduction, ..., references) and the surrounding
`sentence`. `unknown_designation`: GW-shaped names that match no known event.

**Classifications.** `event_usage`: one row per paper–event pair.
`engagement` is the deepest use, from the decision procedure in
`GUIDELINES.md`:

| engagement | meaning |
|---|---|
| `strain_analysis` | analyses the event's strain data |
| `posterior_samples` | uses released posterior samples |
| `published_results` | uses published measurements with their uncertainties |
| `qualitative` | uses what the event showed, without those numbers |
| `cited_only` | names or cites the event as background only |

`uses` lists every use found, `roles` the purposes (`single_event`,
`population`, `test_of_gr`, `cosmology`, `dense_matter_eos`, ...),
`evidence` the supporting quotes, `is_primary_subject` whether the event is
a subject of the paper.

## Quality and known limitations

- **Classification** (Claude Haiku 5.5 on excerpts around each mention):
  engagement agrees with the gold set on 82.1% of pairs (Cohen's kappa 0.76;
  80.3% on the person-labelled pairs). Released posterior-sample use is
  undercounted (recall 0.43, precision 1.00), because statements like "we
  use the released samples for all events" name no event and are not in the
  excerpts. A previous run with identical settings gave a different label
  for 13% of pairs, so treat per-pair labels as noisy and aggregate rates
  with uncertainties.
- **Extraction recall is unmeasured.** The gold set was drawn from pairs the
  extractor found, so events a paper names in a form the matcher misses are
  invisible to it.
- **Pairs found only in reference lists** (about 13%) are included. Many are
  `cited_only`, but some are classified deeper through the sentence citing
  that reference. Filter on `mention.section` to exclude them.
- **Bare-number candidates** (GWOSC marginal candidates named by date alone,
  e.g. `170405`) are matched only with trigger context nearby.

## Full text and offsets

Full text is not included (arXiv licensing). Each paper records the exact
arXiv version and format it came from and a SHA-256 of the stored bytes, and
`gwn release verify-fulltext` rebuilds and checks it. arXiv's HTML page
chrome around the paper changes over time while the paper body does not:
`paper.fulltext_body_start` is where the body begins in our copy, so in a
re-fetched copy every offset moves by `(its body start) - fulltext_body_start`.
In a 30-paper check, 16 texts matched byte for byte and 14 matched in the body
with a shift of -95 characters.

## Citation and licence

Cite this release as:

> Ashton, G. (2026). *GWnetwork v1: gravitational-wave events and the papers
> that use them* (Version 1.0) [Data set]. Zenodo.
> https://doi.org/10.5281/zenodo.23603658

The data are released under the Creative Commons Attribution 4.0
International licence (CC BY 4.0).
