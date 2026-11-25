# arXiv submission volume per quarter

Context for the corpus's growth: how many papers arXiv received per calendar
quarter, overall and in the archives the corpus draws from. Built by
`gwn volume fetch` (code: `gwnetwork/arxiv_volume.py`); for release v3.

## Files

- `listing/<quarter>.csv.gz` — every paper first submitted in that quarter
  with any category in astro-ph, gr-qc, hep-ph, hep-th or nucl-th (one
  deduplicated union query; one per month from 2025Q4, when a quarter
  passed the API's 10,000-result paging limit): `arxiv_id`, `submitted` (version 1, UTC),
  `primary`, `categories` (space-separated, primary first).
- `counts.csv` — derived from the listing by `gwn volume counts`:
  `quarter, group, basis, n`. `group` is an archive (`astro-ph`), a category
  (`astro-ph.HE`, `gr-qc`) or `union`; `basis` is `any` (the paper lists the
  group, cross-lists included, so groups overlap) or `primary`. Only groups
  inside the five archives appear: a paper from elsewhere is in the listing
  only when cross-listed into one of them, so e.g. a hep-ex count would be
  partial.
- `api_totals.csv` — the API's `opensearch:totalResults` for each archive,
  the union and all of arXiv (`all`, not derivable from the listing), with
  the exact query and fetch time. `gwn volume counts` checks the listing
  against them.
- `manifest.json` — per-quarter query, API total, rows and fetch time for
  the listing.
- `arxiv_stats_monthly.csv` — arXiv's own monthly submission counts
  (https://arxiv.org/stats/get_monthly_submissions), fetched with the rest,
  as an independent check on `all`.

## Conventions

- **Date:** `submittedDate`, the UTC time of version 1, as in the corpus's
  `submitted_at`. arxiv.org/stats counts by the month in the arXiv id, which
  follows the announcement day in US Eastern time; about 1% of papers near
  a boundary fall in the other quarter there (2025Q1: API 64,850 against
  65,119 from the monthly statistics).
- **Reproducibility:** the API reflects arXiv at the fetch time
  (withdrawals, category changes). Re-running gives counts as of the new
  date; `--force` re-fetches quarters already on disk.
- **Subcategories:** any breakdown inside the five archives (astro-ph.HE,
  astro-ph.CO, ...) comes from the listing with no new fetch. Adding an
  archive needs a new listing fetch (`ARCHIVES` in the code).
