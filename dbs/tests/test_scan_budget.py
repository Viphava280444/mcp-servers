"""Always answer inside the caller's time limit. Never pretend it was whole.

Measured 2026-08-04 against production, era HIRun2026A (1,693 datasets):

- one listBlocks for the whole era: 312 s, then the server dropped the
  HTTP/2 stream with INTERNAL_ERROR;
- split one request per data tier: 305 s, and the ALCARECO tier (1,002
  datasets) did not finish on its own inside 400 s.

So no amount of splitting brings this under the 120 s at which archi kills a
tool call. The data is simply larger than DBS will hand over in that window.

That leaves one honest option: scan what fits in a budget, return it, and say
plainly which tiers are missing and the exact call that fetches each one. The
rule that matters most is the last test in this file — a tier that was never
scanned reports `null`, never `0`. A zero is a number a reader will add up.
"""

from __future__ import annotations

import pytest

PATTERN = "/*/HIRun2026A*/*"
TIERS = ["AOD", "ALCARECO", "RAW"]


def _names(per_tier=100):
    return [f"/Primary{i}/HIRun2026A-PromptReco-v1/{tier}"
            for tier in TIERS for i in range(per_tier)]


def _wire(stub, names):
    stub.add("listDatasets", [{"dataset": n, "dataset_access_type": "VALID"}
                              for n in names])

    def blocks(kwargs):
        tier = kwargs["dataset"].rsplit("/", 1)[-1]
        return [{"dataset": n, "block_name": n + "#b1", "block_size": 1000,
                 "file_count": 10} for n in names
                if tier == "*" or n.endswith("/" + tier)]

    stub.add("listBlocks", blocks)


@pytest.fixture(autouse=True)
def _sequential(monkeypatch):
    monkeypatch.setenv("DBS_SCAN_WORKERS", "1")


def _clock(monkeypatch, server, ticks):
    it = iter(ticks)
    last = [ticks[-1]]

    def fake():
        try:
            return next(it)
        except StopIteration:
            return last[0]

    monkeypatch.setattr(server.time, "monotonic", fake)


# ---- the budget stops the scan -------------------------------------------

def test_the_scan_stops_when_the_budget_is_spent(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "90")
    _wire(stub, _names())
    # start, then a check before each remaining chunk: first cheap, then over
    _clock(monkeypatch, patched_server, [0.0, 10.0, 999.0])
    patched_server.dbs_aggregate(pattern=PATTERN, status="VALID", group_by="tier")
    scanned = [c["dataset"] for c in stub.calls_for("listBlocks")]
    assert len(scanned) < len(TIERS), "the budget must cut the scan short"


def test_an_unscanned_tier_reports_null_not_zero(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "90")
    _wire(stub, _names())
    _clock(monkeypatch, patched_server, [0.0, 999.0])
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    missing = [g for g in out["groups"] if g.get("scanned") is False]
    assert missing, "some tier must be marked unscanned"
    for group in missing:
        assert group["bytes"] is None, "0 would be added up as if it were real"
        assert group["files"] is None
        assert group["n_datasets"] > 0, "the dataset count is known from names"


def test_a_scanned_tier_keeps_exact_numbers(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "90")
    _wire(stub, _names())
    _clock(monkeypatch, patched_server, [0.0, 999.0])
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    done = [g for g in out["groups"] if g.get("scanned") is not False]
    assert done
    for group in done:
        assert group["bytes"] == 100 * 1000


def test_coverage_says_it_is_incomplete(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "90")
    _wire(stub, _names())
    _clock(monkeypatch, patched_server, [0.0, 999.0])
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    assert out["coverage"]["complete"] is False
    assert out["coverage"]["truncation_reason"]


def test_the_hint_gives_the_exact_follow_up_calls(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "90")
    _wire(stub, _names())
    _clock(monkeypatch, patched_server, [0.0, 999.0])
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    missing = [g["group"] for g in out["groups"] if g.get("scanned") is False]
    for tier in missing:
        assert f"/*/HIRun2026A*/{tier}" in out["hint"], \
            "name the pattern that fetches the missing tier"


