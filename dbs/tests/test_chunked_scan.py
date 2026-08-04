"""A whole-era block scan is too big for DBS. Split it by data tier.

Measured 2026-08-04 against production: dbs_aggregate over /*/HIRun2026A*/*
sent one listBlocks for the entire era. It ran 312 s and then the SERVER
dropped it:

    (92, 'HTTP/2 stream 11 was not closed cleanly: INTERNAL_ERROR (err 2)')

The same call had succeeded earlier in the day, so this is a size limit the
server enforces unpredictably, not a clean error we can plan around. archi
kills any tool at 120 s, so a scan that sometimes needs 300+ s can never be
relied on.

One request per data tier keeps each response inside what DBS serves. The
tier is always the third path segment, so this splits any broad pattern, not
just an era one. A chunk that still fails is reported, never silently
dropped: a total that quietly lost a tier is worse than no total.
"""

from __future__ import annotations

import pytest

PATTERN = "/*/HIRun2026A*/*"
TIERS = ["AOD", "MINIAOD", "RAW"]


def _names(per_tier=100):
    return [f"/Primary{i}/HIRun2026A-PromptReco-v1/{tier}"
            for tier in TIERS for i in range(per_tier)]


def _wire(stub, names, per_chunk_blocks=None):
    """listDatasets resolves the names; listBlocks answers per tier pattern."""
    rows = [{"dataset": n, "dataset_access_type": "VALID"} for n in names]
    stub.add("listDatasets", rows)

    def blocks(kwargs):
        wanted = kwargs["dataset"]
        tier = wanted.rsplit("/", 1)[-1]
        if per_chunk_blocks and tier in per_chunk_blocks:
            return per_chunk_blocks[tier]
        out = []
        for n in names:
            if tier != "*" and not n.endswith("/" + tier):
                continue
            out.append({"dataset": n, "block_name": n + "#b1",
                        "block_size": 1000, "file_count": 10})
        return out

    stub.add("listBlocks", blocks)


@pytest.fixture(autouse=True)
def _deterministic(monkeypatch):
    """One worker so the recording stub sees an ordered call list."""
    monkeypatch.setenv("DBS_SCAN_WORKERS", "1")


# ---- when to split --------------------------------------------------------

def test_a_broad_pattern_is_scanned_one_tier_at_a_time(patched_server, stub):
    names = _names()
    _wire(stub, names)
    patched_server.dbs_aggregate(pattern=PATTERN, status="VALID", group_by="tier")
    scanned = sorted(c["dataset"] for c in stub.calls_for("listBlocks"))
    assert scanned == ["/*/HIRun2026A*/AOD", "/*/HIRun2026A*/MINIAOD",
                       "/*/HIRun2026A*/RAW"]


def test_a_small_pattern_still_uses_one_call(patched_server, stub):
    names = _names(per_tier=2)
    _wire(stub, names)
    patched_server.dbs_aggregate(pattern=PATTERN, status="VALID", group_by="tier")
    assert [c["dataset"] for c in stub.calls_for("listBlocks")] == [PATTERN]


def test_a_tier_specific_pattern_is_never_chunked(patched_server, stub):
    names = [f"/Primary{i}/HIRun2026A-PromptReco-v1/AOD" for i in range(500)]
    _wire(stub, names)
    patched_server.dbs_aggregate(pattern="/*/HIRun2026A*/AOD", status="VALID")
    assert [c["dataset"] for c in stub.calls_for("listBlocks")] == ["/*/HIRun2026A*/AOD"]


def test_chunking_can_be_switched_off(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_CHUNK_MIN", "0")
    names = _names()
    _wire(stub, names)
    patched_server.dbs_aggregate(pattern=PATTERN, status="VALID", group_by="tier")
    assert [c["dataset"] for c in stub.calls_for("listBlocks")] == [PATTERN]


# ---- the answer must not change ------------------------------------------

def test_chunked_totals_equal_the_single_call_totals(patched_server, stub, monkeypatch):
    names = _names()
    _wire(stub, names)
    chunked = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                           group_by="tier")
    monkeypatch.setenv("DBS_SCAN_CHUNK_MIN", "0")
    stub.calls.clear()
    single = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                          group_by="tier")
    assert chunked["totals"] == single["totals"]
    assert {g["group"]: g["bytes"] for g in chunked["groups"]} == \
           {g["group"]: g["bytes"] for g in single["groups"]}


def test_every_tier_is_represented(patched_server, stub):
    names = _names()
    _wire(stub, names)
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    assert {g["group"] for g in out["groups"]} == set(TIERS)
    assert out["totals"]["bytes"] == 300 * 1000


# ---- a lost chunk is reported, never hidden -------------------------------

def test_a_failed_chunk_is_reported_not_swallowed(patched_server, stub):
    names = _names()

    def blocks(kwargs):
        tier = kwargs["dataset"].rsplit("/", 1)[-1]
        if tier == "RAW":
            raise RuntimeError("HTTP/2 stream was not closed cleanly")
        return [{"dataset": n, "block_name": n + "#b1", "block_size": 1000,
                 "file_count": 10} for n in names if n.endswith("/" + tier)]

    stub.add("listDatasets", [{"dataset": n, "dataset_access_type": "VALID"}
                              for n in names])
    stub.add("listBlocks", blocks)
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    coverage = out["coverage"]
    assert coverage["complete"] is False
    assert "RAW" in (coverage["truncation_reason"] or "")
    assert out["totals"]["bytes"] == 200 * 1000, "the tiers that worked still count"


def test_all_chunks_failing_is_still_honest(patched_server, stub):
    names = _names()

    def blocks(kwargs):
        raise RuntimeError("server dropped the stream")

    stub.add("listDatasets", [{"dataset": n, "dataset_access_type": "VALID"}
                              for n in names])
    stub.add("listBlocks", blocks)
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    assert out["coverage"]["complete"] is False
    assert out["totals"]["bytes"] == 0


def test_chunk_calls_are_counted_in_provenance(patched_server, stub):
    names = _names()
    _wire(stub, names)
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    # 1 listDatasets + one listBlocks per tier
    assert out["provenance"]["n_server_calls"] == 1 + len(TIERS)
