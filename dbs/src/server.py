"""MCP tools for the CMS DBS3 Python client."""

from __future__ import annotations

import inspect
import os
from functools import lru_cache
from typing import Any
from dbs.apis.dbsClient import DbsApi

from mcp.server.fastmcp import FastMCP


DEFAULT_DBS_URL = "https://cmsweb.cern.ch/dbs/prod/global/DBSReader/"
# This server is read-only: only these DbsApi methods are reachable, checked
# before any HTTP. (The old write-prefix filter was never applied anywhere.)
READ_METHOD_ALLOWLIST = frozenset(
    {
        "serverinfo",
        "help",
        "blockDump",
        "listDatasets",
        "listFiles",
        "listBlocks",
        "listRuns",
        "listRunSummaries",
        "listFileSummaries",
        "listBlockSummaries",
        "listFileLumis",
        "listDatasetParents",
        "listDatasetChildren",
        "listBlockParents",
        "listBlockChildren",
        "listFileParents",
        "listFileChildren",
        "listBlockOrigin",
        "listAcquisitionEras",
        "listProcessingEras",
        "listDataTiers",
        "listDataTypes",
        "listPhysicsGroups",
        "listDatasetAccessTypes",
        "listPrimaryDatasets",
        "listPrimaryDSTypes",
        "listReleaseVersions",
        "listOutputConfigs",
    }
)

host = os.getenv("MCP_HOST", "0.0.0.0")
port = int(os.getenv("MCP_PORT", "8013"))
mcp = FastMCP("dbs", host=host, port=port)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


@lru_cache(maxsize=1)
def _dbs_client() -> Any:
    dbs_client = DbsApi(
        url=os.getenv("DBS_URL", DEFAULT_DBS_URL),
        proxy=os.getenv("DBS_PROXY") or None,
        key=os.getenv("X509_USER_PROXY") or None,
        cert=os.getenv("X509_USER_PROXY") or None,
        verifypeer=_env_bool("DBS_VERIFY_PEER", True),
        debug=1 if _env_bool("DBS_DEBUG", False) else 0,
        ca_info=os.getenv("X509_CERT_DIR") or None,
        userAgent=os.getenv("DBS_USER_AGENT", "dbs-mcp"),
        port=_env_int("DBS_PORT", 8443),
        accept=os.getenv("DBS_ACCEPT", "application/json"),
        aggregate=_env_bool("DBS_AGGREGATE", True),
        useGzip=_env_bool("DBS_USE_GZIP", False),
    )
    
    return dbs_client


def _public_methods() -> dict[str, Any]:
    client = _dbs_client()
    methods: dict[str, Any] = {}
    for name in dir(client):
        if name.startswith("_") or name not in READ_METHOD_ALLOWLIST:
            continue
        attr = getattr(client, name)
        if callable(attr):
            methods[name] = attr
    return methods


def _get_method(name: str) -> Any:
    methods = _public_methods()
    try:
        return methods[name]
    except KeyError as exc:
        available = ", ".join(sorted(methods))
        raise ValueError(
            f"DBS method {name!r} is not available: this server is read-only. "
            f"Allowed methods: {available}"
        ) from exc


def _call_dbs_method(method_name: str, kwargs: dict[str, Any] | None = None, payload: Any = None) -> Any:
    method = _get_method(method_name)
    kwargs = kwargs or {}

    try:
        if payload is not None:
            if method_name == "insertFiles":
                result = method(payload, **kwargs)
            elif kwargs:
                raise ValueError("Use either `payload` for object-style calls or `kwargs` for parameter calls, not both.")
            else:
                result = method(payload)
        else:
            result = method(**kwargs)
        return result

    except Exception as e:
        client = getattr(method, '__self__', None)
        if client is not None:
            http_resp = getattr(client, 'http_response', None)
            if http_resp is not None:
                status  = getattr(http_resp, 'status',  'N/A')
                reason  = getattr(http_resp, 'reason',  'N/A')
                body    = getattr(http_resp, 'data',    None) \
                       or getattr(http_resp, 'read',    None)
                print(f"[DBS DEBUG] method   : {method_name}",        flush=True)
                print(f"[DBS DEBUG] kwargs   : {kwargs}",             flush=True)
                print(f"[DBS DEBUG] status   : {status} {reason}",    flush=True)
                print(f"[DBS DEBUG] body     : {body!r}",             flush=True)
            else:
                print(f"[DBS DEBUG] http_response attribute was None on client", flush=True)
        else:
            print(f"[DBS DEBUG] Could not retrieve client from method {method_name}", flush=True)
        raise


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


