# DBS MCP Server

This is a Model Context Protocol server for the CMS Data Bookkeeping Service
(DBS), built on top of the official Python DBS client from
https://github.com/dmwm/DBSClient.

It exposes a focused set of common DBS read tools, plus a generic method caller
that can reach any public method on `dbs.apis.dbsClient.DbsApi`.

## Tools

- `dbs_server_info`: return DBS server metadata.
- `dbs_list_methods`: list public DBS client methods available through the MCP.
- `dbs_method_help`: return the local Python docstring for a DBS client method.
- `dbs_call`: call any public DBS client method by name.
- `dbs_list_datasets`: convenience wrapper for `listDatasets`.
- `dbs_list_files`: convenience wrapper for `listFiles`.
- `dbs_list_blocks`: convenience wrapper for `listBlocks`.
- `dbs_list_runs`: convenience wrapper for `listRuns`.
- `dbs_block_dump`: convenience wrapper for `blockDump`.

On top of these, four task tools answer common questions in one call. They
handle the DBS traps (status defaults, per-run counting, invalid files) and
return small, complete answers, so prefer them when one fits:

- `dbs_summary`: one dataset or block — status, size, events, files, runs.
- `dbs_aggregate`: totals for an era or campaign, grouped by tier, status,
  or version.
- `dbs_run_summary`: events, files and bytes per run.
- `dbs_run_coverage`: which runs one dataset has that another is missing.

Two behaviors to know:

- Every reply is capped (256 KB by default). A cut list says so:
  `truncated: showing 623 of 7759 records`.
- The server always asks DBS for gzip responses. Without this, big listings
  cannot finish before DBS closes the stream at ~302 seconds.

`skills/dbs.md` holds four short answer rules for the model. Each rule is
there because removing it made benchmark answers measurably worse.

## Configuration

The server reads configuration from environment variables:

- `DBS_URL`: DBS service URL. Defaults to
  `https://cmsweb.cern.ch/dbs/prod/global/DBSReader/`.
- `DBS_PROXY`: optional SOCKS5 proxy URL.
- `X509_USER_PROXY`: optional path to a grid proxy, passed to DBS as both
  `key` and `cert`.
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

New in this version:

- `MCP_HOST`, `MCP_PORT`: server bind address and port. Default `0.0.0.0`
  and `8013`.
- `DBS_RESULT_CAP_BYTES`: reply size cap. Defaults to `262144`.
- `DBS_CURL_TIMEOUT_S`: hang guard per HTTP call. Defaults to `240`.
- `DBS_SCAN_BUDGET_S`: time budget for multi-call scans. Defaults to `100`.
  When it runs out, the reply says `scanned: false` instead of guessing.

More scan knobs (workers, chunk sizes, per-tool budgets) are read in
`src/utils.py`.

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

The server uses streamable HTTP transport and listens on port `8013` by
default. The Dockerfile builds the same thing as a container.

## Example MCP Client Configuration

```json
{
  "mcpServers": {
    "dbs": {
      "url": "http://localhost:8013/mcp"
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

Insert or update calls can be made through `dbs_call` too, but only use them
against the intended DBS writer/migration service URL and with proper X.509
credentials.
