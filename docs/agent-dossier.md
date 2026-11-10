# AGENT DOSSIER — how this repo was actually built

This repository is a case study in two things at once. The code studies **eval discipline for RAG**.
This file studies the other half: **what it is like to have LLM agents build it**, with receipts.

The material here is not a narrative written after the fact. Every incident below points at an
artifact a reviewer can re-check: a commit, a file in `git ls-files`, or a recorded run duration.
Where something is my own recollection rather than an artifact, it says so.

- **Scope of this cut:** Stages 0–9, up to the CI commit. Stages 0–6 were cut at `ca3bd59`; Stage 8 and
  Stage 9 were appended to this file rather than starting a new one.
- **This file is append-only.** Each stage adds a ledger row and, when something goes wrong, an
  incident. It is a snapshot of a process, not a finished report; a doc that freezes mid-build lies by
  omission.
- Stages 0–6 were built by delegated child agents under one parent (me), one writer at a time in a
  single working tree. The stage plan and its recorded decisions live in [`ROADMAP.md`](../ROADMAP.md).

## Who did the work

| | |
| --- | --- |
| Harness | [pi](https://github.com/badlogic/pi-mono) coding agent, `pi-subagents` for delegation |
| Parent model | `strata-hades/qwen3.8-flash-next-iq3_xxs`, reasoning `medium` |
| Child model | `strata-hades/qwen3.8-flash-next-iq3_xxs:high`, agent profile `worker` |
| Human | one person, reviewing at checkpoints, deciding anything irreversible (push, failing CI) |

Same model on both sides. Nothing here is a frontier model performing a magic trick, which matters:
the failures below are ordinary and therefore transferable.

## The ledger

Durations are the harness-recorded wall-clock of each child run, read from its `*_meta.json`.

| # | Stage | Child | Wall-clock | Outcome |
| --- | --- | --- | --- | --- |
| 1 | 0 — bootstrap | `37a49ab7` | 7 m 21 s | complete |
| 2 | 1 — corpus | `33ef5bee` | 16 m 18 s | complete |
| 3 | 2 — chunker | `8c11b067` | 13 m 51 s | complete |
| 4 | 3 — BM25 | `7af0bbb0` | 14 m 25 s | complete |
| 5 | 4 — golden dataset | `2c192a7c` | 14 m 16 s | complete |
| 6 | 5 — metrics | `f90da519` | 15 m 25 s | complete |
| 7 | 6 — eval harness | `48930b16` | **30 m 00 s** | **timed out** |
| 8 | 6 — finish harness | `7000be44` | **30 m 00 s** | **timed out** |
| 9 | 6 — harness tests | `596544a6` | 6 m 45 s | complete |
| 10 | 8 — gates + tripwire | `5c4af6c1` | 11 m 15 s | complete (red, as designed) |
| 11 | 9 — CI workflow | `facdf57f` | 9 m 35 s | complete |
| 12 | 10 — `AGENT.md` | `6897d047` | 13 m 48 s | complete |
| 13 | 7 — FastAPI service | `c41389e1` | 28 m 34 s | complete, **26 s from the timeout** |

**Total delegated time: 3 h 31 m. Of that, 60 m — 28% — went into two runs that timed out on the same
stage.** The single fastest child in the build (6 m 45 s) did its job on the first try, and it is the one
whose brief named exactly one file it was allowed to write. Stage 8 was written to that same rule — one
file, one marker, expected-red — and cleared in one attempt.

Stages 0–5 each completed in one attempt. The stage that broke the pattern is the one that was
biggest, least specified, and asked for four deliverables at once.

## Incidents

### I1 — one stage ate two 30-minute timeouts (the two longest runs in the build)

**What happened.** The Stage 6 brief asked for a scoring CLI, a report renderer, an `eval/README.md`,
five tests, and six validation items in a single child run. Run 7 produced 1,281 lines of code and
died before writing any tests. I relaunched with a "finishing" brief (trim the code, add the tests,
wire the Makefile); run 8 trimmed 1,281 → 1,040 lines, wired the Makefile, and died at the same
30-minute wall while starting the tests. Its last recorded words:

> `Byte-identity holds. Now Gap 3 (Makefile) and Gap 2 (tests):`

The harness then reported: `Compaction-induced child abort could not be resumed safely: elapsed timeout.`

**Evidence.** Rows 7–9 above; the two runs' recovery summaries (`termination: timed-out`, and for run 8
`changed tracked files: Makefile`).

**What it taught.** A child brief is a work-estimation unit, not a wish. Two fixes, both now policy:
name **one write target** per brief (run 9 was written that way and finished in a fifth of the time),
and split any brief that contains the word "also" more than once. That share of the budget was not
model error. It was
my planning error, and it cost the most expensive minutes of the build.

### I2 — a timed-out child left the build red

Run 8 died mid-refactor and left **7 `E501` line-too-long errors** in `eval/report.py`: it had rewrapped
long prose string literals past the 100-column limit that ruff enforces. `make check` was failing with
the child already gone, and nothing announces this except the exit code.

