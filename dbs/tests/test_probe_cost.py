"""A recovery probe must never cost more than the query it is helping.

Measured 2026-08-03: /*/*/NANOAO (a typo) matched nothing, so the probe
widened the tier to /*/*/NANOAO* — a catalog-wide scan by tier. It ran
**277 seconds** and still returned no suggestion. archi kills any tool call
at 120 s, so a feature meant to rescue an empty answer instead killed two
questions that used to work.

Two bounds, both general:

- Only widen a segment when at least one OTHER segment is specific. That
  leaves DBS an anchor to search on. /HIForward/Era-v1/AOD has three
  anchors, so probing is cheap. /*/*/TIER has none, and widening the one
  specific part asks for the whole catalog.
- Spend at most a wall-clock budget on probing, checked between probes, so
  a slow server cannot turn help into a timeout.
"""

from __future__ import annotations

import pytest

THIRTY = [{"dataset": f"/HIForward{i}/Era-v1/AOD", "dataset_access_type": "VALID"}
          for i in range(30)]


# ---- an anchor is required ------------------------------------------------

def test_a_pattern_with_no_other_anchor_is_not_probed(patched_server, stub):
    """/*/*/NANOAO: widening the tier asks for every dataset in the catalog."""
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/*/*/NANOAO", status="VALID")
    assert out["did_you_mean"] == []
    assert len(stub.calls_for("listDatasets")) == 1, "no probe may be sent"


def test_one_anchor_is_enough_to_probe(patched_server, stub):
    stub.add("listDatasets", [], match={"dataset": "/*/Era-v1/AOD"})
    stub.add("listDatasets", [], match={"dataset": "/*/Era-v1*/AOD"})
    stub.add("listDatasets", THIRTY, match={"dataset": "/*/Era-v1/AOD*"})
    out = patched_server.dbs_aggregate(pattern="/*/Era-v1/AOD", status="VALID")
    assert [s["pattern"] for s in out["did_you_mean"]] == ["/*/Era-v1/AOD*"]


def test_the_fully_specific_case_still_probes(patched_server, stub):
    """The HIForward rescue this feature exists for must keep working."""
    stub.add("listDatasets", [], match={"dataset": "/HIForward/Era-v1/AOD"})
    stub.add("listDatasets", THIRTY, match={"dataset": "/HIForward*/Era-v1/AOD"})
    stub.add("listDatasets", [], match={"dataset": "/HIForward/Era-v1*/AOD"})
    stub.add("listDatasets", [], match={"dataset": "/HIForward/Era-v1/AOD*"})
    out = patched_server.dbs_aggregate(pattern="/HIForward/Era-v1/AOD", status="VALID")
    assert [s["pattern"] for s in out["did_you_mean"]] == ["/HIForward*/Era-v1/AOD"]


def test_an_all_wildcard_pattern_is_never_probed(patched_server, stub):
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/*/*/*", status="VALID")
    assert out["did_you_mean"] == []
    assert len(stub.calls_for("listDatasets")) == 1


# ---- a wall-clock budget --------------------------------------------------

def test_probing_can_be_switched_off(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_PROBE_BUDGET_S", "0")
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/HIForward/Era-v1/AOD", status="VALID")
    assert out["did_you_mean"] == []
    assert len(stub.calls_for("listDatasets")) == 1


def test_probing_stops_once_the_budget_is_spent(patched_server, stub, monkeypatch):
    """A slow server must cost one probe, not three."""
    monkeypatch.setenv("DBS_PROBE_BUDGET_S", "10")
    ticks = iter([0.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(patched_server.time, "monotonic", lambda: next(ticks))
    stub.add("listDatasets", [])
    patched_server.dbs_aggregate(pattern="/HIForward/Era-v1/AOD", status="VALID")
    probes = [c for c in stub.calls_for("listDatasets")
              if c["dataset"] != "/HIForward/Era-v1/AOD"]
    assert len(probes) == 1, "the budget must stop the remaining probes"


def test_the_budget_is_reported_when_it_cuts_probing_short(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_PROBE_BUDGET_S", "10")
    ticks = iter([0.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(patched_server.time, "monotonic", lambda: next(ticks))
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/HIForward/Era-v1/AOD", status="VALID")
    assert "budget" in out["hint"].lower() or "gave up" in out["hint"].lower()


def test_probe_calls_are_still_counted(patched_server, stub):
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/HIForward/Era-v1/AOD", status="VALID")
    assert out["provenance"]["n_server_calls"] == len(stub.calls_for("listDatasets"))