def test_partial_totals_are_flagged(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "90")
    _wire(stub, _names())
    _clock(monkeypatch, patched_server, [0.0, 999.0])
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    assert out["totals"].get("partial") is True
    assert out["totals"]["bytes_partial_reason"]


# ---- the budget does not fire when there is time --------------------------

def test_a_generous_budget_scans_everything(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "90")
    _wire(stub, _names())
    _clock(monkeypatch, patched_server, [0.0, 1.0, 2.0, 3.0, 4.0])
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    assert out["coverage"]["complete"] is True
    assert out["totals"]["bytes"] == 300 * 1000
    assert out["totals"].get("partial") is not True


def test_the_budget_can_be_switched_off(patched_server, stub, monkeypatch):
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "0")
    _wire(stub, _names())
    _clock(monkeypatch, patched_server, [0.0, 999.0, 999.0, 999.0])
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    assert out["coverage"]["complete"] is True
    assert len(stub.calls_for("listBlocks")) == len(TIERS)


# ---- the budget must hold even when a worker is stuck --------------------

def test_a_stuck_worker_cannot_hold_the_answer_hostage(patched_server, stub,
                                                       monkeypatch):
    """A thread inside a slow DBS call cannot be cancelled, so the scan must
    stop WAITING rather than try to stop the work. Exiting a ThreadPoolExecutor
    context manager joins every worker, which would defeat the budget."""
    import threading
    monkeypatch.delenv("DBS_SCAN_WORKERS", raising=False)
    monkeypatch.setenv("DBS_SCAN_WORKERS", "3")
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "1")
    release = threading.Event()
    names = _names()
    stub.add("listDatasets", [{"dataset": n, "dataset_access_type": "VALID"}
                              for n in names])

    def blocks(kwargs):
        tier = kwargs["dataset"].rsplit("/", 1)[-1]
        if tier == "ALCARECO":
            release.wait(30)          # the stall the budget must escape
        return [{"dataset": n, "block_name": n + "#b1", "block_size": 1000,
                 "file_count": 10} for n in names if n.endswith("/" + tier)]

    stub.add("listBlocks", blocks)
    import time as _time
    started = _time.monotonic()
    out = patched_server.dbs_aggregate(pattern=PATTERN, status="VALID",
                                       group_by="tier")
    elapsed = _time.monotonic() - started
    release.set()
    assert elapsed < 10, f"returned in {elapsed:.1f}s; the budget was 1s"
    assert out["coverage"]["complete"] is False
    stalled = [g for g in out["groups"] if g["group"] == "ALCARECO"]
    assert stalled and stalled[0]["bytes"] is None


def test_an_unsplittable_pattern_gets_a_way_down_not_the_same_advice(
        patched_server, stub, monkeypatch):
    """One huge tier cannot be split by tier. Repeating the same pattern would
    stall again, so the hint must offer a narrower primary name instead."""
    import threading
    monkeypatch.setenv("DBS_SCAN_WORKERS", "2")
    monkeypatch.setenv("DBS_SCAN_BUDGET_S", "1")
    release = threading.Event()
    names = [f"/Primary{i}/HIRun2026A-PromptReco-v1/ALCARECO" for i in range(300)]
    stub.add("listDatasets", [{"dataset": n, "dataset_access_type": "VALID"}
                              for n in names])

    def blocks(kwargs):
        release.wait(30)
        return []

    stub.add("listBlocks", blocks)
    out = patched_server.dbs_aggregate(pattern="/*/HIRun2026A*/ALCARECO",
                                       status="VALID")
    release.set()
    assert out["coverage"]["complete"] is False
    assert "/*/HIRun2026A*/ALCARECO" not in out["hint"].split("e.g.")[-1], \
        "do not send the model back to the pattern that just stalled"
    assert "dbs_summary" in out["hint"] or "Narrow" in out["hint"]
