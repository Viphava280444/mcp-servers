"""MCP tools for the CMS DBS3 Python client."""

from __future__ import annotations

import inspect
import os
import time  # noqa: F401  -- see the compatibility seam below
from functools import lru_cache
from typing import Any
from dbs.apis.dbsClient import DbsApi

from mcp.server.fastmcp import FastMCP

# Absolute imports, not relative: one test loads this file directly with
# importlib.spec_from_file_location, which gives it no parent package, and a
# relative import would fail there. Absolute works in all three contexts --
# that loader, pytest with dbs/ on sys.path, and the installed console script.
from src.utils import (
    EXACT,
    RunScanResult,
    ScanResult,
    UNPROVEN,
    UPPER_BOUND,
    build_client,
    call_dbs_method,
    get_method,
    public_methods,
    _agg_envelope,
    _bounded,
    _dbs_instance,
    _diagnose_zero_row,
    _did_you_mean,
    _env_int,
    _envelope,
    _epoch_to_day,
    _first_row,
    _probe_wider_patterns,
    _release_response,
    _resolve_runs,
    _rows,
    _run_exactness,
    _run_numbers,
    _run_summary_envelope,
    _utc_now,
    _validate_run_selector,
)
from src.utils import _scan_blocks as _lib_scan_blocks
from src.utils import _scan_runs as _lib_scan_runs

# Re-exported, not called here. These were reachable as server.<name> before
# the library moved out, and callers -- the test suite included -- may still
# reach for them, so this module keeps offering them.
from src.utils import (  # noqa: F401
    RUCIO_NOTE,
    RunSelection,
    _env_bool,
    _env_float,
    _parse_run_range,
    _probe_budget_s,
    _relaxed_patterns,
    _repro_lines,
    _tier_chunk_patterns,
)


host = os.getenv("MCP_HOST", "0.0.0.0")
port = int(os.getenv("MCP_PORT", "8013"))
# Stateful streamable-http retains one transport + one parked task per
# session FOREVER (the mcp SDK has no default session TTL and FastMCP
# passes none), so every probe or client that skips the DELETE leaks
# ~60-120 KiB. Every tool here is unary request/response, so stateless
# is semantically identical and leaks nothing.
mcp = FastMCP(
    "dbs",
    host=host,
    port=port,
    stateless_http=_env_bool("MCP_STATELESS", True),
    json_response=True,
)


@mcp.custom_route("/healthz", methods=["GET"])
async def _healthz(_request: Any) -> Any:
    # Probe target that never touches the MCP session path.
    from starlette.responses import PlainTextResponse

    return PlainTextResponse("ok")


# ---------------------------------------------------------------------------
# Compatibility seam.
#
# The library above is written to take its collaborators as arguments, so it
# can be read and tested on its own. This module is where those arguments are
# supplied, and it is deliberately the ONLY place the following names are
# defined, because they are what the test suite replaces:
#
#   DbsApi        -- swapped for a fake client class
#   _dbs_client   -- swapped for a stub; also cache_clear()ed between tests
#   time          -- time.monotonic is replaced to drive the scan budgets
#
# Anything that resolves one of those must therefore look it up HERE, in this
# module's namespace, at call time. That is why _fresh_client lives here and
# is passed down into the scanners, and why the wrappers below re-resolve
# _dbs_client() on every call instead of binding a client once. Moving any of
# them into the library would silently reconnect the tests to the real DBS.
#
# The names re-exported by the imports above (_bounded, _resolve_runs,
# _run_numbers, _run_exactness, EXACT/UPPER_BOUND/UNPROVEN, ...) are part of
# the same contract: the public surface of this module did not change when the
# library moved out from under it.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _dbs_client() -> Any:
    return build_client(DbsApi)


def _fresh_client() -> Any:
    """A client for one worker thread. The cached one is shared, and the DBS
    client wraps libcurl, which is not safe to drive from several threads."""
    wrapped = getattr(_dbs_client, "__wrapped__", None)
    return wrapped() if wrapped is not None else _dbs_client()


def _public_methods() -> dict[str, Any]:
    return public_methods(_dbs_client())


def _get_method(name: str) -> Any:
    return get_method(_dbs_client(), name)


def _call_dbs_method(method_name: str, kwargs: dict[str, Any] | None = None,
                     payload: Any = None) -> Any:
    return call_dbs_method(_dbs_client(), method_name, kwargs=kwargs, payload=payload)


def _scan_blocks(client: Any, pattern: str, names: list[str],
                 fold: Any = None) -> ScanResult:
    return _lib_scan_blocks(client, pattern, names,
                            fresh_client=_fresh_client, fold=fold)


def _scan_runs(client: Any, dataset: str, runs: list[int],
               valid_only: bool) -> RunScanResult:
    return _lib_scan_runs(client, dataset, runs, valid_only,
                          fresh_client=_fresh_client)


@mcp.tool()
def dbs_server_info() -> Any:
    """Return the configured DBS server's version and instance URL."""
    info = _dbs_client().serverinfo()
    rows = info if isinstance(info, list) else [info]
    return [dict(r, instance=_dbs_instance()) if isinstance(r, dict) else r for r in rows]


@mcp.tool()
def dbs_list_methods() -> list[dict[str, str]]:
    """List public DBS client methods exposed by this MCP server."""
    items = []
    for name, method in sorted(_public_methods().items()):
        summary = inspect.getdoc(method) or ""
        items.append({"name": name, "summary": summary.splitlines()[0] if summary else ""})
    return items


