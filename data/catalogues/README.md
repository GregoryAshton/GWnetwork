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

## Detection criteria (verified 2026-11-12)

Criteria checked against each paper's LaTeX source (latest arXiv version).
"LVK" = within 1 s of an event with any LVK record (GWTC confident or
marginal, discovery papers, O3 IMBH marginal); all other events are >= 740 s
from any LVK event.

| Catalogue | Paper (version) | Criterion as stated | Events | with LVK record | without |
|---|---|---|---|---|---|
| IAS O1 | 1902.10341v2, 1902.10331v2 | No threshold in the pipeline paper; 1902.10331 §II: P_astro ≈ 0.7 "above the bar of 0.5 for listing events in the catalog of confident GW detections defined in [GWTC-1]" | 4 | 3 | 1 |
| IAS O2 | 1904.07214v2, 1910.09528v1 | 1904.07214 §II: FAR below 1 in 30 days (with trials factor) **and** p_astro > 0.5; 1910.09528 only says "above the thresholds of significance for being called events" | 14 | 6 | 8 |
| IAS O3a | 2201.02252v3 | §I: p_astro "at least one half" (abstract, Table I: > 0.5) | 42 | 32 | 10 |
| IAS O3b | 2311.06061v3 | §I: p_astro "at least one half" (§V.B: "greater than") | 23 | 17 | 6 |
| IAS O3 HM | 2312.06631v2 | Abstract/§III A: p_astro over 0.5 | 79 | 59 | 20 |
| 1-OGC | 1811.01921v3 | §4: "three candidates which are statistically significant" (FAR < 1/50 000 yr for two; LVT151012 by P_astro 0.976). No new events (§1) | 3 | 3 | 0 |
| 2-OGC | 1910.05331v3 | §5.1: "14 BBH candidates with p_astro > 50%, meeting the standard detection criteria" of GWTC-1, plus GW170817 | 15 | 11 | 4 |
| 3-OGC | 2105.09151v3 | §5, Table 2: "P_astro > 0.5 **or** IFAR > 100 years" | 57 | 52 | 5 |
| 4-OGC | 2112.06878v2 | §3, Table 3: "P_astro > 0.5 **or** IFAR > 100 years" | 94 | 79 | 15 |

Together: 46 distinct events without an LVK record (GPS-clustered within 2 s).

## Decisions (2026-11-12, Gregory Ashton)

- Merges are applied by giving each catalogue record the existing event's
  `common_name`, with the decision under `parameters.merge` (no
  merge-application code); the merge table is in `REVIEW-merges.md`.
- The canonical name of a non-LVK event is the first catalogue's.
- GWC170402 is included.
- IAS-HM's recovered GWTC-3 events count as IAS-HM detections.
- IAS-O3a is not duplicated (GWOSC serves it; aliases only in
  `data/events/ias-o3a-aliases.yaml`).
- Short name forms only on unique dates; bare dates are never matched.
- p_astro is taken as the paper tabulates it.
- Event view: these catalogues rank below every LVK listing and
  `is_marginal` ignores them.
- "GW170817A": of 88 corpus papers writing it (as of 2026-11-12), 61 mean
  GW170817 and are overridden in `data/mention_overrides.yaml`; 27 mean the
  IAS candidate and keep it.
