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
| 13 | 7 — FastAPI service | `c41389e1` | 28 m 34 s | complete, **1 m 26 s from the timeout** |
| 14 | 8.5 — suffix folding | `ddbd720d` | **30 m 00 s** | **timed out mid-edit; parent finished the work** |
| 15 | 11 — generation eval | `e416c7ba` | **30 m 00 s** | **timed out; module complete (1077 lines, live-tested), its tests unwritten** |
| 16 | 11 — generation tests | `ed2fccac` | **30 m 00 s** | **timed out; 44 tests landed, 6 of the 10 briefed areas unwritten; parent finished** |

**Total delegated time: 5 h 1 m. Of that, 150 m — 50% — went into five runs that timed out.** The single
fastest child in the build (6 m 45 s) did its job on the first try, and it is the one
whose brief named exactly one file it was allowed to write. Stage 8 was written to that same rule — one
file, one marker, expected-red — and cleared in one attempt.

Stages 0–5 each completed in one attempt. The stage that broke the pattern is the one that was
biggest, least specified, and asked for four deliverables at once.

Run 14 changes the diagnosis slightly. It was **not** a big brief: one file, one hypothesis, a
three-file write target. It timed out anyway, and the thing that ate the clock was a **correct
interruption** — it stopped to ask permission to edit four pre-existing tests, waited on the answer, and
then spent its remaining minutes on the mechanical cleanup the change had caused. A brief that is honest
about a shared tree generates round-trips; the round-trips cost wall-clock; the wall-clock is the budget.
The lesson is not "let the child decide". Run 14 was right to ask, and the parent still finished the
work by hand. The lesson is that a stage needing judgement in a shared tree should be budgeted as two
runs, or run by the parent.

Runs 15 and 16 tested that revised lesson and mostly disproved it. Both briefs were small in *files*
(one module; one test file) and both hit the ceiling. What they had in common was **volume inside the
file**: run 15 produced 1077 working lines, run 16 produced 683. Run 16 also had ten numbered coverage
areas in its brief, and delivered four of them — and the six it never reached were the ones I cared
most about (the skip path, the anti-blending proof, calibration, report determinism), because they sit
at the end of a file and the clock ran out where files end. It also left 19 unused imports behind, which
is the fossil record of tests it intended to write and did not.

So the budgeting unit is neither stage size nor file count: it is **roughly a thousand lines of
reviewed output per 30-minute child**. Past that, split by *section* and dispatch twice — and if the
file has a natural priority order, say so in the brief, because a child that dies at the end of the
file dies having written the parts it was told about first, not the parts the parent needed most.

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

**Addendum, after Stage 8.5.** Part of this was acted on and part of it stands, and the split is the
interesting part. The probe now retrieves: suffix folding takes `"account gets locked"` to **`auth#4`,
top of 3 candidates**. What did *not* change is the sentence that mattered most — **0 of the 30 golden
cases retrieve zero candidates, before or after** — so the dataset is still blind to this failure mode,
and the fix I called for (a paraphrase dimension in the golden set) is still unmade. A retriever fix
closed a query; only a dataset fix closes the class.

### I11 — a rule written to protect the gates nearly caused a silent rewrite of them

**What happened.** Stage 8.5 changed the tokenizer, and four pre-existing tests in `tests/test_retrieval.py`
became false statements about the new code: two asserted exact `tokenize()` outputs, one asserted the
measured `--k` pass counts (`11/14/15`), and one — the title-repetition test — had its premise *destroyed*,
because folding made its two fixtures identical. The child's brief said "do not edit anything above the last
test", a rule written to keep a child from softening the two gate assertions. Read literally, the rule made
the stage impossible: the only way to satisfy both the rule and the code was a no-op tokenizer. The child
stopped, named all four, and asked.

**What it taught.** A prohibition that is stated as a boundary rather than a purpose will eventually
conflict with reality, and the failure mode is not rebellion — it is an agent quietly editing the one
thing the rule existed to protect, because that is the only path that satisfies the letter of both
instructions. The grant that unblocked it was narrow on purpose: those four tests only; exact-equality
assertions kept, no subset checks; the `--k` counts re-measured from a pasted run plus a proof the
tripwire is still live (an ignored `--k` would print `15/15/15`, which differs); the gate assertions and
their comments byte-identical, verified with `git diff`. And every diff to a pre-existing test had to be
listed in the report with a one-line justification, so the audit was a diff and not a conversation.

