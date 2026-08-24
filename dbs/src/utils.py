"""The DBS server's library half.

server.py is the MCP layer: tool declarations, their model-facing docstrings,
and argument handling. Everything it calls lives here, and nothing here
imports mcp or server.py.
"""

from __future__ import annotations

import os
import time
from typing import Any



# ---------------------------------------------------------------------------
# client
# Building and calling the DBS client.
#
# Everything that knows how to reach DBS: the connection, the read-only method
# allowlist, and the environment knobs. Nothing here imports mcp or server --
# the dependency runs one way, server.py -> utils.
#
# The DbsApi class is NOT imported here. build_client takes it as an argument
# so that server.py owns the name, which is the seam the gzip tests replace.
#
# ---------------------------------------------------------------------------

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


def _dbs_instance() -> str:
    return os.getenv("DBS_URL", DEFAULT_DBS_URL)


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


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc


def build_client(api_class: Any) -> Any:
    """Construct a DBS client. `api_class` is DbsApi, passed in by server.py."""
    dbs_client = api_class(
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

    # Ask cmsweb to compress its RESPONSES. The client never does, so every
    # listing streams raw at ~64-147 KB/s (measured 2026-08-12: identical rows
    # 0.86s gzipped vs 7.6-17.4s plain; a 14MB listing 357KB/~4.6s vs ~180s).
    # `useGzip` above cannot do this -- dbsClient gates it on
    # `callmethod == 'POST'` and only sets Content-Encoding on the request
    # BODY. RestApi applies _additional_curl_options to every transfer.
    try:
        import pycurl
    except ImportError:  # offline/test env without pycurl: leave the client as is
        pass
    else:
        options = getattr(
            getattr(dbs_client, "rest_api", None), "_additional_curl_options", None
        )
        if options is not None:
            options[pycurl.ACCEPT_ENCODING] = "gzip"
            # A hang guard, and only that. Without it a transfer that stops
            # making progress holds FastMCP's single event loop forever,
            # because these tools are sync and run inline on it -- one dead
            # socket then stalls every later call on the server.
            #
            # The number is deliberately far above any legitimate call: the
            # slowest real one measured is the 133,231-block ALCARECO scan at
            # 77-107s (2026-08-12, compressed). It is NOT tuned to a caller's
            # deadline. A cap set just under one would abort that scan, which
            # is a correct call doing exactly what it was asked to do.
            options[pycurl.TIMEOUT] = _env_int("DBS_CURL_TIMEOUT_S", 240)

    return dbs_client


def public_methods(client: Any) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for name in dir(client):
        if name.startswith("_") or name not in READ_METHOD_ALLOWLIST:
            continue
        attr = getattr(client, name)
        if callable(attr):
            methods[name] = attr
    return methods


def get_method(client: Any, name: str) -> Any:
    methods = public_methods(client)
    try:
        return methods[name]
    except KeyError as exc:
        available = ", ".join(sorted(methods))
        raise ValueError(
            f"DBS method {name!r} is not available: this server is read-only. "
            f"Allowed methods: {available}"
        ) from exc


def call_dbs_method(client: Any, method_name: str, kwargs: dict[str, Any] | None = None,
                    payload: Any = None) -> Any:
    method = get_method(client, method_name)
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


# ---------------------------------------------------------------------------
# runs
# Run selectors, run numbers, and whether per-run totals can be added up.
# ---------------------------------------------------------------------------

def _run_numbers(rows: list[dict[str, Any]]) -> list[int]:
    """Flatten listRuns() rows into a sorted list of unique run numbers.

    Handles both run_num shapes: a list in ONE row, which is what the live
    aggregating server returns, and one row per run. Assuming only the second
    crashed both run tools live on "unhashable type: 'list'".
    """
    numbers: set[int] = set()
    for row in rows:
        value = row.get("run_num")
        if value is None:
            continue
        if isinstance(value, list):
            numbers.update(int(v) for v in value if v is not None)
        else:
            numbers.add(int(value))
    return sorted(numbers)


class RunSelection:
    """Which runs were asked for, and which of them the dataset actually has."""

    def __init__(self, requested: list[int], not_in_dataset: list[int],
                 all_runs: list[int]) -> None:
        self.requested = requested
        self.not_in_dataset = not_in_dataset
        self.all_runs = all_runs

    @property
    def is_all_runs(self) -> bool:
        return bool(self.requested) and set(self.requested) == set(self.all_runs)

    @property
    def is_dense_range(self) -> bool:
        """True when the requested runs are EVERY dataset run in [min, max],
        which is the only case where a range total is comparable to the sum."""
        if not self.requested:
            return False
        low, high = min(self.requested), max(self.requested)
        inside = [run for run in self.all_runs if low <= run <= high]
        return set(inside) == set(self.requested)


def _parse_run_range(text: str) -> tuple[int, int]:
    parts = text.split("-")
    if len(parts) != 2:
        raise ValueError(f"run range must look like 'lo-hi', got {text!r}")
    try:
        low, high = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"run range must be two integers, got {text!r}") from exc
    if low > high:
        raise ValueError(f"run range {text!r} runs backwards: {low} > {high}")
    return low, high