def _bounded(result: Any) -> Any:
    """Empty results say so; oversized results are cut with an honest note."""
    import json

    if isinstance(result, list) and not result:
        return "0 rows matched"
    cap = _env_int("DBS_RESULT_CAP_BYTES", 262144)
    if isinstance(result, list):
        try:
            size = len(json.dumps(result, default=str))
        except (TypeError, ValueError):
            return result
        if size <= cap:
            return result
        total = len(result)
        kept: list[Any] = []
        used = 2
        for row in result:
            row_size = len(json.dumps(row, default=str)) + 2
            if used + row_size > cap:
                break
            kept.append(row)
            used += row_size
        kept.append(
            f"truncated: showing {len(kept)} of {total} records; "
            "narrow the query for the rest"
        )
        return kept
    try:
        size = len(json.dumps(result, default=str))
    except (TypeError, ValueError):
        return result
    if size <= cap:
        return result
    return (
        f"truncated: the full object is {size} bytes, above the "
        f"{cap}-byte result cap; use a narrower tool or query"
    )


# ---------------------------------------------------------------------------
# Task tools. These do the DBS-specific reasoning once, in code, instead of
# leaving each conversation to rediscover the server's traps.
# ---------------------------------------------------------------------------

# validFileOnly is PRESENCE-checked by the server: sending 0 behaves like 1, so
# the all-files call must omit the key. The flag also silently restricts to
# datasets whose access type is VALID or PRODUCTION.
VALID_SIDE_STATUSES = frozenset({"VALID", "PRODUCTION"})
RUCIO_NOTE = (
    "origin_site is bookkeeping history (where blocks were produced or injected), "
    "not current location; ask Rucio for replicas."
)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_to_day(value: Any) -> str | None:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def _first_row(rows: Any) -> dict[str, Any]:
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _dbs_instance() -> str:
    return os.getenv("DBS_URL", DEFAULT_DBS_URL)


def _repro_lines(subject: str, kind: str) -> list[str]:
    base = _dbs_instance().rstrip("/")
    if kind == "block":
        quoted = subject.replace("#", "%23")
        return [
            f"dasgoclient --query 'summary block={subject}'",
            f"curl -s '{base}/blocksummaries?block_name={quoted}' --cert $X509_USER_PROXY --key $X509_USER_PROXY",
        ]
    return [
        f"dasgoclient --query 'summary dataset={subject}'",
        f"curl -s '{base}/filesummaries?dataset={subject}' --cert $X509_USER_PROXY --key $X509_USER_PROXY",
    ]


def _did_you_mean(client: Any, dataset: str, limit: int = 10) -> list[str]:
    """One bounded probe: keep the primary dataset name, widen the rest."""
    parts = dataset.strip("/").split("/")
    if len(parts) != 3:
        return []
    pattern = f"/{parts[0]}/*/{parts[2]}"
    try:
        rows = client.listDatasets(dataset=pattern, dataset_access_type="*")
    except Exception:
        return []
    return [row.get("dataset") for row in _rows(rows) if row.get("dataset")][:limit]


def _relaxed_patterns(pattern: str) -> list[str]:
    """Widen one path segment at a time by appending a wildcard.

    /HIForward/Era-v1/AOD -> /HIForward*/Era-v1/AOD, /HIForward/Era-v1*/AOD,
    /HIForward/Era-v1/AOD*. Segments that already carry a wildcard are left
    alone, so a fully wildcarded pattern produces no probes at all.
    """
    parts = pattern.strip("/").split("/")
    if len(parts) != 3:
        return []
    out = []
    for i, part in enumerate(parts):
        if not part or "*" in part:
            continue
        widened = list(parts)
        widened[i] = part + "*"
        out.append("/" + "/".join(widened))
    return out


