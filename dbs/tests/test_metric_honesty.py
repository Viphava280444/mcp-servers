"""Never silently drop a metric, and never withhold one already paid for.

Two failures measured in the 2026-08-03 exam:

- AGG-05: the model asked for metrics ["bytes", "datasets"]. There is no
  metric called "datasets", so it was dropped without a word — the same
  accepted-and-ignored behavior this whole server exists to stop DBS doing.
- AGG-02: the model asked for ["bytes"] and got only bytes, so it reported
  tier sizes with no dataset counts and was marked incomplete. But the file,
  block and dataset counts came out of the SAME single block scan. They were
  already bought; withholding them buys nothing.
"""

from __future__ import annotations

import pytest

from conftest import load_fixture

ERA = load_fixture("all_valid_era.json")


def _wire(stub, fixture=ERA):
    all_rows = fixture["datasets_all_status"]
    valid = set(fixture["datasets_valid_names"])
    stub.add("listDatasets", all_rows, match={"dataset_access_type": "*"})
    stub.add("listDatasets", [r for r in all_rows if r["dataset"] in valid],
             match={"dataset_access_type": "VALID"})
    stub.add("listBlocks", fixture["blocks_detail_all"])


# ---- the scan is paid for once; report all of it ---------------------------

def test_asking_only_for_bytes_still_returns_the_free_counts(patched_server, stub):
    _wire(stub)
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                       metrics=["bytes"])
    totals = out["totals"]
    assert totals["bytes"] == ERA["expected_bytes"]
    assert totals["files"] == ERA["expected_files"], "files came from the same scan"
    assert "blocks" in totals
    assert totals["n_datasets"] == 3


def test_groups_also_carry_the_free_counts(patched_server, stub):
    _wire(stub)
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                       metrics=["bytes"], group_by="tier")
    for row in out["groups"]:
        assert "n_datasets" in row
        assert "files" in row


def test_events_are_not_free_and_stay_opt_in(patched_server, stub):
    _wire(stub)
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                       metrics=["bytes"])
    assert "events" not in out["totals"], "events cost one call per dataset"
    assert stub.calls_for("listFileSummaries") == []


def test_events_still_arrive_when_asked_for(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_MAX_DATASETS_SUMMED", "10")
    _wire(stub)
    stub.add("listFileSummaries", [{"num_event": 100, "num_lumi": 5}])
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                       metrics=["events"])
    assert out["totals"]["events"] == 300


# ---- an unknown metric is an error, not a shrug ---------------------------

def test_unknown_metric_is_rejected(patched_server, stub):
    _wire(stub)
    with pytest.raises(ValueError) as exc:
        patched_server.dbs_aggregate(pattern=ERA["pattern"], metrics=["bytes", "datasets"])
    message = str(exc.value)
    assert "datasets" in message, "name the metric that was wrong"
    assert "events" in message, "list what is actually valid"


def test_rejection_happens_before_any_server_call(patched_server, stub):
    _wire(stub)
    with pytest.raises(ValueError):
        patched_server.dbs_aggregate(pattern=ERA["pattern"], metrics=["nonsense"])
    assert stub.calls == [], "do not spend a DBS call on a request we will not honor"


def test_every_documented_metric_is_accepted(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_MAX_DATASETS_SUMMED", "10")
    _wire(stub)
    stub.add("listFileSummaries", [{"num_event": 100, "num_lumi": 5}])
    out = patched_server.dbs_aggregate(
        pattern=ERA["pattern"], status="VALID",
        metrics=["count", "bytes", "files", "blocks", "events"])
    for key in ("n_datasets", "bytes", "files", "blocks", "events"):
        assert key in out["totals"]


def test_empty_metric_list_behaves_like_the_default(patched_server, stub):
    _wire(stub)
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                       metrics=[])
    assert out["totals"]["bytes"] == ERA["expected_bytes"]