@mcp.tool()
def dbs_method_help(method: str) -> dict[str, str]:
    """Return the local Python DBS client documentation for a method."""
    dbs_method = _get_method(method)
    return {
        "method": method,
        "doc": inspect.getdoc(dbs_method) or "",
    }


@mcp.tool()
def dbs_call(method: str, kwargs: dict[str, Any] | None = None, payload: Any = None) -> Any:
    """Call a READ method on dbs.apis.dbsClient.DbsApi (write methods are blocked).

    Use `kwargs` for parameter-style DBS read methods such as `listDatasets`
    or `listFileSummaries`. Prefer the task tools when one fits: totals and
    per-group sums -> dbs_aggregate; one dataset or block's full picture ->
    dbs_summary.
    """
    try:
        return _bounded(_call_dbs_method(method, kwargs=kwargs, payload=payload))
    except Exception:
        import traceback
        traceback.print_exc()
        raise


@mcp.tool()
def dbs_list_datasets(
    dataset: str | None = None,
    primary_ds_name: str | None = None,
    processed_ds_name: str | None = None,
    data_tier_name: str | None = None,
    dataset_access_type: str | None = None,
    run_num: int | str | list[Any] | None = None,
    detail: bool = False,
) -> Any:
    """List DBS dataset NAMES matching filters.

    Pass dataset_access_type explicitly ('*' for every status): the server
    silently shows VALID only when it is omitted. For counts or totals use
    dbs_aggregate instead of listing and counting here.
    """
    kwargs = _drop_none(
        {
            "dataset": dataset,
            "primary_ds_name": primary_ds_name,
            "processed_ds_name": processed_ds_name,
            "data_tier_name": data_tier_name,
            "dataset_access_type": dataset_access_type,
            "run_num": run_num,
            "detail": detail,
        }
    )
    result = _bounded(_dbs_client().listDatasets(**kwargs))
    if dataset_access_type is None:
        if isinstance(result, str):
            result = f"{result}. {VALID_DEFAULT_NOTE}"
        elif isinstance(result, list):
            result = result + [VALID_DEFAULT_NOTE]
    return result


@mcp.tool()
def dbs_list_files(
    dataset: str | None = None,
    block_name: str | None = None,
    logical_file_name: str | None = None,
    run_num: int | str | list[Any] | None = None,
    detail: bool = False,
    validFileOnly: int | None = None,
) -> Any:
    """List DBS files matching filters.

    Narrow with run_num, block_name or logical_file_name; an unfiltered
    dataset listing can return hundreds of thousands of records. For a
    dataset's file COUNT use dbs_summary.
    """
    kwargs = _drop_none(
        {
            "dataset": dataset,
            "block_name": block_name,
            "logical_file_name": logical_file_name,
            "run_num": run_num,
            "detail": detail,
            "validFileOnly": validFileOnly,
        }
    )
    return _bounded(_dbs_client().listFiles(**kwargs))


@mcp.tool()
def dbs_list_blocks(
    dataset: str | None = None,
    block_name: str | None = None,
    data_tier_name: str | None = None,
    logical_file_name: str | None = None,
    run_num: int | str | list[Any] | None = None,
    detail: bool = False,
) -> Any:
    """List DBS blocks matching filters.

    run_num works here (it does not on the summary endpoints). Note the
    server ignores open_for_writing as a filter and applies no dataset
    status filter. For block counts and sizes use dbs_summary.
    """
    kwargs = _drop_none(
        {
            "dataset": dataset,
            "block_name": block_name,
            "data_tier_name": data_tier_name,
            "logical_file_name": logical_file_name,
            "run_num": run_num,
            "detail": detail,
        }
    )
    return _bounded(_dbs_client().listBlocks(**kwargs))


@mcp.tool()
def dbs_list_runs(
    dataset: str | None = None,
    block_name: str | None = None,
    logical_file_name: str | None = None,
    run_num: int | str | list[Any] | None = None,
) -> Any:
    """List run numbers for a dataset, block, file, or explicit run filter.

    Returns raw run numbers, unsorted, on an all-files basis. For a run
    range plus counts use dbs_summary.
    """
    kwargs = _drop_none(
        {
            "dataset": dataset,
            "block_name": block_name,
            "logical_file_name": logical_file_name,
            "run_num": run_num,
        }
    )
    return _bounded(_dbs_client().listRuns(**kwargs))


@mcp.tool()
def dbs_block_dump(block_name: str) -> Any:
    """Return all DBS information related to a block (large).

    For a block's size, files, events, open flag and origin site, prefer
    dbs_summary with the block name as subject.
    """
    return _bounded(_dbs_client().blockDump(block_name=block_name))


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


VALID_DEFAULT_NOTE = (
    "note: VALID datasets only (server default); "
    "pass dataset_access_type='*' for all statuses"
)


# ---------------------------------------------------------------------------
# Task tools. These do the DBS-specific reasoning once, in code, instead of
# leaving each conversation to rediscover the server's traps.
# ---------------------------------------------------------------------------

# validFileOnly is PRESENCE-checked by the server: sending 0 behaves like 1, so
# the all-files call must omit the key. The flag also silently restricts to
# datasets whose access type is VALID or PRODUCTION.
VALID_SIDE_STATUSES = frozenset({"VALID", "PRODUCTION"})


