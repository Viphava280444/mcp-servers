# DBS MCP Server

This is a Model Context Protocol server for the CMS Data Bookkeeping Service
(DBS), built on top of the official Python DBS client from
https://github.com/dmwm/DBSClient.

It exposes two task tools that answer a whole question in one call, the
original set of thin DBS read wrappers, and a generic caller limited to read
methods on `dbs.apis.dbsClient.DbsApi`.

## Tools

Task tools (prefer these):

- `dbs_summary`: everything about ONE dataset or block — status, size, events,
  files on both validity sides, lumis, blocks and open blocks, activity dates,
  run range, origin sites — with provenance and reproduction commands. Fixed
  small output.
- `dbs_aggregate`: totals and per-group sums over MANY datasets matching a
  pattern (group by tier, stream, version or status; `count_only` for a pure
  count). Output size follows the number of groups, not the number of
  datasets, so a whole-era question stays small. Dataset, byte, file and block
  counts always come back together — one block scan pays for all four — while
  `events` is opt-in because it costs one call per dataset. An unknown metric
  name is rejected, never ignored. When the pattern matches nothing, the reply
  carries `did_you_mean`: wider patterns that do hold data.

Thin wrappers (use when you want raw rows):

- `dbs_server_info`: DBS server version and the configured instance URL.
- `dbs_list_methods`: read methods reachable through this server.
- `dbs_method_help`: local Python docstring for a DBS client method.
- `dbs_call`: call a DBS **read** method by name (writes are rejected).
- `dbs_list_datasets`: wrapper for `listDatasets`.
- `dbs_list_files`: wrapper for `listFiles`.
- `dbs_list_blocks`: wrapper for `listBlocks`.
- `dbs_list_runs`: wrapper for `listRuns`.
- `dbs_block_dump`: wrapper for `blockDump`.

Nothing was removed or renamed: every tool that existed before still exists
with the same signature. New in this version: the two task tools, an enforced
read-only method allow-list, a shared output cap, and honest empty results.

Why the task tools exist: DBS has traps that a thin wrapper cannot hide. The
dataset status filter defaults to VALID silently; `validFileOnly` is
presence-checked so sending 0 behaves like 1; the blocks endpoint ignores the
status filter entirely; the acquisition-era filter is ignored and answers with
the whole catalog. `dbs_summary` and `dbs_aggregate` handle all of these once,
in code. `skills/dbs.md` documents them for the model.

## Configuration

The server reads configuration from environment variables:

- `DBS_URL`: DBS service URL. Defaults to
  `https://cmsweb.cern.ch/dbs/prod/global/DBSReader/`.
- `DBS_PROXY`: optional SOCKS5 proxy URL.
- `X509_USER_CERT`: optional path to a user certificate.
- `X509_USER_KEY`: optional path to a private key.
- `X509_CERT_DIR`: optional CA certificate directory, passed to DBS as
  `ca_info`.
- `DBS_VERIFY_PEER`: set to `0`, `false`, or `no` to disable peer
  verification.
- `DBS_USER_AGENT`: optional suffix for the DBS client user agent.
- `DBS_PORT`: port added by the DBS client when the URL omits one. Defaults to
  `8443`.
- `DBS_ACCEPT`: response accept header. Defaults to `application/json`.
- `DBS_AGGREGATE`: set to `0`, `false`, or `no` to disable DBS client
  aggregation helpers.
- `DBS_USE_GZIP`: set to `1`, `true`, or `yes` to gzip POST bodies.
- `DBS_DEBUG`: set to `1`, `true`, or `yes` to print DBS HTTP debug output.
- `DBS_RESULT_CAP_BYTES`: maximum serialized size of any tool result. Oversized
  lists are cut at a record boundary with a `truncated: showing X of N records`
  note. Defaults to `262144`.
- `DBS_PROBE_BUDGET_S`: wall-clock seconds `dbs_aggregate` may spend probing
  wider patterns after a pattern matches nothing. Set to `0` to switch probing
  off. Defaults to `15`. A probe is only sent when some other path segment is
  specific enough to anchor the search, because widening the only specific
  part of a pattern asks DBS for the whole catalog.
- `DBS_MAX_DATASETS_SUMMED`: how many datasets `dbs_aggregate` will sum events
  over (events cost one call per dataset). Above the cap events come back
  `null` with the reason. Defaults to `60`.

Read-only DBS endpoints often work with the default reader URL. Write/update
operations generally require valid CERN X.509 credentials.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

`dbs3-client` depends on libcurl through `dbs3-pycurl`. If installation fails
while building pycurl, install libcurl development headers for your platform and
retry.

## Run

```bash
DBS_URL=https://cmsweb.cern.ch/dbs/prod/global/DBSReader/ dbs-mcp
```

The server uses stdio transport, which is what desktop MCP clients expect.

## Example MCP Client Configuration

```json
{
  "mcpServers": {
    "dbs": {
      "command": "/absolute/path/to/this/repo/.venv/bin/dbs-mcp",
      "env": {
        "DBS_URL": "https://cmsweb.cern.ch/dbs/prod/global/DBSReader/"
      }
    }
  }
}
```

## Generic Call Examples

List datasets:

```json
{
  "method": "listDatasets",
  "kwargs": {
    "dataset": "/Primary/Processed/TIER",
    "detail": true
  }
}
```

`dbs_call` reaches read methods only. Write, insert, update, migration and
removal methods are rejected before any HTTP request: this server is a reader.

## Tests

```bash
python -m pip install -e ".[test]"
python -m pytest            # offline; live tests need -m integration
```

Tests run offline by default against a recording stub of the DBS client, so no
grid credentials are needed. Fixtures are modeled on live-measured DBS
behavior, including a VALID dataset that holds invalid files and a campaign
whose block totals are wrong without a status intersect.