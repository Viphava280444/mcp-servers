"""dbs_aggregate: totals and group rows computed server-side (spec section 2).

This is the production-crash fix: the answer's size follows the number of
GROUPS, never the number of datasets.
"""

from __future__ import annotations

import json

import pytest

from conftest import load_fixture

W24 = load_fixture("winter24_multi_status.json")
ERA = load_fixture("all_valid_era.json")


def _wire_campaign(stub, fixture):
    """Name resolution per status, plus the status-blind blocks route."""
    all_rows = fixture["datasets_all_status"]
    valid_names = set(fixture["datasets_valid_names"])
    stub.add("listDatasets", all_rows, match={"dataset_access_type": "*"})
    stub.add("listDatasets",
             [r for r in all_rows if r["dataset"] in valid_names],
             match={"dataset_access_type": "VALID"})
    stub.add("listDatasets",
             [r for r in all_rows if r["dataset_access_type"] == "INVALID"],
             match={"dataset_access_type": "INVALID"})
    stub.add("listBlocks", fixture["blocks_detail_all"])


# ---- the status intersect (the 9x trap) -----------------------------------

def test_valid_total_excludes_blocks_of_non_valid_datasets(patched_server, stub):
    _wire_campaign(stub, W24)
    out = patched_server.dbs_aggregate(pattern=W24["pattern"], status="VALID")
    totals = out["totals"]
    assert totals["bytes"] == W24["expected_valid_bytes"]
    assert totals["files"] == W24["expected_valid_files"]
    assert totals["bytes"] != W24["expected_unfiltered_bytes"], "blocks API ignores status"
    assert out["coverage"]["n_matched"] == 3


def test_intersect_follows_the_requested_status_not_a_hardcoded_valid(patched_server, stub):
    _wire_campaign(stub, W24)
    out = patched_server.dbs_aggregate(pattern=W24["pattern"], status="INVALID")
    assert out["coverage"]["n_matched"] == 2
    assert out["totals"]["bytes"] == 45000  # SampleD 40000 + SampleE 5000
    assert out["provenance"]["status_filter"] == "INVALID"


def test_intersect_still_runs_when_it_looks_like_a_no_op(patched_server, stub):
    """The all-VALID era: the intersect changes nothing and must not break."""
    _wire_campaign(stub, ERA)
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID")
    assert out["totals"]["bytes"] == ERA["expected_bytes"]
    assert out["totals"]["files"] == ERA["expected_files"]
    assert out["coverage"]["n_matched"] == 3


def test_status_star_sums_everything(patched_server, stub):
    _wire_campaign(stub, W24)
    out = patched_server.dbs_aggregate(pattern=W24["pattern"], status="*")
    assert out["totals"]["bytes"] == W24["expected_unfiltered_bytes"]
    assert out["coverage"]["n_matched"] == 6


# ---- counting without listing ---------------------------------------------

def test_count_only_answers_without_touching_blocks(patched_server, stub):
    _wire_campaign(stub, W24)
    out = patched_server.dbs_aggregate(pattern=W24["pattern"], status="*", count_only=True)
    assert out["totals"]["n_datasets"] == 6
    assert stub.calls_for("listBlocks") == [], "counting must not pay for sizes"
    assert "bytes" not in out["totals"]


def test_count_only_groups_by_status_from_the_resolved_rows(patched_server, stub):
    _wire_campaign(stub, W24)
    out = patched_server.dbs_aggregate(pattern=W24["pattern"], status="*",
                                       group_by="status", count_only=True)
    groups = {row["group"]: row["n_datasets"] for row in out["groups"]}
    assert groups == {"VALID": 3, "INVALID": 2, "PRODUCTION": 1}


def test_grouping_by_tier_comes_from_names_alone(patched_server, stub):
    rows = [
        {"dataset": "/A/Era-v1/RAW", "dataset_access_type": "VALID"},
        {"dataset": "/B/Era-v1/RAW", "dataset_access_type": "VALID"},
        {"dataset": "/C/Era-v1/AOD", "dataset_access_type": "VALID"},
    ]
    stub.add("listDatasets", rows)
    out = patched_server.dbs_aggregate(pattern="/*/Era*/*", status="VALID",
                                       group_by="tier", count_only=True)
    groups = {row["group"]: row["n_datasets"] for row in out["groups"]}
    assert groups == {"RAW": 2, "AOD": 1}


