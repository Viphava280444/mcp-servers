"""Zero matches must come back with a way forward, not just a shrug.

Measured failure this guards (exam AGG-01, 2026-08-03): the model asked for
/HIForward/HIRun2026A-PromptReco-v*/AOD. The real datasets are HIForward0 to
HIForward29, so DBS matched nothing. The tool said "check the name or widen
the status", the model widened the STATUS twice, still got nothing, and then
answered "0 bytes, no such datasets" — a confident wrong answer built on an
empty result.

The fix is general: when a three-part pattern matches nothing, widen ONE
segment at a time and report which wider pattern actually has data. Nothing
here knows about HIForward; it is the same recovery for any name that is too
narrow by a suffix.
"""

from __future__ import annotations

import pytest

NARROW = "/HIForward/HIRun2026A-PromptReco-v1/AOD"
WIDER = "/HIForward*/HIRun2026A-PromptReco-v1/AOD"

THIRTY = [{"dataset": f"/HIForward{i}/HIRun2026A-PromptReco-v1/AOD",
           "dataset_access_type": "VALID"} for i in range(30)]


def _wire_narrow_miss(stub, wider_rows=THIRTY):
    """The narrow pattern matches nothing; the primary-widened one matches."""
    stub.add("listDatasets", [], match={"dataset": NARROW})
    stub.add("listDatasets", wider_rows, match={"dataset": WIDER})
    stub.add("listDatasets", [], match={"dataset": NARROW + "*"})


def test_zero_match_probes_a_wider_primary_name(patched_server, stub):
    _wire_narrow_miss(stub)
    out = patched_server.dbs_aggregate(pattern=NARROW, status="VALID")
    assert out["coverage"]["n_matched"] == 0
    suggestions = out["did_you_mean"]
    assert [s["pattern"] for s in suggestions] == [WIDER]
    assert suggestions[0]["n_datasets"] == 30
    assert suggestions[0]["example"] == "/HIForward0/HIRun2026A-PromptReco-v1/AOD"


def test_hint_names_the_pattern_to_rerun(patched_server, stub):
    _wire_narrow_miss(stub)
    out = patched_server.dbs_aggregate(pattern=NARROW, status="VALID")
    assert WIDER in out["hint"], "the hint must carry the pattern, not just advice"
    assert "30" in out["hint"]


def test_probe_keeps_the_requested_status(patched_server, stub):
    """A status-hidden dataset must not be smuggled in by the probe."""
    stub.add("listDatasets", [], match={"dataset": NARROW})
    stub.add("listDatasets", THIRTY, match={"dataset": WIDER})
    stub.add("listDatasets", [], match={"dataset": NARROW + "*"})
    patched_server.dbs_aggregate(pattern=NARROW, status="INVALID")
    probes = [c for c in stub.calls_for("listDatasets") if c["dataset"] != NARROW]
    assert probes, "expected at least one probe"
    for call in probes:
        assert call["dataset_access_type"] == "INVALID"


def test_probe_skips_segments_that_already_have_a_wildcard(patched_server, stub):
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/A*/B*/C*", status="VALID")
    assert out["did_you_mean"] == []
    assert len(stub.calls_for("listDatasets")) == 1, "nothing left to widen"


def test_probe_is_bounded_to_one_per_segment(patched_server, stub):
    stub.add("listDatasets", [])
    patched_server.dbs_aggregate(pattern="/A/B/C", status="VALID")
    # 1 real query + at most 3 probes, one per path segment
    assert len(stub.calls_for("listDatasets")) <= 4
    probed = {c["dataset"] for c in stub.calls_for("listDatasets")}
    assert probed == {"/A/B/C", "/A*/B/C", "/A/B*/C", "/A/B/C*"}


def test_no_suggestion_when_nothing_wider_matches(patched_server, stub):
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/Nope/Nope/NOPE", status="VALID")
    assert out["did_you_mean"] == []
    assert "no datasets match" in out["hint"]


def test_probe_calls_are_counted_in_provenance(patched_server, stub):
    _wire_narrow_miss(stub)
    out = patched_server.dbs_aggregate(pattern=NARROW, status="VALID")
    assert out["provenance"]["n_server_calls"] == len(stub.calls_for("listDatasets"))


def test_malformed_pattern_does_not_crash(patched_server, stub):
    stub.add("listDatasets", [])
    out = patched_server.dbs_aggregate(pattern="/OnlyTwo/Parts", status="VALID")
    assert out["did_you_mean"] == []
    assert out["coverage"]["n_matched"] == 0


def test_a_probe_that_errors_is_ignored(patched_server, stub):
    def blow_up(kwargs):
        if kwargs["dataset"] != NARROW:
            raise RuntimeError("DBS said no")
        return []
    stub.add("listDatasets", blow_up)
    out = patched_server.dbs_aggregate(pattern=NARROW, status="VALID")
    assert out["did_you_mean"] == []
    assert out["coverage"]["n_matched"] == 0


def test_count_only_zero_also_gets_the_suggestion(patched_server, stub):
    _wire_narrow_miss(stub)
    out = patched_server.dbs_aggregate(pattern=NARROW, status="VALID", count_only=True)
    assert [s["pattern"] for s in out["did_you_mean"]] == [WIDER]


def test_a_successful_match_carries_no_suggestions(patched_server, stub):
    stub.add("listDatasets", THIRTY)
    stub.add("listBlocks", [])
    out = patched_server.dbs_aggregate(pattern=WIDER, status="VALID", count_only=True)
    assert out["coverage"]["n_matched"] == 30
    assert "did_you_mean" not in out
