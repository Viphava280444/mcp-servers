# The golden question set

Twenty real questions about the CMS catalog, each with an answer taken from
the live production DBS reader and verified a second time before being written
down. This is how the claims in the pull request were measured, so anyone can
re-run them and disagree with the numbers.

| file | what it is |
|---|---|
| `golden-qa.jsonl` | one JSON object per line — the machine-readable set |
| `golden-qa.md` | the same content laid out to read, with the before-and-after score |

## Fields

- `question` — asked to the chat exactly as written, in a fresh conversation.
- `answer` — the verified answer.
- `grading_rule` — what counts as correct. This is the part that matters:
  it names the facts an answer must contain and the values that must be
  rejected. Several rules exist to catch a specific wrong answer that the DBS
  API makes easy to produce.
- `stability` — `stable` means the number cannot legitimately move, so a
  correct answer must match exactly. `drifts` means live data changes; the
  grading rule says in which direction and by how much.
- `repro` — a command to check the answer outside any chat.
- `provenance` — instance, status filter and when it was measured.

## How the questions were chosen

They cover six areas: discovery, aggregation, one dataset in depth, blocks and
files, runs and lineage, and edge cases. Several were written specifically to
land on measured DBS behaviour that a thin wrapper cannot hide, for example:

- the dataset status filter defaults to VALID silently;
- the blocks endpoint ignores the status filter entirely, which makes one
  campaign total 9x too large;
- `validFileOnly` is presence-checked, so sending `0` behaves like `1`;
- per-run event counts from summary endpoints count whole files, so on merged
  tiers they are an upper bound, not a count;
- nothing ever returns 404 — a typo, a missing dataset and a status-hidden
  dataset all come back as an empty result.

A question that only a correctly-built tool can answer is worth more than ten
that any wrapper passes.

## Grading

Each answer was graded against its rule by four independent graders, five
questions each, with no knowledge of which version produced it. The same
rubric ran on both versions.

Verdicts are `PASS` (every required fact present and correct), `PARTIAL` (right
subject, but a required fact missing or one number wrong) and `FAIL` (no
answer, wrong subject, or a required number wrong).

## Re-running it

These are not unit tests and `pytest` does not touch them. They need a running
chat with the DBS server attached, because they measure the whole path: the
model choosing a tool, the tool talking to DBS, and the answer that comes back.

The offline suite in `dbs/tests/` is the fast check. This set is the slow one
that catches what mocks cannot — a tool that returns correct data and still
leads to a wrong answer.

## A caution about the numbers

Seven of the twenty drift. CMS keeps producing data, so counts grow and
datasets get invalidated. Read `stability` and the grading rule before
treating a mismatch as a failure. The `stable` thirteen are frozen: Run2018
finished, and RAW is written once and never regenerated.

All of it is read from the public production reader with a standard CMS grid
certificate. Nothing here required special access.
