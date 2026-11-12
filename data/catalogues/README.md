# Non-LVK search catalogues (v2, paper question A6)

One JSON file per catalogue, transcribed 2026-11-12 from each paper's arXiv
LaTeX source (latest version, recorded in `source_versions`). Each file holds
the catalogue's detection criterion as the paper states it (verbatim quote and
section), every event it counts as a detection by that criterion (including
LVK events it recovers), with the designations it uses, GPS time and where it
came from, reported p_astro, FAR/IFAR, table and tier, and a count of
tabulated candidates below the criterion.

These files are provenance, not pipeline input: events enter the database
through `data/events/*.yaml` (manual provider). GPS times marked "computed"
come from the UTC in the event name and are good to about 1 s.