**The part that would have passed review.** Rebuilding the broken fixture, the first candidate I wrote
*passed*. It also passed with the title weight removed — BM25's length preference was deciding the order,
not the thing under test. A test that passes for the wrong reason is invisible to the suite and invisible
to CI; the only reason it surfaced is that the grant demanded a mutation run before the fixture was
trusted. That demand is cheap and it belongs in every brief that touches a test fixture.

### I12 — the parent's own one-line script broke two files

**What happened.** Cleaning up 32 over-long comment lines, I ran a script that reflowed them with
`textwrap`. It re-emitted continuation lines with spaces as padding but dropped the `#` prefix, so every
folded comment became bare indented text in the middle of a module and a test file. Two files stopped
parsing. Nothing had been lost — the damage was whitespace and comment markers — but "nothing had been
lost" is a claim, not a measurement.

**What it taught.** Mechanical edits to text scale a mistake across every file it touches, and they fail
silently because they never intend to change behaviour. Two habits turned it around: *enumerate the
candidates before applying* — I printed all 16 lines the repair would touch and read them, and every one
was a sentence fragment ("it", "of", "and", "stopword."), which is what made the repair safe; and
*re-run the known-good expectations after* — the suite had to come back at the exact counts I had
measured before the script, or the repair was not a repair. After this I stopped scripting formatting in
this repo. A 30-line mechanical fix done by hand is cheaper than one bad script plus its audit.

### I13 — the third time I published arithmetic as if it were a measurement

**What happened.** In one session I wrote "60 m — 32%" of the budget lost to timeouts, and "the child
finished 26 seconds inside the ceiling". The script said 28.4%. And 30:00 − 28:34 is 1 m 26 s, not 26
seconds. Both figures were plausible, both were *mine*, and neither had been computed by anything except
me thinking.

**What it taught.** This repo's whole argument is that a number must name the command that produced it
(I3, I7), and the rule does not stop applying when the author is the one writing the rule — especially
then, because a derived figure feels more trustworthy than a fetched one. The two that slipped were both
*derived*: a percentage and a subtraction. Derived numbers get computed, or they get removed. The README
carries the same lesson in a harder form: because every 4-decimal figure in it must appear verbatim in
`eval/report.md`, the sentence "Recall moved 0.0556" was rejected by the rule and rewritten as one case
in eighteen — the arithmetic became unquotable, so it had to become a fact instead.

### I14 — a check that passed because it ran nothing

**What happened.** To prove the CI steps still pass, I extracted the workflow's embedded docs-check
script into a temp file and ran it. It exited `0`. It had also printed nothing, because my extraction
had written an **empty file** — `write()` was handed a list of lines where it wanted a string, the
exception happened in the writer process, and the runner happily executed an empty script a moment
later. Two commands, one of which was the thing under test, and the success was pure absence.

**What it taught.** A green exit code from an artifact I assembled myself is not evidence until I have
seen the artifact *do something*. The cost of the fix is one line — assert the thing is non-empty, or
tee its real output — and it is the same reason `make eval-retrieval` prints the bucket rows instead of
only exiting nonzero, and why the gates read numbers off the report rather than trusting the caller. The
empty-script failure is the general form of every "the test passed" that means "the test ran". A check
needs a line it can only print when it has actually looked.

**Evidence.** `gh` 404s on `runs/{id}/artifacts` and `--log` with the commit token, so the artifact
comparison this stage wanted was never made; `ROADMAP.md`, Stage 8.5 narrows the cross-machine claim to
what `gh run list` actually supports.

### I15 — the harness reported its own missing two thirds, and that is what found the bug

