# CMS DBS MCP — Usage Guide

## What DBS is for

DBS (Data Bookkeeping Service) is the CMS catalog of datasets, blocks, files,
and their processing provenance (release, era, tier, parentage, runs/lumis).
Use the `dbs_*` tools for questions like "does this dataset exist", "how many
files/events does it have", "what are its blocks", "what is its parent",
"which datasets match this pattern".

DBS is NOT the right tool for replica/location questions ("where is this
dataset", "is it on disk/tape", transfer rules) — use the `rucio_*` tools for
those. DBS says what the data *is*; Rucio says *where* it lives.

## This interface is READ-ONLY

Only lookups. For anything that would modify DBS (injections, invalidations),
explain that it must go through the standard CompOps procedures and point the
user to the relevant tooling instead.

## Query discipline

- Dataset patterns: always use the full `/Primary/Processed/Tier` form, with
  `*` wildcards only where needed. Never run a bare `/*/*/*`-style pattern —
  all three fields unbounded is expensive on the DBS server. A pattern with
  any field bound (for example `/*/Run2018*/RAW`) is cheap here, and is the
  right SINGLE call for a catalog count — do not split it into per-era
  queries.
- Prefer the most specific tool available over listing everything and
  counting it yourself: `dbs_summary`, `dbs_aggregate`, `dbs_run_summary`
  and `dbs_run_coverage` do the counting and summing server-side.
- When a dataset is not found, check the obvious variants before concluding it
  does not exist: re-query with `dataset_access_type='*'` (VALID is a silent
  default that hides the rest), and check typos in the processed-dataset name
  (campaign, era, version suffix like `-v1`/`-v2`).
- Counting or naming datasets is a catalog question: query the name pattern
  in one call with status `*` — never decompose it into hand-picked eras.
  Sizing is different: adding up bytes/events takes `VALID`.

## Answer discipline

- Answer every figure and list the question names — give numbers and run
  lists directly in the reply, never as an offer to provide them later.
- Give exact numbers: exact bytes plus human units (1 TB = 1e12 bytes);
  never round or approximate a count the tool returned.
- Copy names exactly from the tool's rows. Never extend a family or series
  by analogy — if a name did not appear in the tool output, it does not
  exist.
- Nothing in DBS returns 404. A nonexistent or mistyped dataset comes back
  as an empty list from listing endpoints, and as a single all-zero record
  (HTTP 200) from summary endpoints — zeros there do not mean "empty
  dataset". Only re-querying with status `*` tells a typo apart from a
  status-hidden dataset.

## Reproducibility

After any non-trivial response based on DBS results, append a collapsible
`<details>` block with equivalent commands the user can run themselves
(`dasgoclient --query 'dataset=/...'` or the DBS REST URL you effectively
queried), so results can be reproduced outside the chatbot. The task tools
return ready-made `repro` lines — use those when present.
