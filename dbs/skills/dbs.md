# CMS DBS MCP — Usage Guide

DBS (Dataset Bookkeeping Service) is the CMS catalog of datasets, blocks,
files and their processing history: status, size, events, lumis, runs,
parentage, release and configuration. DBS says what the data **is**.

This interface is **read-only**. Anything that would change DBS (injection,
invalidation, migration) must go through the standard CompOps procedures.

## Which tool for which question

| Question | Tool |
|---|---|
| "How big is this dataset / block? Is it valid? How many files, events, runs?" | `dbs_summary` |
| "Does this dataset exist? What was in it before it was invalidated?" | `dbs_summary` |
| "Total size of this era / campaign, by tier / stream / version / status" | `dbs_aggregate` |
| "How many datasets match this pattern?" | `dbs_aggregate` with `count_only=true` |
| "Which datasets match this pattern?" (names wanted) | `dbs_list_datasets` |
| "Which files hold run X lumi Y?" | `dbs_list_files` with `run_num` |
| "Which blocks contain run X?" | `dbs_list_blocks` with `run_num` |
| "Which runs are in this dataset?" | `dbs_list_runs` |
| Anything else in the DBS read API | `dbs_call` |

Prefer `dbs_summary` and `dbs_aggregate`. They already handle the traps below
and return bounded, self-describing answers. Reach for the listing tools when
the user actually wants names, and for `dbs_call` only when nothing else fits.

## Not DBS — send these elsewhere

Where data physically sits, replicas, tape versus disk, transfers, rules,
quotas → **Rucio**. Site status → SSB/CRIC. Jobs → MONIT/Condor. Workflow or
request state → ReqMgr2/WMStats. Certification and golden JSON → RunRegistry
or OMS. Luminosity → OMS/brilcalc. Request provenance → McM.

Inbound rule: for **bytes and events, DBS numbers win**, even when another
system could also add them up.

## Server traps (why the task tools exist)

1. **VALID is a silent default.** A dataset query with no status filter shows
   VALID datasets only, and never says so. Always pass a status explicitly;
   use `*` for all. Measured example: a Run2018 RAW pattern returns 372
   datasets by default and 574 across all statuses.
2. **`validFileOnly` is presence-checked.** Sending it as 0 behaves exactly
   like 1. All-file numbers require omitting the key entirely. The same flag
   also silently restricts to VALID/PRODUCTION datasets, so on an invalidated
   dataset it returns zeros.
3. **The era-name filter is worse than broken.** `acquisition_era_name` is
   accepted, ignored, and answers with the entire catalog (over a million
   dataset names, hundreds of MB). Select an era by name pattern only:
   `/*/HIRun2026A*/AOD`.
4. **Wildcards into summary endpoints return zeros, not errors.** One dataset
   per summary call. `dbs_summary` refuses to be fooled by this; a raw call
   will not tell you.
5. **The blocks endpoint ignores the dataset status filter**, and also ignores
   `open_for_writing`. Any pattern-level total built on blocks must be
   intersected with a status-resolved name list — otherwise it can be many
   times too large.
6. **Per-run event counts from summary endpoints count whole files.** On
   merged tiers (MINIAOD, NANOAOD) one file spans several runs, so a per-run
   number can be many times too high. Treat it as an upper bound and say so.
7. **Nothing returns 404.** A typo, a missing dataset and a status-hidden
   dataset all come back as an empty result. Only a query with status `*`
   tells them apart.

## Answer discipline

- State the instance, the status filter and the validity basis with any
  number. The task tools return these in `provenance`; quote them.
- Give bytes as exact bytes **and** in human units (1 TB = 1e12 bytes).
- Never round a count. Either give the exact number or say why it is not
  available and how to narrow the question.
- Never present a partial sum as a total. If a tool returns `null` with a
  reason, pass that reason on.
- Append the reproduction commands the tools return (`repro`) in a collapsible
  block, so the user can check any number outside the chat.
