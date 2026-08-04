"""Shared test setup for the DBS MCP server.

Design goals:
- Tests run OFFLINE by default on any machine: if the real `mcp` or
  `dbs3-client` packages are missing, minimal stand-ins are installed in
  sys.modules before `src.server` is imported.
- Every DBS interaction goes through StubDbsApi, which records calls and
  serves canned responses, so tests can assert on the exact kwargs sent
  (the server traps live in kwargs: validFileOnly presence, silent
  defaults, ignored filters).
- Live tests are opt-in via the `integration` marker.
"""

from __future__ import annotations

import json
import sys
import types
from functools import partial
from pathlib import Path

import pytest

DBS_DIR = Path(__file__).resolve().parent.parent
if str(DBS_DIR) not in sys.path:
    sys.path.insert(0, str(DBS_DIR))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --------------------------------------------------------------------------
# Import-time stand-ins so `import src.server` works without the real deps.
# --------------------------------------------------------------------------

class _StubFastMCP:
    """Just enough FastMCP: @mcp.tool() registers, run() refuses."""

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.registered_tools: dict[str, object] = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.registered_tools[fn.__name__] = fn
            return fn
        return decorator

    def run(self, *args, **kwargs):
        raise RuntimeError("stub FastMCP cannot serve; use the real package")


def _ensure_module(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


def _install_import_stubs() -> None:
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        mcp_mod = _ensure_module("mcp")
        server_mod = _ensure_module("mcp.server")
        fast_mod = _ensure_module("mcp.server.fastmcp")
        fast_mod.FastMCP = _StubFastMCP
        server_mod.fastmcp = fast_mod
        mcp_mod.server = server_mod

    try:
        from dbs.apis.dbsClient import DbsApi  # noqa: F401
    except ImportError:
        dbs_mod = _ensure_module("dbs")
        apis_mod = _ensure_module("dbs.apis")
        client_mod = _ensure_module("dbs.apis.dbsClient")

        class DbsApi:  # minimal constructor-compatible stand-in
            def __init__(self, **kwargs):
                self.constructor_kwargs = kwargs

        client_mod.DbsApi = DbsApi
        apis_mod.dbsClient = client_mod
        dbs_mod.apis = apis_mod


_install_import_stubs()

import src.server as server  # noqa: E402  (needs the stubs above)


# --------------------------------------------------------------------------
# The recording test double for DbsApi.
# --------------------------------------------------------------------------

# Read methods the stub exposes as real attributes (visible to dir()).
READ_METHODS = [
    "serverinfo",
    "listDatasets",
    "listFileSummaries",
    "listBlockSummaries",
    "listBlocks",
    "listFiles",
    "listRuns",
    "listFileLumis",
    "listDatasetParents",
    "listDatasetChildren",
    "listAcquisitionEras",
    "listDataTiers",
    "listPhysicsGroups",
    "listDatasetAccessTypes",
    "listOutputConfigs",
    "blockDump",
    "help",
]

# Write-capable methods a real DbsApi also exposes. The stub carries them so
# tests can prove what the server's method discovery lets through.
WRITE_METHODS = [
    "insertFiles",
    "insertDataset",
    "insertBulkBlock",
    "updateFileStatus",
    "updateDatasetType",
    "submitMigration",
    "removeMigration",
]


class StubDbsApi:
    """Records every call; answers from a response table.

    responses maps method name to one of:
    - a plain value: always returned;
    - a list of (match_kwargs, value) pairs: first pair whose match_kwargs
      is a subset of the call kwargs wins;
    - a callable(kwargs) -> value.
    Unregistered calls raise AssertionError so tests notice every request.
    """

    def __init__(self, responses: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = dict(responses or {})
        for method in READ_METHODS + WRITE_METHODS:
            setattr(self, method, partial(self._respond, method))

    def add(self, method: str, value, match: dict | None = None) -> None:
        if match is None:
            self.responses[method] = value
        else:
            table = self.responses.setdefault(method, [])
            if not isinstance(table, list):
                raise TypeError(f"responses[{method!r}] already holds a plain value")
            table.append((match, value))

    def calls_for(self, method: str) -> list[dict]:
        return [kw for m, kw in self.calls if m == method]

    def _respond(self, method: str, **kwargs):
        self.calls.append((method, dict(kwargs)))
        if method not in self.responses:
            raise AssertionError(f"unexpected DBS call: {method}({kwargs})")
        entry = self.responses[method]
        if callable(entry):
            return entry(kwargs)
        if isinstance(entry, list) and entry and isinstance(entry[0], tuple):
            for match, value in entry:
                if all(kwargs.get(k) == v for k, v in match.items()):
                    return value
            raise AssertionError(f"no fixture matches {method}({kwargs})")
        return entry


# --------------------------------------------------------------------------
# Fixtures.
# --------------------------------------------------------------------------

def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def stub() -> StubDbsApi:
    return StubDbsApi()


@pytest.fixture
def patched_server(monkeypatch, stub):
    """server module with its cached client replaced by the stub."""
    server._dbs_client.cache_clear()
    monkeypatch.setattr(server, "_dbs_client", lambda: stub)
    yield server