def _probe_wider_patterns(client: Any, pattern: str,
                          status: str) -> tuple[list[dict[str, Any]], int]:
    """Which wider patterns actually hold data? Returns (suggestions, calls).

    A zero result is the one answer a model cannot check, so it invents a
    reason instead. Handing back a pattern that DOES match turns a dead end
    into a next step. Bounded: at most one probe per path segment.
    """
    found: list[dict[str, Any]] = []
    calls = 0
    for candidate in _relaxed_patterns(pattern):
        calls += 1
        try:
            rows = _rows(client.listDatasets(dataset=candidate,
                                             dataset_access_type=status))
        except Exception:
            continue
        names = [r.get("dataset") for r in rows if r.get("dataset")]
        if names:
            found.append({"pattern": candidate, "n_datasets": len(names),
                          "example": sorted(names)[0]})
    return found, calls


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
    runs = sorted({r.get("run_num") for r in _rows(client.listRuns(dataset=subject))
                   if r.get("run_num") is not None})
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


def _site_counts(blocks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in blocks:
        site = block.get("origin_site_name")
        if site:
            counts[site] = counts.get(site, 0) + 1
    return counts


def _diagnose_zero_row(row: dict[str, Any], *, wildcard_sent: bool,
                       status: str | None, flag_sent: bool) -> str | None:
    """An all-zero summary row is usually a trap, not data. num_block tells which."""
    if not row or any(row.get(k) for k in ("file_size", "num_file", "num_event")):
        return None
    if row.get("num_block"):
        return ("zeros with a non-zero block count: the validFileOnly gate gated this "
                "dataset (its access type is outside VALID/PRODUCTION)")
    if wildcard_sent:
        return ("zeros with no blocks: a wildcard reached a summaries API, which returns "
                "an all-zero row instead of an error; query one exact dataset")
    return "zeros with no blocks: nothing matched this exact path"


def _envelope(summary: dict[str, Any], subject: str, kind: str, calls: int,
              status_filter: str = "*") -> dict[str, Any]:
    return {
        "summary": summary,
        "provenance": {
            "instance": _dbs_instance(),
            "status_filter": status_filter,
            "validity_basis": "both_sides_reported",
            "queried_utc": _utc_now(),
            "n_server_calls": calls,
        },
        "repro": _repro_lines(subject, kind),
        "note": RUCIO_NOTE,
    }


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

    rows = _rows(client.listDatasets(dataset=pattern, dataset_access_type=status, detail=True))
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
            suggestions, probe_calls = _probe_wider_patterns(client, pattern, status)
            calls += probe_calls
            hint = (f"no datasets match this pattern at status {status}; "
                    "check the name or widen the status")
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
    blocks = _rows(client.listBlocks(dataset=pattern, detail=True))
    calls += 1
    for block in blocks:
        owner = block.get("dataset")
        if owner not in wanted_set:
            continue
        key = _group_of(owner, by_name[owner], group_by)
        bucket = groups.setdefault(key, {"group": key, "n_datasets": 0, "datasets": []})
        bucket["bytes"] = bucket.get("bytes", 0) + (block.get("block_size") or 0)
        bucket["files"] = bucket.get("files", 0) + (block.get("file_count") or 0)
        bucket["n_blocks"] = bucket.get("n_blocks", 0) + 1
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

    out_groups = []
    for g in sorted(groups.values(), key=lambda x: -x["n_datasets"]):
        row = {"group": g["group"], "n_datasets": g["n_datasets"]}
        for metric, field in (("bytes", "bytes"), ("files", "files"), ("blocks", "n_blocks")):
            if metric in wanted:
                row[field] = g.get(field, 0)
        if "events" in wanted and events_total is not None:
            row["events"] = g.get("events", 0)
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
    return _agg_envelope(out_groups, totals, coverage, pattern, status, calls, hint)


def _agg_envelope(groups: list[dict[str, Any]], totals: dict[str, Any],
                  coverage: dict[str, Any], pattern: str, status: str,
                  calls: int, hint: str) -> dict[str, Any]:
    base = _dbs_instance().rstrip("/")
    return {
        "groups": groups,
        "totals": totals,
        "coverage": coverage,
        "provenance": {
            "instance": _dbs_instance(),
            "pattern": pattern,
            "status_filter": status,
            "queried_utc": _utc_now(),
            "n_server_calls": calls,
        },
        "repro": [
            f"dasgoclient --query 'dataset dataset={pattern} status={status}'",
            f"curl -s '{base}/blocks?dataset={pattern}&detail=1' "
            "--cert $X509_USER_PROXY --key $X509_USER_PROXY",
        ],
        "hint": hint,
    }


def main() -> None:
    mcp.run(
        transport="streamable-http",
    )


if __name__ == "__main__":
    main()