@mcp.tool()
def dbs_summary(subject: str) -> dict[str, Any]:
    """Full picture of ONE dataset or block: status, size, events, files
    (valid and invalid sides), lumis, blocks and open blocks, activity dates,
    run range and origin sites, with provenance and reproduction commands.

    `subject` is an exact dataset path (/primary/processed/TIER) or a block
    name (a path with a #hash). Use this instead of stitching together
    listDatasets, listFileSummaries and listBlocks by hand. For totals over
    MANY datasets use dbs_aggregate.
    """
    client = _dbs_client()
    calls = 0
    is_block = "#" in subject

    if is_block:
        summaries = client.listBlockSummaries(block_name=subject)
        calls += 1
        blocks = _rows(client.listBlocks(block_name=subject, detail=True))
        calls += 1
        row = _first_row(summaries)
        block_row = blocks[0] if blocks else {}
        created = [b.get("creation_date") for b in blocks if b.get("creation_date")]
        summary: dict[str, Any] = {
            "subject": subject,
            "subject_kind": "block",
            "found": bool(summaries or blocks),
            "dataset": block_row.get("dataset"),
            "bytes_all_files": row.get("file_size"),
            "events_all_files": row.get("num_event"),
            "n_files_all": row.get("num_file"),
            "n_blocks": len(blocks),
            "n_open_blocks": sum(1 for b in blocks if b.get("open_for_writing")),
            "origin_sites": _site_counts(blocks),
            "oldest_block_created": _epoch_to_day(min(created)) if created else None,
            "newest_block_created": _epoch_to_day(max(created)) if created else None,
        }
        return _envelope(summary, subject, "block", calls, status_filter="n/a")

    # Dataset subject. Existence first, with '*' so an invalidated dataset is
    # never mistaken for a missing one (DBS answers 200 + empty list for both).
    found_rows = _rows(client.listDatasets(dataset=subject, dataset_access_type="*", detail=True))
    calls += 1
    if not found_rows:
        summary = {
            "subject": subject,
            "subject_kind": "dataset",
            "found": False,
            "did_you_mean": _did_you_mean(client, subject),
        }
        calls += 1
        return _envelope(summary, subject, "dataset", calls)

    meta = found_rows[0]
    status = meta.get("dataset_access_type")
    all_row = _first_row(client.listFileSummaries(dataset=subject))
    calls += 1

    valid_row: dict[str, Any] = {}
    valid_reason = None
    if status in VALID_SIDE_STATUSES:
        valid_row = _first_row(client.listFileSummaries(dataset=subject, validFileOnly=1))
        calls += 1
    else:
        valid_reason = "gated_by_access_type"

    blocks = _rows(client.listBlocks(dataset=subject, detail=True))
    calls += 1
    runs = _run_numbers(_rows(client.listRuns(dataset=subject)))
    calls += 1
    created = [b.get("creation_date") for b in blocks if b.get("creation_date")]

    n_all = all_row.get("num_file")
    n_valid = valid_row.get("num_file") if valid_row else None
    summary = {
        "subject": subject,
        "subject_kind": "dataset",
        "found": True,
        "status": status,
        "tier": meta.get("data_tier_name") or subject.rstrip("/").split("/")[-1],
        "bytes_all_files": all_row.get("file_size"),
        "bytes_valid_files": valid_row.get("file_size") if valid_row else None,
        "events_all_files": all_row.get("num_event"),
        "events_valid_files": valid_row.get("num_event") if valid_row else None,
        "n_files_all": n_all,
        "n_files_valid": n_valid,
        "n_files_invalid": (n_all - n_valid) if (n_all is not None and n_valid is not None) else None,
        "n_lumis": all_row.get("num_lumi"),
        "n_blocks": len(blocks),
        "n_open_blocks": sum(1 for b in blocks if b.get("open_for_writing")),
        "oldest_block_created": _epoch_to_day(min(created)) if created else None,
        "newest_block_created": _epoch_to_day(max(created)) if created else None,
        "n_runs": len(runs),
        "run_min": runs[0] if runs else None,
        "run_max": runs[-1] if runs else None,
        "runs_basis": "all_files",
        "origin_sites": _site_counts(blocks),
    }
    if valid_reason:
        summary["valid_side_null_reason"] = valid_reason
    if status and status != "VALID":
        summary["invalidated_on"] = _epoch_to_day(meta.get("last_modification_date"))
        summary["invalidated_by"] = meta.get("last_modified_by")

    diagnosis = _diagnose_zero_row(all_row, wildcard_sent="*" in subject,
                                   status=status, flag_sent=False)
    if diagnosis:
        summary["zero_row_diagnosis"] = diagnosis
    return _envelope(summary, subject, "dataset", calls)


