"""Scaffolding sanity: the stub wiring, the fixtures, and characterization
tests that pin CURRENT server behavior (including the known holes) so later
commits change them consciously, red-green."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import READ_METHODS, WRITE_METHODS, load_fixture

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_all_fixtures_parse_and_stay_consistent():
    names = sorted(p.name for p in FIXTURES.glob("*.json"))
    assert len(names) == 6
    for name in names:
        json.loads((FIXTURES / name).read_text())

    park = load_fixture("parkingbph1_mixed_validity.json")
    all_row = park["filesummaries_all_files"][0]
    valid_row = park["filesummaries_valid_only"][0]
    assert all_row["file_size"] - valid_row["file_size"] == park["delta_bytes"]
    assert all_row["num_file"] - valid_row["num_file"] == park["delta_files"] == 26
    assert all_row["num_event"] - valid_row["num_event"] == park["delta_events"]

    w24 = load_fixture("winter24_multi_status.json")
    valid = set(w24["datasets_valid_names"])
    valid_sum = sum(b["block_size"] for b in w24["blocks_detail_all"] if b["dataset"] in valid)
    all_sum = sum(b["block_size"] for b in w24["blocks_detail_all"])
    assert valid_sum == w24["expected_valid_bytes"]
    assert all_sum == w24["expected_unfiltered_bytes"]
    assert all_sum > 8 * valid_sum  # the trap must be big enough to notice

    nano = load_fixture("merged_file_nanoaod.json")
    per_lumi_sum = sum(sum(row["event_count"]) for row in nano["filelumis"])
    assert per_lumi_sum == nano["whole_file_events"]
    assert nano["true_events_315257"] + nano["true_events_315258"] == nano["whole_file_events"]

    zero = load_fixture("nonexistent_path.json")["filesummaries"][0]
    assert zero["num_block"] == 0 and zero["num_file"] == 0


def test_stub_records_calls_and_kwargs(patched_server, stub):
    stub.add("listDatasets", [{"dataset": "/A/B/RAW"}])
    result = patched_server.dbs_list_datasets(dataset="/A/*/RAW", dataset_access_type="VALID")
    assert result == [{"dataset": "/A/B/RAW"}]
    assert stub.calls_for("listDatasets") == [
        {"dataset": "/A/*/RAW", "dataset_access_type": "VALID", "detail": False}
    ]


def test_stub_match_table(patched_server, stub):
    stub.add("listFileSummaries", [{"num_event": 1}], match={"run_num": 315257})
    stub.add("listFileSummaries", [{"num_event": 2}], match={"run_num": 315258})
    one = patched_server._dbs_client().listFileSummaries(dataset="/X/Y/Z", run_num=315257)
    two = patched_server._dbs_client().listFileSummaries(dataset="/X/Y/Z", run_num=315258)
    assert one[0]["num_event"] == 1 and two[0]["num_event"] == 2


def test_none_kwargs_are_dropped_but_detail_false_is_sent(patched_server, stub):
    stub.add("listBlocks", [])
    patched_server.dbs_list_blocks(dataset="/A/B/RAW")
    [kwargs] = stub.calls_for("listBlocks")
    assert "block_name" not in kwargs and "run_num" not in kwargs
    assert kwargs["detail"] is False


# --- characterization: pins the CURRENT holes; commit 2 flips these on purpose


def test_current_method_discovery_exposes_write_methods(patched_server, stub):
    methods = patched_server._public_methods()
    for name in WRITE_METHODS:
        assert name in methods, "characterization: the write hole is open today"
    for name in READ_METHODS:
        assert name in methods


def test_current_empty_result_is_a_bare_empty_list(patched_server, stub):
    stub.add("listDatasets", [])
    result = patched_server.dbs_list_datasets(dataset="/No/Match/RAW")
    assert result == []  # characterization: silent empty today