**What happened.** The first live full run of the generation eval answered **10 of 30 cases** and exited
`0`. Twenty cases carried a `GenerationUnavailable`, the report named every one of them by id, and the
run finished as if nothing unusual had happened. Nothing pointed at the cause. What pointed at the cause
was the shape of the ids: `n-005` through `n-014`, contiguous, with `n-015` and `n-017` fine afterwards.
Content-dependent failures do not cluster by file order. A replay of one of them on its own came back in
`20.1 s` — `lumen.generation.REQUEST_TIMEOUT_SECONDS` was `20.0`, and the endpoint is a reasoning model.

**The measurement that turned it into a decision.** Replaying 10 of the same prompts with the ceiling
removed: min 3.8 s, median 35.2 s, mean 47.4 s, p80 76.0 s, max 134.7 s, 6 of 10 over 20 s. One case
spent 5515 completion tokens reasoning about a rate limit. That is the entire justification now written
above the constant, which moved to `120.0`: not "make the timeout bigger so CI is green", but a ceiling
chosen from a distribution, with the fraction it still truncates stated.

**What it taught.** Two things, and the second is the uncomfortable one. First: a per-case `try` that
degrades a provider failure into a named hole is not politeness — it is the reason the failure was
legible at all, because it preserved the *pattern* of failures instead of replacing it with one
traceback and a dead process. Second: an exit code of `0` with 20 of 30 cases unanswered is I14 wearing
different clothes. A check that runs nothing and a run that measures a third of its cases both produce a
success status, and in both cases the number next to the status is the only thing that tells you. The
report's habit of printing `(measured/total)` after every mean is what made this visible without anyone
reading the per-case table.

### I16 — the judge template died on its first real call, and no test had ever run it

**What happened.** `JUDGE_TEMPLATE` contains an example of the JSON the judge must return:
`{"supported": true, ...}`. The template is rendered with `str.format`, which read those braces as a
replacement field and raised `KeyError: '"supported"'` — on the first live call, in the only code path
that had never been executed, because every test up to that moment had exercised the parser and none had
exercised the *prompt builder*.

**What it taught.** The fix is trivial (`{{...}}`) and is now asserted: a test builds the prompt and
asserts the example survives, and another asserts a braces-bearing answer cannot break it. What is worth
keeping is the shape of the miss. The module had 1077 lines, its parser was heavily tested, and the bug
sat in the one function whose output goes to a machine that charges for a mistake. A mock transport that
is only ever handed *canned replies* tests the code that reads responses and leaves the code that writes
requests untested. Every seam needs both directions exercised, or the untested half is exactly the half
that talks to the world.

### I17 — two more single-file briefs, two more 30-minute ceilings

**What happened.** Runs 15 and 16 (above, in the ledger note): one file each, both hit the ceiling, both
leaving the parent to finish. Run 16 delivered 44 tests and left six of its ten briefed coverage areas
unwritten, plus 19 imports it had planned to use.

**What it taught.** The correction to the rule is in the ledger note rather than here, so this entry
records the cost honestly instead: of the 60 minutes these two children spent, none of it was wasted —
run 15's module ran live and run 16's 44 tests all pass — but the parent still wrote the four tests that
mattered, so the stage's *review* cost was not reduced by delegation at all. The honest framing of those
two hours is "the work got done by a pair", not "the work got done twice".

### I18 — I asserted the report I had briefed, not the report that existed

**What happened.** Writing the four missing tests by hand, I filed four assertions against what the
module *should* say from my own brief, and the module, which had already been running live for an hour,
disagreed three times over: it says `**Nothing in this file is a gate.**` and not the "no threshold is
asserted here" I had written; it counts an unanswered case as a judge error too, with a reason string
saying there is nothing to grade, where I asserted `judge is None`; and my "no absolute path" assertion
failed because *I had passed it an absolute path*, which it faithfully printed.

The fourth was self-inflicted in a different way: I put `block_network()` in a test that also installs an
`httpx.MockTransport`, and the tripwire blocked the mock — it patches `httpx.Client.post`, which is the
method a mocked client dispatches through. The isolation guard and the fake transport are not
stackable, which is not obvious from either file's name.