@mcp.tool()
def dbs_run_summary(dataset: str, runs: list[int] | str | None = None,
                    first: int | None = None,
                    last: int | None = None) -> dict[str, Any]:
    """Per-run events, files, lumis and bytes for ONE dataset, in one call.

    Use this instead of looping a listing tool run by run. `dataset` is an
    exact path (/primary/processed/TIER). Choose the runs with exactly ONE of:
    `runs` (a list like [326900, 326909], or a range string "326900-327006"
    meaning every run the dataset has inside it), `first` (the N lowest runs),
    `last` (the N highest). Pass none of them for every run.

    ALWAYS read `exactness.verdict` before adding these numbers up. DBS counts
    a file entirely against every run it appears in, so on merged tiers
    (MINIAOD, NANOAOD) one file spans several runs and the per-run numbers are
    upper bounds whose sum double-counts. Measured: a NANOAOD file of 1,059,120
    events is reported in full under BOTH run 315257 and run 315258.
    - "exact": every run was requested and no file is shared. Add them freely.
    - "upper_bound": files are shared. Say so; never present the sum as a total.
    - "unproven": a subset was asked for, or the scan was cut short. Treat the
      numbers as upper bounds and say why.

    `totals.blocks` comes from one range call, never from summing the per-run
    rows: each per-run reply says 1 block, so summing 25 runs gives 25 where
    the truth is 5. Blocks are not an additive field.
    """
    # Validate BEFORE touching the client. _resolve_runs checks this too (it is
    # the contract for direct callers), but by then two DBS calls have already
    # gone out, and the spec's rule is to reject bad input before any HTTP.
    # EVERY selector check lives in the hoisted helper, not just the mutually
    # exclusive pair: first=0, last=-3 and a malformed or backwards range are
    # just as answerable from the arguments alone.
    _validate_run_selector(runs, first, last)

    client = _dbs_client()
    calls = 0

    # Access type first: validFileOnly=1 on a non-VALID dataset returns zeros,
    # and nothing in DBS returns 404, so this doubles as the existence check.
    matches = _rows(client.listDatasets(dataset=dataset,
                                        dataset_access_type="*", detail=True))
    calls += 1
    if not matches:
        return _run_summary_envelope(
            [], {"n_runs": 0},
            {"verdict": UNPROVEN, "reason": "the dataset was not found"},
            {"n_requested": 0, "complete": True},
            dataset, "not applicable", calls,
            f"{dataset} not found at any status. Nothing in DBS returns 404, so "
            "check the name with dbs_summary before reporting that it does not "
            "exist. Do NOT report zero runs.")

    access = matches[0].get("dataset_access_type")
    valid_only = access in VALID_SIDE_STATUSES
    validity = "valid files only" if valid_only else "all files"

    run_rows = _rows(client.listRuns(dataset=dataset))
    calls += 1
    all_runs = _run_numbers(run_rows)
    selection = _resolve_runs(all_runs, runs=runs, first=first, last=last)

    if not selection.requested:
        return _run_summary_envelope(
            [], {"n_runs": 0},
            {"verdict": UNPROVEN, "reason": "no runs matched the selection"},
            {"n_requested": 0, "complete": True,
             "not_in_dataset": selection.not_in_dataset},
            dataset, validity, calls,
            "the dataset exists but no run matched the selection; "
            f"it holds {len(all_runs)} run(s). Do NOT report zero events.")

    scan = _scan_runs(client, dataset, selection.requested, valid_only)
    calls += scan.calls

    low, high = min(selection.requested), max(selection.requested)
    range_kwargs: dict[str, Any] = {"dataset": dataset, "run_num": f"{low}-{high}"}
    if valid_only:
        range_kwargs["validFileOnly"] = 1
    calls += 1
    try:
        range_row = _first_row(client.listFileSummaries(**range_kwargs))
    except Exception:
        range_row = {}

    rows_out = []
    for run in selection.requested:
        row = scan.rows.get(run)
        if row is None:
            continue
        rows_out.append({
            "run": run,
            "events": row.get("num_event") or 0,
            "files": row.get("num_file") or 0,
            "lumis": row.get("num_lumi") or 0,
            "bytes": row.get("file_size") or 0,
        })

    files_summed = sum(row["files"] for row in rows_out)
    files_actual = range_row.get("num_file")
    exactness = _run_exactness(selection, files_summed, files_actual,
                               scan.complete)

    totals: dict[str, Any] = {
        "n_runs": len(rows_out),
        "events": sum(row["events"] for row in rows_out),
        "files": files_actual if files_actual is not None else files_summed,
        "lumis": sum(row["lumis"] for row in rows_out),
        "bytes": sum(row["bytes"] for row in rows_out),
        # Never summed: each per-run reply reports 1 block (golden RL9).
        "blocks": range_row.get("num_block"),
    }

    # Only meaningful when the verdict is EXACT, which already implies every
    # run was requested — so this is always 100.0 and exists to make the "these
    # runs ARE the whole dataset" claim explicit rather than implied.
    if exactness["verdict"] == EXACT and range_row.get("num_event"):
        totals["pct_of_dataset_events"] = round(
            100.0 * totals["events"] / range_row["num_event"], 4)

    coverage: dict[str, Any] = {
        "n_requested": len(selection.requested),
        "n_returned": len(rows_out),
        "complete": scan.complete,
        "not_in_dataset": selection.not_in_dataset,
    }
    # One hint per verdict. Collapsing UPPER_BOUND and UNPROVEN into a single
    # "these are bounds, do not sum them" warning would be an R15-class bug:
    # RL2 asks for a 25-run subset, so its verdict is UNPROVEN, yet its golden
    # answer states all 25 counts AND their total of 212,355 events. A blanket
    # prohibition would make the model refuse the answer the tool got right.
    if not scan.complete:
        coverage["unscanned"] = scan.unscanned
        coverage["failed"] = scan.failed
        totals["partial"] = True
        totals["partial_reason"] = (
            f"{len(scan.unscanned) + len(scan.failed)} of "
            f"{len(selection.requested)} runs were not measured, so these totals "
            "are a floor, not the answer")
        hint = ("PARTIAL — do not present these totals as complete. Re-run "
                "dbs_run_summary with runs=[...] for the runs listed in "
                "coverage.unscanned and coverage.failed, and add the results.")
    elif exactness["verdict"] == UPPER_BOUND:
        hint = ("These per-run numbers are whole-file UPPER BOUNDS: at least one "
                "file is counted under more than one run, which is PROVEN here. "
                "Report them as bounds, quote exactness.reason, and do NOT "
                "present their sum as a total.")
    elif exactness["verdict"] == UNPROVEN:
        hint = ("Only part of the dataset was measured, so exactness could be "
                "neither proven nor disproven. Give every per-run value AND "
                "their total — the sum is the right answer for the runs asked "
                "about. Label it as covering exactly these runs, never as the "
                "dataset total, and pass on exactness.reason as the caveat.")
    else:
        hint = ("per-run numbers are exact here; state the run range and the "
                "validity basis with them")
    if selection.not_in_dataset:
        hint += (" Note: run(s) " +
                 ", ".join(str(r) for r in selection.not_in_dataset) +
                 " are not in this dataset.")

    return _bounded(_run_summary_envelope(rows_out, totals, exactness, coverage,
                                          dataset, validity, calls, hint))


