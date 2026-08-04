"""dbs_summary: one subject, the full picture, always small (spec section 1)."""

from __future__ import annotations

import json

from conftest import load_fixture

PARK = load_fixture("parkingbph1_mixed_validity.json")
NONE = load_fixture("nonexistent_path.json")
BLOCK = load_fixture("block_subject.json")
W24 = load_fixture("winter24_multi_status.json")


def _wire_valid_dataset(stub, dataset=PARK["dataset"]):
    """A VALID dataset holding 26 invalid files, plus blocks and runs."""
    stub.add("listDatasets", [{"dataset": dataset, "dataset_access_type": "VALID",
                               "data_tier_name": "RAW", "last_modification_date": 1540000000,
                               "last_modified_by": "tier0"}])
    stub.add("listFileSummaries", PARK["filesummaries_valid_only"], match={"validFileOnly": 1})
    stub.add("listFileSummaries", PARK["filesummaries_all_files"], match={})
    stub.add("listBlocks", [
        {"dataset": dataset, "block_name": f"{dataset}#b1", "block_size": 500, "file_count": 5,
         "open_for_writing": 0, "origin_site_name": "T1_US_FNAL_Disk", "creation_date": 1540000000},
        {"dataset": dataset, "block_name": f"{dataset}#b2", "block_size": 700, "file_count": 7,
         "open_for_writing": 1, "origin_site_name": "T2_CH_CERN", "creation_date": 1541000000},
    ])
    stub.add("listRuns", [{"run_num": 320000}, {"run_num": 319000}, {"run_num": 321000}])


# ---- both validity sides, named unambiguously -----------------------------

def test_reports_both_validity_sides_with_named_fields(patched_server, stub):
    _wire_valid_dataset(stub)
    out = patched_server.dbs_summary(subject=PARK["dataset"])
    s = out["summary"]
    assert s["bytes_all_files"] == PARK["filesummaries_all_files"][0]["file_size"]
    assert s["bytes_valid_files"] == PARK["filesummaries_valid_only"][0]["file_size"]
    assert s["n_files_all"] == 111808 and s["n_files_valid"] == 111782
    assert s["n_files_invalid"] == PARK["delta_files"] == 26
    assert s["events_all_files"] - s["events_valid_files"] == PARK["delta_events"]


def test_all_files_call_omits_the_validfileonly_key_entirely(patched_server, stub):
    """Presence-checked server flag: sending 0 behaves like 1, so it must be absent."""
    _wire_valid_dataset(stub)
    patched_server.dbs_summary(subject=PARK["dataset"])
    calls = stub.calls_for("listFileSummaries")
    assert len(calls) == 2
    all_files = [c for c in calls if "validFileOnly" not in c]
    valid_only = [c for c in calls if c.get("validFileOnly") == 1]
    assert len(all_files) == 1, "the all-files call must not carry the key at all"
    assert len(valid_only) == 1
    assert all(c.get("validFileOnly") != 0 for c in calls)


def test_valid_side_skipped_for_a_non_valid_dataset(patched_server, stub):
    """validFileOnly=1 silently gates to VALID/PRODUCTION, so it must not be sent."""
    stub.add("listDatasets", [{"dataset": "/A/B-v1/RAW", "dataset_access_type": "INVALID",
                              "last_modification_date": 1745800000,
                              "last_modified_by": "cmsdm-transferops"}])
    stub.add("listFileSummaries", [{"file_size": 11570437923, "num_file": 5, "num_event": 900,
                                    "num_lumi": 12, "num_block": 1}], match={})
    stub.add("listBlocks", [{"dataset": "/A/B-v1/RAW", "block_name": "/A/B-v1/RAW#b1",
                             "block_size": 11570437923, "file_count": 5, "open_for_writing": 0,
                             "origin_site_name": "T2_DE_DESY", "creation_date": 1540000000}])
    stub.add("listRuns", [{"run_num": 1}])
    out = patched_server.dbs_summary(subject="/A/B-v1/RAW")
    s = out["summary"]
    assert s["status"] == "INVALID"
    assert s["bytes_all_files"] == 11570437923
    assert s["bytes_valid_files"] is None
    assert s["valid_side_null_reason"] == "gated_by_access_type"
    assert all("validFileOnly" not in c for c in stub.calls_for("listFileSummaries"))
    assert s["invalidated_on"] == "2025-04-28" and "transferops" in s["invalidated_by"]


