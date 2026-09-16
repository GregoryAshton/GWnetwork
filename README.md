# GWnetwork

Maps gravitational-wave events to the literature that discusses or uses them.

Architecture and cost model: [DESIGN.md](DESIGN.md).

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,web]"
```

## Quick start (no API keys needed)

```bash
gwn init
gwn events ingest                      # 433 events, 671 catalogue records from GWOSC
gwn corpus add-arxiv 2111.03606 1710.05832 2006.12611
gwn corpus fetch                       # arXiv HTML -> ar5iv -> PDF fallback
gwn extract                            # deterministic mention extraction, free
gwn serve                              # http://127.0.0.1:8000
```

`gwn status` prints counts across the whole pipeline at any point.

## Full pipeline

| Command | What it does | Cost |
|---|---|---|
| `gwn events ingest` | GWOSC + manual YAML providers | free |
| `gwn corpus discover` | ADS full-text search, one query per designation | free (needs `ADS_DEV_KEY`) |
| `gwn corpus fetch` | full text from arXiv | free |
| `gwn extract` | find event mentions | free |
| `gwn classify --dry-run` | show the rule/LLM split, spend nothing | free |
| `gwn classify` | rules + LLM via Batch API | ~$26 full backfill |
| `gwn serve` | read-only website | free |

Everything before `gwn classify` is free. A useful site exists at that point --
the LLM adds the `roles` labels, not the event/paper graph.

## Adding a non-GWOSC event

Drop a YAML file in `data/events/` and re-run `gwn events ingest`. The matcher is
compiled from the database, so the whole existing corpus becomes searchable for
that event on the next `gwn extract`, with no code change.

```yaml
common_name: GW151216
source: ias
catalog: IAS-O1
external_id: GW151216-ias-v1
gps: 1134293073.0
reference: "arXiv:1902.10331"
designations:
  - designation: GW151216
```

See `data/events/ias-o1.yaml` (a real non-GWOSC candidate) and
`data/events/aliases.yaml` (EM counterpart aliases for GW170817).

## Discovering events nobody told us about

`gwn unknown` lists GW-shaped strings matching no known event. This is how a
signal absent from GWOSC enters the system: the literature crawler surfaces it,
a human promotes it to a YAML file. On a small test corpus it correctly
surfaced `GW170121`, an IAS candidate not in any GWTC catalogue.

## Measuring quality before spending money

```bash
gwn eval template          # pre-filled skeleton from extraction output
#   ... a human fills in `engagement` and `roles` ...
gwn eval score
```

Read the caveat in `gwnetwork/evalset.py`: a template-derived gold set cannot
measure extraction *recall*, only classification quality. Rows for pairs the
extractor missed have to be added by reading papers.

## Credentials

Copy `.env.example` to `.env` in the project root and fill it in. `.env` is
gitignored. Real environment variables override the file, so
`ADS_DEV_KEY=... gwn corpus discover` still works for one-off runs.

```
ADS_DEV_KEY=your_token_here
ANTHROPIC_API_KEY=
```

Get an ADS token at <https://ui.adsabs.harvard.edu/user/settings/token>
(free; sign in with your institutional account).

| Variable | Purpose |
|---|---|
| `ADS_DEV_KEY` | ADS API token, for `gwn corpus discover` |
| `ANTHROPIC_API_KEY` | for `gwn classify` |
| `GWN_DB_URL` | defaults to local SQLite |
| `GWN_CLASSIFY_MODEL` | defaults to `claude-haiku-4-5` |
| `GWN_ENV_FILE` | alternative path to the `.env` file |

## Tests

```bash
.venv/bin/python -m pytest
```

## Static site (GitHub Pages)

```bash
gwn export --out docs      # renders 438 pages, ~6.5 MB
```

Pages serves files, not processes, so the export differs from `gwn serve` in two
ways: event filtering moves into the browser (434 events is small enough that
client-side filtering is instant and needs no server), and paper links point at
arXiv rather than local pages — exporting all 13,904 paper pages would add
~49 MB to host what arXiv already hosts.

Regenerate and commit `docs/` after any pipeline run that changes the data.