RUNS_API_CAVEAT = ("all files — the runs API has no validFileOnly option, so "
                   "these run counts are not valid-only numbers")


@mcp.tool()
def dbs_run_coverage(datasets: list[str],
                     reference: str | None = None) -> dict[str, Any]:
    """Which runs does one dataset have that the others are missing?

    Give two or more exact dataset paths, usually one processing chain: RAW
    plus its AOD, MINIAOD and NANOAOD. `reference` is the dataset everything
    else is compared against, and defaults to the first one — for a chain
    question that is the RAW tier.

    Returns `covered` — true, false, or NULL with `covered_null_reason` when
    the reference dataset has no runs and there is therefore nothing to cover —
    the run count and range per dataset, the exact runs each non-reference
    dataset lacks, and `identical_sets` grouping datasets whose run sets are
    equal. The set difference is computed here, in code. Do NOT redo it by
    reading run numbers.

    `n_runs_missing_somewhere` is the count to quote: how many DISTINCT
    reference runs are missing from at least one other dataset. Do NOT add up
    `missing[].n_missing` — the same run appears in every tier that lacks it,
    so a four-tier chain missing 24 runs sums to 72.

    A dataset with no runs is named too: in `not_found` if it does not exist at
    any status (a typo — nothing in DBS returns 404), otherwise in
    `empty_datasets`.

    The runs API has no validFileOnly option, so every count is an all-files
    count. Say so with the answer.
    """
    if not isinstance(datasets, list) or len(datasets) < 2:
        raise ValueError(
            "dbs_run_coverage needs at least two datasets to compare; "
            "for one dataset use dbs_summary or dbs_run_summary")
    if reference is not None and reference not in datasets:
        raise ValueError(
            f"reference {reference!r} is not in datasets: {', '.join(datasets)}")

    ref = reference or datasets[0]
    client = _dbs_client()
    calls = 0
    run_sets: dict[str, list[int]] = {}
    for name in datasets:
        rows = _rows(client.listRuns(dataset=name))
        calls += 1
        run_sets[name] = _run_numbers(rows)

    # An empty run set is the one ambiguous answer here: a real dataset nobody
    # has processed yet, or a name that does not exist. Nothing in DBS returns
    # 404, so only a status '*' lookup tells them apart — the same check
    # dbs_run_summary makes. Done once, and only for the datasets that came
    # back empty: a dataset that returned runs has already proved it exists, so
    # the common call costs nothing extra.
    empty = [name for name in datasets if not run_sets[name]]
    not_found = []
    for name in empty:
        calls += 1
        if not _rows(client.listDatasets(dataset=name,
                                         dataset_access_type="*")):
            not_found.append(name)
    empty_datasets = [name for name in empty if name not in not_found]

    cap = _env_int("DBS_MAX_MISSING_RUNS_LISTED", 500)
    ref_runs = set(run_sets[ref])

    out_datasets = []
    for name in datasets:
        runs_here = run_sets[name]
        out_datasets.append({
            "dataset": name,
            "n_runs": len(runs_here),
            "run_min": runs_here[0] if runs_here else None,
            "run_max": runs_here[-1] if runs_here else None,
        })

    missing = []
    gap_union: set[int] = set()
    for name in datasets:
        if name == ref:
            continue
        gap = sorted(ref_runs - set(run_sets[name]))
        if not gap:
            continue
        # Built from the UNCAPPED gap, never from row["runs"] below: the count
        # must not change because the display list was sliced.
        gap_union.update(gap)
        row: dict[str, Any] = {"dataset": name, "n_missing": len(gap),
                               "runs": gap[:cap]}
        if len(gap) > cap:
            row["truncated"] = (f"{len(gap) - cap} run number(s) withheld; "
                                "n_missing is the exact count")
        missing.append(row)

    seen: dict[tuple[int, ...], list[str]] = {}
    for name in datasets:
        seen.setdefault(tuple(run_sets[name]), []).append(name)
    identical = [names for names in seen.values() if len(names) > 1]

    # DISTINCT runs, never the sum of n_missing: on a four-tier chain the same
    # 24 runs are missing from AOD, MINIAOD and NANOAOD, and summing calls it
    # 72. This is the number the hint tells the model to give, and RL6's whole
    # question.
    n_missing_somewhere = len(gap_union)

    # A reference with no runs cannot be "covered": there is nothing to cover.
    # It is null with a reason, not false (nothing is missing either) and not a
    # raise (an empty dataset is a data state, not bad input).
    ref_empty = not run_sets[ref]
    covered = None if ref_empty else not missing

    # The verdict and the empty-dataset warning are COMPOSED, never a ladder:
    # a chain genuinely missing 153 runs must still be described as missing 153
    # runs when one of its datasets is also empty.
    if ref_empty:
        verdict = (f"COVERAGE UNKNOWN: the reference {ref} has no runs, so "
                   "'covered' is null — NOT true. Say the comparison could not "
                   "be made and why; do not report full coverage.")
    elif missing:
        verdict = (f"NOT fully covered: {n_missing_somewhere} run(s) of {ref} "
                   "never reach at least one other dataset. Give that count "
                   "(n_runs_missing_somewhere) and list the runs; they are "
                   "already computed here. Do NOT add up missing[].n_missing — "
                   "a run missing from three tiers is still one run.")
    else:
        verdict = (f"every run of {ref} appears in all the other datasets. State "
                   "the run counts and the all-files basis.")

    caveats = []
    if not_found:
        caveats.append("NOT FOUND at any status: " + ", ".join(not_found) +
                        " — nothing in DBS returns 404, so an empty run set "
                        "here means the name did not match anything. Check it "
                        "with dbs_summary before drawing any conclusion.")
    if empty_datasets:
        caveats.append("no runs found for " + ", ".join(empty_datasets) +
                        " — present in the catalog at some status, but the "
                        "runs API returned nothing. Do NOT read that as zero "
                        "events.")
    hint = " ".join([verdict] + caveats)

    base = _dbs_instance().rstrip("/")
    payload: dict[str, Any] = {
        "covered": covered,
        "reference": ref,
        "datasets": out_datasets,
        "missing": missing,
        "n_runs_missing_somewhere": n_missing_somewhere,
        "identical_sets": identical,
        "provenance": {
            "instance": _dbs_instance(),
            "datasets": datasets,
            "validity_basis": RUNS_API_CAVEAT,
            "queried_utc": _utc_now(),
            "n_server_calls": calls,
        },
        "repro": [
            "for D in " + " ".join(datasets) +
            "; do dasgoclient --query \"run dataset=$D\" | wc -l; done",
            f"curl -s '{base}/runs?dataset={ref}' "
            "--cert $X509_USER_PROXY --key $X509_USER_PROXY",
        ],
        "hint": hint,
    }
    if covered is None:
        payload["covered_null_reason"] = (
            f"the reference {ref} has no runs, so there is nothing for the "
            "other datasets to cover" +
            (" — and it was not found at any status, so the name is probably "
             "wrong" if ref in not_found else ""))
    if not_found:
        payload["not_found"] = not_found
    if empty_datasets:
        payload["empty_datasets"] = empty_datasets
    return _bounded(payload)