def _validate_run_selector(runs: list[int] | str | None,
                           first: int | None, last: int | None) -> list[str]:
    """Reject a bad run selector before any DBS call, and return the selector
    names actually supplied. Every check reads the arguments alone."""
    given = [name for name, value in
             (("runs", runs), ("first", first), ("last", last))
             if value is not None]
    if len(given) > 1:
        raise ValueError(
            "pass only one of runs, first, last — got " + ", ".join(given))
    if first is not None and first < 1:
        raise ValueError(f"first must be 1 or more, got {first}")
    if last is not None and last < 1:
        raise ValueError(f"last must be 1 or more, got {last}")
    if isinstance(runs, str):
        _parse_run_range(runs)          # raises on malformed or backwards
    elif runs is not None:
        for value in runs:
            try:
                int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"run numbers must be integers, got {value!r}") from exc
    return given


def _resolve_runs(all_runs: list[int], runs: list[int] | str | None = None,
                  first: int | None = None,
                  last: int | None = None) -> RunSelection:
    """Turn a selector into an explicit run list, naming what does not exist."""
    given = _validate_run_selector(runs, first, last)

    ordered = sorted(set(all_runs))
    if not given:
        return RunSelection(ordered, [], ordered)
    if first is not None:
        return RunSelection(ordered[:first], [], ordered)
    if last is not None:
        return RunSelection(ordered[-last:], [], ordered)
    if isinstance(runs, str):
        low, high = _parse_run_range(runs)
        return RunSelection([r for r in ordered if low <= r <= high], [], ordered)

    wanted = sorted({int(r) for r in runs})
    have = set(ordered)
    return RunSelection([r for r in wanted if r in have],
                        [r for r in wanted if r not in have],
                        ordered)


EXACT = "exact"
UPPER_BOUND = "upper_bound"
UNPROVEN = "unproven"


def _run_exactness(selection: RunSelection, files_summed: int,
                   files_actual: int | None,
                   scan_complete: bool) -> dict[str, Any]:
    """Can these per-run numbers be added up, or are they whole-file bounds?

    DBS counts a file against every run it appears in, so the per-run file
    total is compared against a range call to detect that sharing. A subset
    never proves exactness: a file may still span the edge of the range.
    """
    out: dict[str, Any] = {"files_summed": files_summed,
                           "files_actual": files_actual}
    if not scan_complete:
        # A cut-short scan leaves rows out, so files_summed is too small and
        # may match by luck. Luck is not proof.
        out["verdict"] = UNPROVEN
        out["reason"] = ("the run scan did not finish, so the per-run file total "
                         "is incomplete and proves nothing")
        return out
    if files_actual is None:
        out["verdict"] = UNPROVEN
        out["reason"] = "no range total was available to compare against"
        return out
    if files_summed > files_actual:
        shared = files_summed - files_actual
        out["verdict"] = UPPER_BOUND
        out["reason"] = (
            f"{shared} file(s) are counted under more than one run, so each "
            "per-run number is a whole-file upper bound and the sum double-counts")
        return out
    if files_summed == files_actual and selection.is_all_runs:
        out["verdict"] = EXACT
        out["reason"] = ("every run was requested and no file is shared between "
                         "runs, so the per-run numbers are exact")
        return out
    if files_summed == files_actual and selection.is_dense_range:
        out["verdict"] = UNPROVEN
        out["reason"] = ("no file is shared inside the requested runs, but a file "
                         "may still span the edge of the range; treat these as "
                         "upper bounds")
        return out
    out["verdict"] = UNPROVEN
    out["reason"] = ("the requested runs are sparser than the range queried, so "
                     "the two file totals are not comparable")
    return out


# ---------------------------------------------------------------------------
# safety
# Bounding output and stamping answers with their provenance.
#
# The output cap and the envelope builders: everything that decides how much a
# caller is handed and what it is told about where the numbers came from.
#
# ---------------------------------------------------------------------------

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


