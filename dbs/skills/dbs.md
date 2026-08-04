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
   VALID datasets only, and never says so. Always pass a status explicitly, so
   the answer can state which one it used. Measured example: a Run2018 RAW
   pattern returns 372 datasets by default and 574 across all statuses.

   **Explicit does not mean widest.** The split is catalog versus size:

   | The question asks for | Status | Why |
   |---|---|---|
   | how many DATASETS there are, or their NAMES | `*` | the catalog is the catalog; a dataset that exists still exists after it is invalidated |
   | whether something exists, or a name that found nothing | `*` | only `*` tells a typo apart from a hidden dataset |
   | invalidated, deprecated or deleted data, or a status breakdown | `*` | that is the question |
   | BYTES, EVENTS, FILES, LUMIS — how big something is | `VALID` | non-valid datasets are failed and superseded attempts; adding them inflates the size |
   | what a campaign or era holds as usable data | `VALID` | same reason |

   One line to remember: **counting or naming datasets is a catalog question,
   use `*`. Adding up size is a size question, use `VALID`.**

   Both mistakes are measured and both are large:
   - `/*/Run3Winter24*/GEN-SIM-RAW` is 40 datasets and 540.4 TB at `VALID`,
     and 458 datasets and 4.88 PB at `*`. Answering `*` to "how big is this
     campaign" is nine times too large.
   - `/*/Run2018*/RAW` holds 574 datasets at `*` and far fewer at `VALID`.
     Answering `VALID` to "how many RAW datasets are there" leaves out real
     datasets that are still in the catalog.

   When the question genuinely wants both, give the one the table names first,
   then the other clearly labelled. Never present one as if it were the other.
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
8. **A name can be too narrow by one character.** `/HIForward/...` matches
   nothing because the datasets are `HIForward0` … `HIForward29`. Widening the
   status will not save you; widen the NAME. `dbs_aggregate` does this probe
   for you and returns `did_you_mean` with patterns that really hold data.

## Zero is almost never the answer

An empty result means "this query found nothing", not "this data does not
exist". Before you report a zero:

- Read `did_you_mean`. If it offers a pattern, re-run with it. Never report
  zero while a suggestion is sitting in the reply.
- Try status `*`. VALID is a silent default and hides the rest.
- Check the name shape: primary, processed, tier, and a wildcard on any part
  that may carry a number or suffix.

Only after those say nothing may you answer "no such data", and then say which
query you ran.

## Partial answers are normal on big patterns

A whole era holds more block data than DBS will hand over inside one call.
`dbs_aggregate` answers with what it reached and marks the rest:

- `groups[].scanned: false` with `bytes: null` means that tier was NOT
  measured. It is not zero. Never add it in, never call it empty.
- `totals.partial: true` means the total is a floor, not the answer.
- `hint` names the exact follow-up calls. Make them, add the results, and
  then give one complete total.
- If you run out of calls, say plainly which tiers are included and which are
  not. A total labelled "RAW, AOD and MINIAOD only" is useful. A total that
  silently dropped ALCARECO is wrong.

## Answer discipline

- State the instance, the status filter and the validity basis with any
  number. The task tools return these in `provenance`; quote them. Do not
  call `dbs_server_info` just to learn the instance — it is already in
  `provenance`.
- **Report every number the tool handed you.** `dbs_aggregate` returns the
  dataset, byte, file and block counts together, per group and in the total,
  because one scan paid for all four. A "how big is it" answer that gives
  bytes but drops the dataset count is an incomplete answer. The same holds
  for `dbs_summary`: give the whole picture, not the single field asked about.
- **Ask for events on a campaign or era summary.** Size means bytes, events
  and files together — that is what a CompOps reader expects from "how big is
  this campaign". Events are opt-in because they cost one call per dataset, so
  pass `metrics=["events"]` when you want them. You do not need to guess
  whether it is affordable: above the cap the tool returns `events: null` with
  the reason, and never a partial sum. Ask, and pass on whatever comes back.
- Give bytes as exact bytes **and** in human units (1 TB = 1e12 bytes).
- Never round a count. Either give the exact number or say why it is not
  available and how to narrow the question.
- Never present a partial sum as a total. If a tool returns `null` with a
  reason, pass that reason on.
- Append the reproduction commands the tools return (`repro`) in a collapsible
  block, so the user can check any number outside the chat.
