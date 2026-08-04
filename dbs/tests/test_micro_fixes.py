"""The four minimal-diff fixes to the existing tools (spec section 3):

1. dbs_call read-only lock that actually works
2. shared output cap with an honest truncation note
3. VALID-default note on dbs_list_datasets
4. "0 rows matched" instead of a silent empty result
"""

from __future__ import annotations

import json

import pytest

from conftest import READ_METHODS, WRITE_METHODS


# ---- fix 1: read-only lock ------------------------------------------------

def test_method_discovery_hides_write_methods(patched_server, stub):
    methods = patched_server._public_methods()
    for name in WRITE_METHODS:
        assert name not in methods
    for name in READ_METHODS:
        assert name in methods


def test_dbs_call_rejects_a_write_method_before_any_http(patched_server, stub):
    with pytest.raises(ValueError, match="read-only"):
        patched_server.dbs_call(method="updateFileStatus", kwargs={"status": 0})
    assert stub.calls == []  # nothing reached the client


def test_dbs_call_still_serves_reads(patched_server, stub):
    stub.add("serverinfo", [{"dbs_version": "v00.06.44"}])
    result = patched_server.dbs_call(method="serverinfo")
    assert result == [{"dbs_version": "v00.06.44"}]


# ---- fix 2: shared output cap ---------------------------------------------

def test_oversize_list_is_truncated_with_note(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_RESULT_CAP_BYTES", "600")
    rows = [{"dataset": f"/Primary{i:04d}/Era-v1/RAW"} for i in range(50)]
    stub.add("listDatasets", rows)
    result = patched_server.dbs_list_datasets(dataset="/*/Era*/RAW", dataset_access_type="*")
    assert isinstance(result, list)
    note = result[-1]
    assert isinstance(note, str) and "truncated: showing" in note and "of 50 records" in note
    kept = result[:-1]
    assert 0 < len(kept) < 50
    assert kept == rows[: len(kept)]
    assert len(json.dumps(kept)) <= 600


def test_small_results_are_untouched(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_RESULT_CAP_BYTES", "262144")
    rows = [{"dataset": "/A/B/RAW"}]
    stub.add("listDatasets", rows)
    result = patched_server.dbs_list_datasets(dataset="/A/B/RAW", dataset_access_type="*")
    assert result == rows


def test_cap_applies_to_dbs_call_too(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_RESULT_CAP_BYTES", "300")
    rows = [{"logical_file_name": f"/store/data/f{i}.root"} for i in range(30)]
    stub.add("listFiles", rows)
    result = patched_server.dbs_call(method="listFiles", kwargs={"dataset": "/A/B/RAW"})
    assert isinstance(result[-1], str) and "truncated" in result[-1]


# ---- fix 3: VALID-default note --------------------------------------------

def test_valid_default_note_appended_when_no_status_given(patched_server, stub):
    stub.add("listDatasets", [{"dataset": "/A/B/RAW"}])
    result = patched_server.dbs_list_datasets(dataset="/A/*/RAW")
    note = result[-1]
    assert isinstance(note, str)
    assert "VALID datasets only" in note and "dataset_access_type='*'" in note


def test_no_note_when_status_is_explicit(patched_server, stub):
    stub.add("listDatasets", [{"dataset": "/A/B/RAW"}])
    result = patched_server.dbs_list_datasets(dataset="/A/*/RAW", dataset_access_type="*")
    assert all(not (isinstance(x, str) and "VALID datasets only" in x) for x in result)


# ---- fix 4: empty results say so ------------------------------------------

def test_empty_list_becomes_zero_rows_matched(patched_server, stub):
    stub.add("listBlocks", [])
    result = patched_server.dbs_list_blocks(dataset="/No/Match/RAW")
    assert result == "0 rows matched"


def test_empty_dataset_listing_keeps_the_default_note(patched_server, stub):
    stub.add("listDatasets", [])
    result = patched_server.dbs_list_datasets(dataset="/No/Match/RAW")
    assert isinstance(result, str)
    assert result.startswith("0 rows matched")
    assert "VALID datasets only" in result