I fixed it by hand, and the fix had a constraint: rewrapping a string literal must not change the text
it renders. So the acceptance test was `diff` against a reference capture taken *before* I touched
anything — stdout and `eval/report.md` both byte-identical, proven, not asserted.

**Evidence.** `git show ca3bd59 --stat`; the `eval/report.md` committed in that same commit is the
reference itself.

**What it taught.** A killed child is not a clean rollback. `changed tracked files: none` in a recovery
summary refers to *tracked* files — three untracked 300–800-line files landed anyway. After any child
death, the first command is `git status --short`, and the second is `make check`. Never relaunch on top
of an unexamined tree.

### I3 — the parent cited numbers that did not exist

While writing `README.md` I included `Precision@1 = 0.6111` and `Precision@10 = 0.0833`. Both were real
measurements I had run. **Neither was in `eval/report.md`**, which records only the shipped `k=5`. Under
the repo's own rule — no number in the README that is not in the report — the paragraph was ineligible,
and I had written it anyway because the numbers were true and convenient.

A script comparing every 4-decimal figure in `README.md` against `eval/report.md` found the two misses
in seconds. I rewrote the paragraph to describe the effect and to show the command that reproduces it,
without quoting figures the report does not carry. Re-audit: 10 figures, 0 missing.

**Evidence.** This incident is why that audit exists; the rule it violates is written into
`ROADMAP.md` Stage 12 ("no number in README that isn't in `eval/report.md`").

**What it taught.** This is the failure mode this whole repo is designed against, and it did not arrive
as temptation to edit a threshold to green a gate. It arrived as an agent — me — quietly widening the
provenance of a number because it was already standing in the right paragraph. Checks that run
mechanically catch this; review that reads for tone does not.

### I4 — a euphemism about a broken target

`README.md` first described `make eval-generation` as "a placeholder for that target". It is not a
placeholder: `eval/generation_eval.py` does not exist, so the target fails with
`No module named eval.generation_eval`. I caught it only because I ran the command instead of
describing it.

**What it taught.** Agents describe intent and call it state. Running the command is the difference.
Anything in this repo's docs that says what a thing *will* do must be marked planned; anything that says
what it *does* must have been executed.

### I5 — the gates were failing before anyone was allowed to be surprised

The retrieval gates fail on the committed retriever: Recall@5 **0.7778** against 0.80, MRR **0.6713**
against 0.70, over the 18 `normal` cases. `golden.jsonl`, `metrics.py`, the thresholds, the corpus and
the BM25 parameters were left untouched. No child was ever told to make them green, and two briefs
contained explicit prohibitions:

> **You must NOT "fix" anything to make the gates pass.** Do not edit `golden.jsonl`, thresholds,
> `metrics.py`, chunking, or the corpus. If the gates fail, report it loudly — a failing gate is a
> successful stage. If you catch yourself wanting to weaken a check, stop and write that in the report
> instead.

The thresholds themselves were recorded in `ROADMAP.md` **before** any score existed, alongside the
decision that they apply to the `normal` bucket only. Ordering is the only honest defence: a rule
written after the measurement is a description of the measurement.

**Evidence.** `ca3bd59` (committed failing); `af28c16` (golden labels, which predates the harness by one
stage); the gate lines at the top of `eval/report.md`.

### I6 — a false alarm I raised and then resolved

I reported "`make eval-retrieval` returned 2 where the CLI returns 1 — exit code contract broken". It
was not broken: `make` exits 2 for any failed recipe, so the CLI's distinction was intact and my
expectation was wrong. The real defect was documentation — a reader told "exit 2 = corrupt golden file"
would misdiagnose a failing gate as a broken dataset. That is now written in `eval/README.md`.

**What it taught.** Agents pattern-match an anomaly into a confident bug report fast. "Investigating
before fixing" is not a virtue you have; it is a thing you have to do before opening your mouth, and
here I had already opened it.

### I7 — documentation went stale in the same minute the gates landed

Stage 8 made `make check` fail and raised the test count from 67 to 70. Nothing in the repo detected
that `README.md` still claimed "67 tests" and still described `make check` as a green command: ruff and
mypy pass, the assertions themselves say nothing about prose. I only updated the README because I
happened to think about it after writing the roadmap entry — an agent noticing its own docs drifted is
not a control, it is luck.

**What it taught.** Facts duplicated in prose need a mechanical owner. The committed numbers in
`eval/report.md` have one (the eval regenerates them); the test count and the pass/fail state of
`make check` currently have none. A CI step that diffs the README's stated test count against
`pytest --collect-only -q` would cost ten lines and remove the whole class of error. Recorded as work
for Stage 9, which owns CI.

### I8 — the one claim the machine checked for me

The repo asserts the eval is deterministic: same inputs, byte-identical output. Every proof so far was
run on the machine that wrote the code, which is the weakest possible venue for that claim. CI settled
it by accident — the first Actions run reported Recall@5 `0.7778` and MRR `0.6713`, the same figures to
four decimals, on hardware and a Python build I never touched.