# ---- existence resolution -------------------------------------------------

def test_existence_check_uses_star_status(patched_server, stub):
    _wire_valid_dataset(stub)
    patched_server.dbs_summary(subject=PARK["dataset"])
    first = stub.calls_for("listDatasets")[0]
    assert first["dataset_access_type"] == "*", "exists-but-filtered must not read as missing"


def test_missing_dataset_reports_not_found_with_suggestions(patched_server, stub):
    stub.add("listDatasets", NONE["datasets_star"], match={"dataset": NONE["dataset"]})
    stub.add("listDatasets", NONE["did_you_mean_probe"], match={})
    out = patched_server.dbs_summary(subject=NONE["dataset"])
    assert out["summary"]["found"] is False
    assert out["summary"]["did_you_mean"] == ["/DoesExist/RealEra-v1/RAW"]
    assert "summary" in out and "provenance" in out
    assert stub.calls_for("listFileSummaries") == [], "no number calls for a missing dataset"


# ---- blocks, activity dates, runs, sites ----------------------------------

def test_open_blocks_counted_client_side_never_filtered(patched_server, stub):
    _wire_valid_dataset(stub)
    out = patched_server.dbs_summary(subject=PARK["dataset"])
    s = out["summary"]
    assert s["n_blocks"] == 2 and s["n_open_blocks"] == 1
    assert all("open_for_writing" not in c for c in stub.calls_for("listBlocks")), \
        "the server ignores open_for_writing as a filter"


def test_activity_dates_answer_is_it_still_being_written(patched_server, stub):
    _wire_valid_dataset(stub)
    s = patched_server.dbs_summary(subject=PARK["dataset"])["summary"]
    assert s["oldest_block_created"] == "2018-10-20"
    assert s["newest_block_created"] == "2018-10-31"


def test_runs_range_and_basis_label(patched_server, stub):
    _wire_valid_dataset(stub)
    s = patched_server.dbs_summary(subject=PARK["dataset"])["summary"]
    assert (s["n_runs"], s["run_min"], s["run_max"]) == (3, 319000, 321000)
    assert s["runs_basis"] == "all_files"


def test_origin_sites_carry_the_rucio_caveat(patched_server, stub):
    _wire_valid_dataset(stub)
    out = patched_server.dbs_summary(subject=PARK["dataset"])
    assert out["summary"]["origin_sites"] == {"T1_US_FNAL_Disk": 1, "T2_CH_CERN": 1}
    assert "Rucio" in out["note"]


# ---- block subject --------------------------------------------------------

def test_block_subject_uses_blocksummaries_and_the_detail_row(patched_server, stub):
    stub.add("listBlockSummaries", BLOCK["blocksummaries"])
    stub.add("listBlocks", BLOCK["blocks_detail"])
    out = patched_server.dbs_summary(subject=BLOCK["block_name"])
    s = out["summary"]
    assert s["subject_kind"] == "block"
    assert s["bytes_all_files"] == 4190000000000
    assert s["events_all_files"] == 26000000 and s["n_files_all"] == 456
    assert s["n_open_blocks"] == 0
    assert s["dataset"] == "/ParkingBPH1/Run2018D-v1/RAW"
    assert stub.calls_for("listDatasets") == [], "a block subject needs no dataset lookup"


# ---- envelope -------------------------------------------------------------

def test_envelope_has_provenance_and_repro_and_stays_small(patched_server, stub):
    _wire_valid_dataset(stub)
    out = patched_server.dbs_summary(subject=PARK["dataset"])
    prov = out["provenance"]
    assert prov["instance"].endswith("DBSReader/") or prov["instance"].endswith("DBSReader")
    assert prov["status_filter"] == "*"
    assert prov["n_server_calls"] >= 4
    assert prov["queried_utc"].endswith("Z")
    assert "dasgoclient" in out["repro"][0] and "DBSReader" in out["repro"][1]
    assert len(json.dumps(out)) < 16384


def test_zero_row_with_zero_blocks_is_diagnosed_not_reported_as_data(patched_server, stub):
    stub.add("listDatasets", [{"dataset": "/A/B-v1/RAW", "dataset_access_type": "VALID"}])
    stub.add("listFileSummaries", NONE["filesummaries"])
    stub.add("listBlocks", [])
    stub.add("listRuns", [])
    out = patched_server.dbs_summary(subject="/A/B-v1/RAW")
    assert "zero_row_diagnosis" in out["summary"]