def _site_counts(blocks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in blocks:
        site = block.get("origin_site_name")
        if site:
            counts[site] = counts.get(site, 0) + 1
    return counts


GROUP_KEYS = ("auto", "none", "tier", "stream", "version", "status")
DEFAULT_METRICS = ("count", "bytes", "files", "blocks")
VALID_METRICS = ("count", "bytes", "files", "blocks", "events")


def _group_of(dataset: str, row: dict[str, Any], key: str) -> str:
    parts = dataset.strip("/").split("/")
    if key == "tier":
        return parts[2] if len(parts) == 3 else "unknown"
    if key == "stream":
        return parts[0] if parts else "unknown"
    if key == "version":
        processed = parts[1] if len(parts) == 3 else ""
        return processed.rsplit("-", 1)[-1] if "-" in processed else (processed or "unknown")
    if key == "status":
        return row.get("dataset_access_type") or "unknown"
    return "all"


@mcp.tool()
def dbs_aggregate(
    pattern: str,
    status: str = "VALID",
    group_by: str = "auto",
    metrics: list[str] | None = None,
    count_only: bool = False,
) -> dict[str, Any]:
    """Totals and per-group sums over MANY datasets, computed here, not in chat.

    Answers questions like "how much data is in this era, by tier" or "how
    many datasets match this pattern". `pattern` is a dataset wildcard such as
    /*/HIRun2026A*/AOD. `status` must be explicit ('VALID', 'INVALID', '*',
    ...) because the DBS default silently hides everything that is not VALID.
    Explicit is not the same as widest. The split is catalog versus size:
    counting or naming DATASETS is a catalog question, so use '*' — a dataset
    that exists still exists after it is invalidated. Adding up BYTES, EVENTS
    or FILES is a size question, so use 'VALID' — non-valid datasets are
    failed and superseded attempts. Both mistakes are large and measured: '*'
    on a how-big question came out nine times too high, and 'VALID' on a
    how-many-datasets question left out a third of the catalog.
    `group_by` is auto|none|tier|stream|version|status. Dataset, byte, file
    and block counts always come back together, because one block scan pays
    for all four; add "events" to `metrics` to also pay one call per dataset
    for event counts. An unknown metric name is an error, never ignored. Set
    `count_only` for a pure count with no block scan at all.

    If the pattern matches nothing, the reply carries `did_you_mean`: wider
    patterns that DO hold data. Re-run with one of those instead of reporting
    zero.

    The reply's size follows the number of GROUPS, never the number of
    datasets, so it is safe on a whole era. Eras are selected by name pattern:
    there is deliberately no era-name parameter, because the server ignores
    that filter and answers with the entire catalog.
    """
    if group_by not in GROUP_KEYS:
        raise ValueError(f"group_by must be one of {', '.join(GROUP_KEYS)}")
    unknown = [m for m in (metrics or ()) if m not in VALID_METRICS]
    if unknown:
        # Accepting a metric and ignoring it is exactly the DBS behavior this
        # server exists to stop. Fail loudly, before spending a call.
        raise ValueError(
            f"unknown metric(s): {', '.join(unknown)}. "
            f"Valid metrics are {', '.join(VALID_METRICS)}."
        )
    # count, bytes, files and blocks all fall out of the same single block
    # scan, so they are always returned; `metrics` only decides whether to pay
    # the per-dataset cost of events.
    wanted = list(DEFAULT_METRICS)
    if "events" in (metrics or ()):
        wanted.append("events")
    client = _dbs_client()
    calls = 0

    # detail=1 returns the whole dataset record instead of the name, and on a
    # census that is the difference between 8s and 64s (measured live
    # 2026-08-12 over 94,976 NanoAODv9 datasets). Only two things read those
    # row fields: grouping by status, which needs dataset_access_type, and the
    # sizing path below, whose events branch reads dataset_access_type per
    # dataset to decide validFileOnly. A count_only run does neither, unless
    # it groups by status. tier, stream and version are all read off the
    # dataset name, and so is group_by='auto', which resolves through tier.
    # The flag is omitted rather than sent as False, which is how every other
    # name-only call here asks (see dbs_run_coverage): no assumption is then
    # needed about how the client serialises a false flag onto the query
    # string, or how dbs2go reads it back.
    ds_kwargs: dict[str, Any] = {"dataset": pattern, "dataset_access_type": status}
    if (not count_only) or group_by == "status":
        ds_kwargs["detail"] = True
    rows = _rows(client.listDatasets(**ds_kwargs))
    calls += 1
    names = [r.get("dataset") for r in rows if r.get("dataset")]
    by_name = {r.get("dataset"): r for r in rows}
    n_matched = len(names)

    if group_by == "auto":
        tiers = {_group_of(n, by_name[n], "tier") for n in names}
        group_by = "tier" if len(tiers) > 1 else "none"

    groups: dict[str, dict[str, Any]] = {}
    for name in names:
        key = _group_of(name, by_name[name], group_by)
        groups.setdefault(key, {"group": key, "n_datasets": 0, "datasets": []})
        groups[key]["n_datasets"] += 1
        groups[key]["datasets"].append(name)

    coverage = {"n_matched": n_matched, "n_summed": 0, "n_failed": 0,
                "complete": True, "truncation_reason": None}

    if count_only or n_matched == 0:
        out_groups = [{"group": g["group"], "n_datasets": g["n_datasets"]}
                      for g in groups.values()]
        totals = {"n_datasets": n_matched}
        suggestions: list[dict[str, Any]] = []
        if n_matched == 0:
            suggestions, probe_calls, timed_out = _probe_wider_patterns(
                client, pattern, status)
            calls += probe_calls
            hint = (f"no datasets match this pattern at status {status}; "
                    "check the name or widen the status")
            if timed_out and not suggestions:
                hint += " (gave up probing wider names: time budget spent)"
            if suggestions:
                best = max(suggestions, key=lambda s: s["n_datasets"])
                hint = (f"no datasets match {pattern} at status {status}, but "
                        f"{best['pattern']} matches {best['n_datasets']}. "
                        "Re-run with that pattern; do NOT report zero.")
        else:
            hint = ("counts come from dataset names; ask for bytes to pay for "
                    "the block scan")
        envelope = _agg_envelope(out_groups, totals, coverage, pattern, status,
                                 calls, hint)
        if n_matched == 0:
            envelope["did_you_mean"] = suggestions
        return envelope

    # Sizes: one status-blind blocks scan per pattern, intersected client-side
    # against the resolved names. The blocks API applies NO status filter, so
    # skipping the intersect overcounts by an unbounded factor.
    wanted_set = set(names)

    # Folded in the scan workers: an era's block dicts (~300 MB) used to be
    # accumulated in full and walked here, only to become three integers per
    # group. Each worker now reduces its own rows and frees them.
    counters: dict[str, dict[str, int]] = {}

    def _fold(rows: list[dict[str, Any]]) -> None:
        for block in rows:
            owner = block.get("dataset")
            if owner not in wanted_set:
                continue
            key = _group_of(owner, by_name[owner], group_by)
            c = counters.setdefault(key, {"bytes": 0, "files": 0, "n_blocks": 0})
            c["bytes"] += block.get("block_size") or 0
            c["files"] += block.get("file_count") or 0
            c["n_blocks"] += 1

    scan = _scan_blocks(client, pattern, names, fold=_fold)
    calls += scan.calls
    missing_chunks = scan.unscanned + [f.split(":", 1)[0] for f in scan.failed]
    missing_tiers = {c.rsplit("/", 1)[-1] for c in missing_chunks}
    if not scan.complete:
        coverage["complete"] = False
        coverage["n_failed"] = len(missing_chunks)
        parts = []
        if scan.unscanned:
            parts.append("time budget spent before scanning "
                         + ", ".join(sorted(scan.unscanned)))
        if scan.failed:
            parts.append("DBS refused " + "; ".join(scan.failed))
        coverage["truncation_reason"] = "; ".join(parts)
    for key, c in counters.items():
        bucket = groups.setdefault(key, {"group": key, "n_datasets": 0, "datasets": []})
        bucket.update(c)
    coverage["n_summed"] = n_matched

    events_total = None
    events_reason = None
    if "events" in wanted:
        cap = _env_int("DBS_MAX_DATASETS_SUMMED", 60)
        if n_matched > cap:
            events_reason = (
                f"{n_matched} datasets exceed the {cap}-dataset event cap; events need one "
                "call per dataset. Narrow the pattern (for example per tier) to get them."
            )
        else:
            events_total = 0
            for name in names:
                row = by_name[name]
                kwargs: dict[str, Any] = {"dataset": name}
                # validFileOnly is presence-checked and gates non-VALID datasets,
                # so it is sent only where it is both meaningful and harmless.
                if row.get("dataset_access_type") in VALID_SIDE_STATUSES and status != "*":
                    kwargs["validFileOnly"] = 1
                summary = _first_row(client.listFileSummaries(**kwargs))
                calls += 1
                events_total += summary.get("num_event") or 0
                key = _group_of(name, row, group_by)
                bucket = groups.setdefault(key, {"group": key, "n_datasets": 0, "datasets": []})
                bucket["events"] = bucket.get("events", 0) + (summary.get("num_event") or 0)

    # A group whose tier was never scanned has no size data. Reporting 0 there
    # would be a number a reader adds up; null with a flag cannot be.
    def _unscanned(group_key: str, member_names: list[str]) -> bool:
        if not missing_tiers:
            return False
        tiers = {n.rsplit("/", 1)[-1] for n in member_names}
        return bool(tiers) and tiers <= missing_tiers

    out_groups = []
    for g in sorted(groups.values(), key=lambda x: -x["n_datasets"]):
        row = {"group": g["group"], "n_datasets": g["n_datasets"]}
        blind = _unscanned(g["group"], g.get("datasets", []))
        for metric, field in (("bytes", "bytes"), ("files", "files"), ("blocks", "n_blocks")):
            if metric in wanted:
                row[field] = None if blind else g.get(field, 0)
        if "events" in wanted and events_total is not None:
            row["events"] = g.get("events", 0)
        if blind:
            row["scanned"] = False
        out_groups.append(row)

    totals: dict[str, Any] = {"n_datasets": n_matched}
    if "bytes" in wanted:
        totals["bytes"] = sum(g.get("bytes", 0) for g in groups.values())
    if "files" in wanted:
        totals["files"] = sum(g.get("files", 0) for g in groups.values())
    if "blocks" in wanted:
        totals["blocks"] = sum(g.get("n_blocks", 0) for g in groups.values())
    if "events" in wanted:
        totals["events"] = events_total
        if events_reason:
            totals["events_null_reason"] = events_reason

    tiers_present = {_group_of(n, by_name[n], "tier") for n in names}
    hint = "bytes and files come from block records; events need one call per dataset"
    if "events" in wanted and len(tiers_present) > 1:
        hint = ("events across more than one tier double-count the same physics events; "
                "sum events within one tier only")

    if missing_chunks:
        follow_up = ", ".join(sorted(set(missing_chunks)))
        totals["partial"] = True
        totals["bytes_partial_reason"] = (
            f"{len(missing_chunks)} of {len(tiers_present)} tiers were not scanned, "
            "so this total is a floor, not the answer")
        if set(missing_chunks) == {pattern}:
            # Nothing was split, so repeating the same pattern would just stall
            # again. Narrowing the primary name is the only way down.
            head = pattern.strip("/").split("/")[0].rstrip("*")
            example = "/" + "/".join([(head or "") + "A*"]
                                     + pattern.strip("/").split("/")[1:])
            totals["bytes_partial_reason"] = (
                "this pattern is too large to size inside the time budget")
            hint = ("NO SIZE DATA — the scan did not finish and nothing here is a "
                    f"total. {pattern} cannot be sized in one call. Narrow the "
                    f"primary dataset name and sum the parts, e.g. {example}, or "
                    "size one dataset at a time with dbs_summary.")
        else:
            hint = ("PARTIAL TOTAL — do not present it as the total. Missing tiers: "
                    f"{follow_up}. Call dbs_aggregate once per missing pattern and add "
                    "the results, or say which tiers are missing.")

    # The singleton client pins the raw body of its last response (an era
    # listing is tens of MB); hand the freed arenas back to the kernel too,
    # or the scan's peak stays resident as glibc arena high-water.
    _release_response(client)
    _malloc_trim()
    return _agg_envelope(out_groups, totals, coverage, pattern, status, calls, hint)


def _malloc_trim() -> None:
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def main() -> None:
    mcp.run(
        transport="streamable-http",
    )


if __name__ == "__main__":
    main()