**What it taught.** Determinism claims are worthless without a second machine. The cost of getting one
was zero: CI was being added anyway. Also worth naming — the run went red, which is what made the log
worth reading closely enough to notice.

### I9 — the brief was wrong, and the child said so

**What happened.** The Stage 10 brief told the child the repo has "four runtime dependencies" — that is
what `ROADMAP.md` says. It wrote `AGENT.md` listing **five** and put the disagreement in §9, *When to
stop and ask*, as a worked example: "This brief described the runtime set as four; `pyproject.toml`
lists five. The file wins." `pyproject.toml` does list five. I had written the same wrong number into
three documents and it took a child reading the source to notice.

**What it taught.** An agent that follows instructions literally would have written "four" and nobody
would have looked. The behaviour I wanted was not obedience to the brief but obedience to the repo, and
the only way to get it is to make "the instruction and the code disagree" an explicit stop condition —
the brief had to ask for the disagreement to be reported, and the child obliged by reporting the brief
itself. I have since stopped writing counts into briefs when the file the count describes is one
`grep` away.

**Evidence.** `grep -A6 '^dependencies' pyproject.toml` prints the five runtime packages ·
`AGENT.md` §9, last bullet · the Stage 10 handoff.

### I10 — the eval cannot see the service failing to find anything

**What happened.** Auditing the new `/ask` endpoint, I curled it with `"what happens when my account gets
locked"`. Retrieval returned **nothing** — `context_chunk_ids: []`. Not a bug: BM25 here is lexical and
unstemmed, that phrasing shares **0 of its 4 tokens** with the corpus vocabulary, and the corpus words
the same fact as "five failed attempts within 60 seconds lock the API key for 15 minutes". So I measured
the golden set against the same question: **0 of its 30 cases retrieve zero candidates.** The eval
therefore exercises ordering among candidates and never exercises the absence of candidates — while 28
of the 30 golden queries already contain out-of-vocabulary terms (about half their tokens on average).

**What it taught.** A metric can be honest and still be measuring the wrong failure. Recall@5 `0.7778`
is a truthful number about a dataset that flatters the retriever's weakest point: queries written with
corpus-ish vocabulary. The fix is not to tune — it is to add the paraphrase dimension to the dataset and
to say, wherever `0.7778` appears, that it is a claim about ranking, not about coverage. Recorded against
Stage 8.5, where a stemming or synonymy change is the honest candidate and must be justified by named
cases, not by the score.

**Evidence.** `grep -n "failed attempts" src/lumen/corpus/auth.md` · the zero-hit probe and the 30-case
vocabulary sweep are reproduced in `ROADMAP.md`, Stage 7.

## What the briefs said

A child receives no chat history — only the brief, the repo, and its own reading. Briefs in this build
had five fixed sections: **READ FIRST** (exact files to read before editing), **DESIGN DECISIONS**
(constraints already settled, so the child does not relitigate them), **VALIDATION** (commands whose
literal output must be pasted back), **HARD CONSTRAINTS** (files it may touch, and what it must refuse),
**REPORT** (the shape of the handoff).

Two clauses that did real work:

> Read `eval/retrieval_eval.py` just enough to see its public function names — use
> `grep -n "^def \|^class "` first and then read only those regions; the file is ~620 lines, do **not**
> read it end to end.

Context budget is the binding resource. Telling a child how to read a big file is worth more than
telling it to be careful.

> If a test fails, the honest outcome is to report the failure. … you may not change the assertion
> values to match what the code does.

Expected values in the tests above (11/14/15 normal passes at k=1/5/10) were measured by the parent
*before* writing the brief, so a child cannot quietly redefine "correct" as "what the code happens to
do".

## What this does not prove

- **The transcripts are not in this repo.** They are large, full of dead ends, and reference paths and
  session ids that mean nothing outside the machine that ran them. The durations, the commits and the
  committed reports are the citable residue.
- **My account of my own mistakes is self-reported.** I1's numbers, and I3, I4 and I7, come from me. What
  is independently checkable is in *Evidence* lines: `git log`, `make check`, the audits.
- **Parent time is not measured.** Seven successful children, two deaths, and a fair amount of repair
  work happened against a human clock I never started.
- **Four commits share one minute** (`17:12`: bootstrap, corpus, chunker, BM25). Commit granularity here
  is logical, not chronological: stages were reviewed and pushed together. Anyone reading the timeline as
  "how long each stage took" will be misled by my batching, not by the work.
- **Nothing here is about the retriever's quality.** The retrieval numbers live in `eval/report.md`, and
  they say the retriever is not good enough yet. This file is about how it was measured.

## Where this is going

`AGENT.md` (Stage 10) is the working agreement these incidents justify — including the single good-vs-bad
agent-reply example, deliberately kept there and not duplicated here. The study guide (Stage 12) turns
them into exercises, one of which is "audit an agent's transcript against `AGENT.md`". If you want the
short version of what an LLM does wrong on a real task, read I1–I4 above, then read the brief that
produced I1, and you will see that the bad outcome was specified by me before the model ran a single
command.