**What it taught.** The three content misses are one habit: after writing a brief that describes an
artefact, the artefact stops being evidence and the brief becomes it. Each assertion was fixed by reading
what the renderer actually emits, and each of those readings improved the sentence in the report — the
overlap between answer errors and judge errors, for instance, is now stated in the report *because* my
wrong assertion forced me to look at why the ids coincide. And the general form of the mock/tripwire
collision is worth remembering on its own: a guard that blocks the transport layer blocks every
transport, including the one you are pretending to have.

### I19 — "red by design" outlived the green by three commits

**What happened.** `ROADMAP.md` opened with **"`make check` is currently RED by design"**. Stage 8.5 made
it green, was committed, pushed, and re-documented in four other files — and the status line stayed
wrong until Stage 11's doc pass noticed it. A reader arriving from the README badge (green) into the
planning document (deliberately red) would have concluded the repo was lying about one of the two.

**What it taught.** This is I4 with a longer fuse. A claim about *current state* has a half-life equal to
the interval between state changes, and Stage 8.5 was the first state change large enough to expose how
many files carried the old one. The fix is not more diligence — it is the same rule as everywhere else
in this file: prefer the sentence that names the command to the sentence that states the result, so the
reader runs `make check` instead of trusting me.

### I20 — I pushed a workflow that could not be parsed, and the only check that could

**What happened.** Adding one CI step for Stage 11, I named it
`make eval-generation (no key available: must skip cleanly with exit 0)`. A plain YAML scalar
containing `: ` is a mapping, so the workflow file was invalid and Actions refused it: the run reported
`failure` in `0s` with **zero jobs**, which looks nothing like a red test and took a `gh api` call
listing jobs (empty) to identify. `make check` was green on the same commit, because a workflow file is
not a Python file.

**What it taught.** Two things. First, `0s / no jobs` is a distinguishable failure shape and is worth
knowing: it means the file, not the build. Second, and worse, the obvious guard — parse the workflow in
`make check` — needs a YAML library, which is a dependency nobody approved, so I did not add it. What
would have caught it for free is a five-line stdlib scan for a `name:` line carrying a second `: `, plus
an assertion that any commit touching `.github/` shows a CI run *with jobs*. Not built yet; recorded in
ROADMAP as debt, which is the honest alternative to pretending the mistake was unpreventable.

### I21 — I published a conclusion drawn from one case, and the class contradicted it

**What happened.** The generation eval found one case where the human label and the judge disagreed.
I wrote the interpretation into three places — the commit message of `286890b`, `eval/README.md`, and an
open question in `ROADMAP.md` — and it said the label was "probably a bug", "the judge is the more
likely of the two to be right". Next morning the operator asked what, exactly, needed deciding. Answering
that required the audit I had skipped: check **all** ten `normal` cases carrying a prohibition for
whether the forbidden string appears in the context their own query retrieves.

Four of the ten do collide, and all four have the identical shape: the string is in a chunk retrieval
offers and in none of the *expected* chunks. That is not a corpus contradicting a label; it is a guard
placed deliberately against the retriever's noise — five chunks offered, one is the source, and importing
the neighbour's rule is the error worth catching. The label and the judge were never in conflict: one
says "not this question's rule", the other says "and not false either". My sentence was an inference
from n=1, written down in the voice of a class-level finding, and it survived into a public commit
because the one case I looked at was vivid and the audit was not.

**What it cost.** The wrong sentence is in history and stays there — `286890b` is public, and corrections
go forward rather than backward.

**What it taught.** Two things, and the second is the one worth keeping. A conclusion that came from one
case must either say "one case" in the sentence or wait for the count; the audit takes minutes and runs
offline, and it changed the answer completely. And when the fix is *meaning*, put the measurement in the
tool rather than in the prose that explains the tool: the report now recomputes and prints the off-scope
count every run, and a test locks the four colliding case ids so that anyone who later "fixes" a label
to silence a disagreement trips a failure instead of quietly winning an argument. I13 was arithmetic
published as measurement; this is inference published as measurement, same failure, smarter clothes — and
the felt confidence was identical both times, which is the actual reason to audit before writing.

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
- **Parent time is not measured.** Eleven successful children, five deaths, and a fair amount of repair
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