def _run_summary_envelope(runs: list[dict[str, Any]], totals: dict[str, Any],
                          exactness: dict[str, Any], coverage: dict[str, Any],
                          dataset: str, validity: str, calls: int,
                          hint: str) -> dict[str, Any]:
    base = _dbs_instance().rstrip("/")
    return {
        "runs": runs,
        "totals": totals,
        "exactness": exactness,
        "coverage": coverage,
        "provenance": {
            "instance": _dbs_instance(),
            "dataset": dataset,
            "validity_basis": validity,
            "queried_utc": _utc_now(),
            "n_server_calls": calls,
        },
        "repro": [
            f"dasgoclient --query 'summary dataset={dataset} run=RUN'",
            f"curl -s '{base}/filesummaries?dataset={dataset}&run_num=RUN' "
            "--cert $X509_USER_PROXY --key $X509_USER_PROXY",
        ],
        "hint": hint,
    }


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


# ---------------------------------------------------------------------------
# patterns
# Dataset-pattern shaping: splitting a scan, widening a dead end.
#
# `import time` and `time.monotonic()` are deliberate: the tests replace
# monotonic on the shared time module object, which only works when the call
# goes through the module rather than a name bound at import.
#
# ---------------------------------------------------------------------------

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

    A segment is widened ONLY if another stays specific, because that other
    one is the anchor DBS searches on; without it the probe asks for the whole
    catalog and costs more than the query it was meant to rescue.
    """
    parts = pattern.strip("/").split("/")
    if len(parts) != 3:
        return []
    specific = [i for i, part in enumerate(parts) if part and "*" not in part]
    if len(specific) < 2:
        return []
    out = []
    for i in specific:
        widened = list(parts)
        widened[i] = parts[i] + "*"
        out.append("/" + "/".join(widened))
    return out


def _tier_chunk_patterns(pattern: str, names: list[str]) -> list[str]:
    """Split a broad block scan into one pattern per data tier, because one
    whole-era listBlocks is more than DBS will reliably serve. A pattern that
    already names a tier is left alone."""
    parts = pattern.strip("/").split("/")
    if len(parts) != 3 or "*" not in parts[2]:
        return []
    tiers = sorted({n.rsplit("/", 1)[-1] for n in names if n.count("/") == 3})
    if len(tiers) < 2:
        return []
    return ["/" + "/".join([parts[0], parts[1], tier]) for tier in tiers]


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


def _probe_budget_s() -> float:
    try:
        return float(os.environ.get("DBS_PROBE_BUDGET_S", "15"))
    except ValueError:
        return 15.0


def _probe_wider_patterns(client: Any, pattern: str,
                          status: str) -> tuple[list[dict[str, Any]], int, bool]:
    """Which wider patterns actually hold data? -> (suggestions, calls, timed_out)

    A zero result is the one answer a model cannot check, so it invents a
    reason; a pattern that DOES match turns the dead end into a next step.
    Bounded by one probe per specific segment and a wall-clock budget.
    """
    budget = _probe_budget_s()
    if budget <= 0:
        return [], 0, False

    found: list[dict[str, Any]] = []
    calls = 0
    started = time.monotonic()
    for candidate in _relaxed_patterns(pattern):
        if calls and time.monotonic() - started >= budget:
            return found, calls, True
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
    return found, calls, False


# ---------------------------------------------------------------------------
# scan
# Budgeted concurrent scans.
#
# Both scanners answer inside a wall-clock budget and name what they did not
# reach, rather than letting a partial result look complete.
#
# `import time` and `time.monotonic()` are deliberate; see the patterns section.
#
# ---------------------------------------------------------------------------

def scan_with_budget(items: list[Any], one: Any, record: Any, *,
                     budget: float, workers: int, result: Any) -> Any:
    """Run `one` over `items` until the budget is spent, naming what was left.

    Everything not reached lands on result.unscanned, so a partial answer can
    never read as a whole one. `record(item, value)` is the only thing the two
    scanners do differently. `result` is the caller's own, returned as-is.
    """
    started = time.monotonic()

    if workers == 1:
        for index, item in enumerate(items):
            if index and budget > 0 and time.monotonic() - started >= budget:
                result.unscanned.extend(items[index:])
                break
            result.calls += 1
            try:
                value = one(item)
            except Exception as exc:
                result.failed.append(f"{item}: {exc}")
            else:
                record(item, value)
        return result

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Deliberately not a `with` block. Exiting the context manager joins every
    # worker, and a thread already inside a slow DBS call cannot be cancelled —
    # so `with` would wait out the very stall the budget exists to escape.
    # shutdown(wait=False) lets this return on time; the stragglers finish into
    # results nobody reads and then exit.
    pool = ThreadPoolExecutor(max_workers=min(workers, len(items)))
    futures = {pool.submit(one, item): item for item in items}
    try:
        for future in as_completed(futures, timeout=budget if budget > 0 else None):
            result.calls += 1
            try:
                value = future.result()
            except Exception as exc:
                result.failed.append(f"{futures[future]}: {exc}")
            else:
                record(futures[future], value)
    except Exception:  # TimeoutError from as_completed: the budget ran out
        pass
    for future, item in futures.items():
        if not future.done():
            result.unscanned.append(item)
    pool.shutdown(wait=False, cancel_futures=True)
    return result


class ScanResult:
    """What a block scan managed to collect, and what it did not."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.calls = 0
        self.failed: list[str] = []      # chunk patterns DBS refused
        self.unscanned: list[str] = []   # chunk patterns the budget cut off

    @property
    def complete(self) -> bool:
        return not self.failed and not self.unscanned