# ---- events: exact or honestly absent -------------------------------------

def test_events_summed_below_the_cap(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_MAX_DATASETS_SUMMED", "10")
    _wire_campaign(stub, ERA)
    stub.add("listFileSummaries", [{"num_event": 100, "num_lumi": 5}])
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                       metrics=["count", "bytes", "events"])
    assert out["totals"]["events"] == 300
    assert len(stub.calls_for("listFileSummaries")) == 3


def test_events_null_with_reason_above_the_cap(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_MAX_DATASETS_SUMMED", "2")
    _wire_campaign(stub, ERA)
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                       metrics=["count", "bytes", "events"])
    assert out["totals"]["events"] is None
    assert "3 datasets" in out["totals"]["events_null_reason"]
    assert stub.calls_for("listFileSummaries") == [], "no partial sum is attempted"
    assert out["totals"]["bytes"] == ERA["expected_bytes"], "bytes still answered"


def test_event_summing_never_sends_validfileonly_zero(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_MAX_DATASETS_SUMMED", "10")
    _wire_campaign(stub, ERA)
    stub.add("listFileSummaries", [{"num_event": 100, "num_lumi": 5}])
    patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID",
                                 metrics=["count", "events"])
    for call in stub.calls_for("listFileSummaries"):
        assert call.get("validFileOnly") != 0


def test_cross_tier_event_warning(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_MAX_DATASETS_SUMMED", "10")
    stub.add("listDatasets", [
        {"dataset": "/A/Era-v1/RAW", "dataset_access_type": "VALID"},
        {"dataset": "/A/Era-v1/AOD", "dataset_access_type": "VALID"},
    ])
    stub.add("listBlocks", [])
    stub.add("listFileSummaries", [{"num_event": 10, "num_lumi": 1}])
    out = patched_server.dbs_aggregate(pattern="/A/Era*/*", status="VALID",
                                       group_by="tier", metrics=["count", "events"])
    assert "double-count" in out["hint"]


# ---- refusals and bounds --------------------------------------------------

def test_no_era_name_parameter_exists(patched_server, stub):
    with pytest.raises(TypeError):
        patched_server.dbs_aggregate(pattern="/*/X*/RAW", acquisition_era_name="HIRun2026A")


def test_never_sends_acquisition_era_name(patched_server, stub):
    _wire_campaign(stub, ERA)
    patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID")
    for _, kwargs in stub.calls:
        assert "acquisition_era_name" not in kwargs


def test_empty_match_is_stated_not_silent(patched_server, stub):
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/*/NoSuchEra*/RAW", status="VALID")
    assert out["coverage"]["n_matched"] == 0
    assert out["totals"]["n_datasets"] == 0
    assert "no datasets" in out["hint"].lower()


def test_output_is_bounded_by_groups_not_datasets(patched_server, stub):
    many = [{"dataset": f"/P{i:05d}/Era-v1/RAW", "dataset_access_type": "VALID"}
            for i in range(4000)]
    stub.add("listDatasets", many)
    out = patched_server.dbs_aggregate(pattern="/*/Era*/RAW", status="VALID",
                                       group_by="tier", count_only=True)
    assert out["totals"]["n_datasets"] == 4000
    assert len(out["groups"]) == 1
    assert len(json.dumps(out)) < 32768


def test_envelope_carries_provenance_and_repro(patched_server, stub):
    _wire_campaign(stub, ERA)
    out = patched_server.dbs_aggregate(pattern=ERA["pattern"], status="VALID")
    prov = out["provenance"]
    assert prov["status_filter"] == "VALID"
    assert prov["queried_utc"].endswith("Z")
    assert prov["n_server_calls"] >= 2
    assert any("dasgoclient" in line for line in out["repro"])
    assert out["coverage"]["complete"] is True