def _scan_blocks(client: Any, pattern: str, names: list[str], *,
                 fresh_client: Any) -> ScanResult:
    """Block records for the pattern, inside a wall-clock budget: split per
    tier, run concurrently, and whatever is missing is named, never zero."""
    result = ScanResult()
    chunk_min = _env_int("DBS_SCAN_CHUNK_MIN", 200)
    chunks = _tier_chunk_patterns(pattern, names) if (
        chunk_min > 0 and len(names) >= chunk_min) else []
    # Even one un-splittable scan runs through the pool, so the budget applies
    # to it too. A single large tier can still be the whole cost on its own:
    # /*/HIRun2026A*/ALCARECO is 133,231 blocks, 72.8MB of rows.
    if not chunks:
        chunks = [pattern]

    # How long the block scan may spend before it gives up and reports the
    # tiers it never reached. This is a coverage budget, not a timeout guard:
    # a tier that runs past it is reported scanned:false rather than summed
    # as a smaller number.
    #
    # Measured 2026-08-12 with response compression on: the largest single
    # tier, ALCARECO's 133,231 blocks, takes 77-107s. The old 75.0 predates
    # compression, when that call never returned at all; kept now it would
    # mark the era's biggest tier unscanned on the median night, and would
    # look like a server fault rather than the budget it is.
    budget = _env_float("DBS_SCAN_BUDGET_S", 100.0)
    workers = max(1, _env_int("DBS_SCAN_WORKERS", 6))

    def one(chunk: str) -> list[dict[str, Any]]:
        worker_client = client if workers == 1 else fresh_client()
        return _rows(worker_client.listBlocks(dataset=chunk, detail=True))

    def record(chunk: str, rows: list[dict[str, Any]]) -> None:
        result.rows.extend(rows)

    return scan_with_budget(chunks, one, record,
                            budget=budget, workers=workers, result=result)


class RunScanResult:
    """What a per-run scan collected, and what it did not."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.calls = 0
        self.failed: list[str] = []      # "run: error"
        self.unscanned: list[int] = []   # runs the budget cut off

    @property
    def complete(self) -> bool:
        return not self.failed and not self.unscanned


def _scan_runs(client: Any, dataset: str, runs: list[int],
               valid_only: bool, *, fresh_client: Any) -> RunScanResult:
    """One listFileSummaries per run, inside a wall-clock budget. A range
    returns a total and never a breakdown, so per-run rows genuinely cost one
    call each. Runs not reached are named, never counted as zero."""
    # This used to duplicate _scan_blocks deliberately, under an owner ruling
    # not to merge them without a new decision. That decision was taken on
    # 2026-08-12: share the skeleton only if the shared core stays free of
    # flags and both callers stay thin. scan_with_budget above is that core,
    # and the two differ only in `record` — which is the real difference
    # between them, not an accident of duplication.
    result = RunScanResult()
    if not runs:
        return result

    budget = _env_float("DBS_RUN_SCAN_BUDGET_S", 60.0)
    workers = max(1, _env_int("DBS_RUN_SCAN_WORKERS", 6))

    def one(run: int) -> dict[str, Any]:
        worker_client = client if workers == 1 else fresh_client()
        kwargs: dict[str, Any] = {"dataset": dataset, "run_num": run}
        # validFileOnly is presence-checked: sending 0 behaves like 1, so the
        # all-files call must omit the key entirely.
        if valid_only:
            kwargs["validFileOnly"] = 1
        return _first_row(worker_client.listFileSummaries(**kwargs))

    def record(run: int, row: dict[str, Any]) -> None:
        """An empty row is a FAILED run, never a zero one: DBS answers 200 + []
        for a run it cannot summarise, and a stored zero is summable."""
        if row:
            result.rows[run] = row
        else:
            result.failed.append(f"{run}: empty summary row")

    return scan_with_budget(runs, one, record,
                            budget=budget, workers=workers, result=result)
