# 0001 — Split the eval into a deterministic retrieval harness and a live generation harness

## Status

Accepted — 2026-11-10. Describes the build from `8583e55`
(Stage 0, `chore: bootstrap repo skeleton, tooling and make targets`) through `d15906c`
— 31 commits as of that commit (`git rev-list --count d15906c`).

## Context

The system fails in two ways with different physics.

Retrieval either orders the right chunk into the top 5 or it does not. Given the same corpus and the
same query it always produces the same ordering, so a number computed from it is a fact about the
code and can be compared against a threshold fixed in advance.

Generation puts a reasoning model in front of those chunks. It has no ground truth of the same kind:
what it writes depends on a model that re-reads its own context each time, and grading that prose with
another model call adds a second source of the same variance. A number from it is a sample, not a fact.

The two properties that separate them are recorded and checkable:

- `eval/retrieval_eval.py` and `eval/report.py` import no clock, no random source and no network
  client, and a test compares two renders byte for byte — `grep -n "byte_identical_report"
  tests/test_retrieval.py` finds `test_scoring_twice_replays_the_same_cases_and_a_byte_identical_report`.
- `eval/generation_eval.py` is the only module in `eval/` that imports `httpx` —
  `grep -rn "^import httpx" eval/` returns exactly `eval/generation_eval.py:32:import httpx`.

The Stage 0 brief asked for something else: a **gated** LLM-as-judge eval, one rubric of six numeric
fields, and a second human-label file at `eval/calibration/labels.csv`. `ROADMAP.md`'s Stage 11
planned-vs-built table (`grep -n '^## Stage 11' ROADMAP.md`) records what was built instead and why.
This ADR records the reason for the split itself.

## Decision

Two harnesses, two make targets, two artefacts, and opposite contracts about gating.

**Two entry points.** `grep -n 'eval-retrieval\|eval-generation' Makefile` shows both targets
(`Makefile:35`, `Makefile:38`). The retrieval target carries the thresholds and writes the retrieval
artefact: `grep -n 'min-recall' Makefile` returns the single line
`--golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr 0.70 --report eval/report.md`.
The generation target is one bare module call and passes no threshold.

**The retrieval harness gates, on one bucket.** `grep -n "_gates(" eval/retrieval_eval.py` returns two
lines — the `def` at 557 and a single call site, `gates=_gates(buckets[TYPE_NORMAL], k, min_recall,
min_mrr)` — so the only thing the thresholds are ever compared against is the `normal` bucket, never the
blended one. The blended figures are still printed, under a heading that disowns them:
`grep -n "not a gate" eval/report.py` returns
`### Global line — all {n} cases blended, **not a gate**`.

**The generation harness gates on nothing.** `grep -n "min_recall\|min_mrr\|min-score"
eval/generation_eval.py` returns no hits, and
`.venv/bin/python -m eval.generation_eval --help` lists four options and no threshold —
`[-h] [--golden GOLDEN] [--report PATH] [--k K] [--limit N]` — under a description that says
"no flag here can fail the run". Its exit contract is `0` skipped or completed, `1` harness error.
A loose grep for the words `PASS`/`FAIL` in that file does hit five lines, and reading them is the
point: the module docstring, a comment, the `JUDGE_TRANSPORT_FAILURE` label and its one use, and the
rendered report's own disclaimer. None of them is a gate.

**Only one of them may reach the network**, and only when given a key: with `LLM_API_KEY` unset
`main()` writes `SKIPPED_LINE` and returns `0` before constructing a client.

**They share the dataset loader, deliberately, and not each other's numbers.**
`eval/generation_eval.py:35` imports `CASE_TYPES, TYPE_NORMAL, GoldenCase, GoldenFileError, load_golden`
from `eval.retrieval_eval`, so there is one golden-file validator rather than two that can drift apart.
In the other direction `grep -n "generation" eval/retrieval_eval.py` returns one line, a comment saying
`must_include` / `must_not_include` are the generation eval's job. Neither harness reads the other's
artefact: `grep -n "generation_report\|generation_eval" eval/retrieval_eval.py eval/report.py` is empty.

## Consequences

**Accepted.**

- A retrieval regression is caught on every push in well under a second, with no key and no spend.
  Measured on this checkout, running the module directly with no `--report` so nothing was written:
  `time .venv/bin/python -m eval.retrieval_eval --golden eval/golden.jsonl --k 5 --min-recall 0.80
  --min-mrr 0.70` → `real 0m0.054s` on one warm run, with `exit=0` beside it. The hundredths of a second
  are machine-dependent and should not be quoted as a specification; the order of magnitude is the load
  bearing fact, because it is what makes a gate free.
- The two harnesses cross-validate without being told to, which is the strongest evidence in the repo
  that the golden set is measuring something. `grep -n "| n-" eval/report.md | grep FAIL` names
  `n-007`, `n-015`, `n-016` as the retrieval misses in the committed report; `grep -n "n-007\|n-015\|n-016"
  eval/generation_report.md` names `n-007` and `n-015` as the two `normal` cases that abstained when
  `should_abstain` is `no`, and `n-016` as answered but `0.0000` must_include coverage. Nothing in
  either harness can see the other's numbers; they agree because one root cause runs through both — the
  right chunk never reaches the prompt, so the model declines to invent it.
- The expensive harness keeps the output shape that survives its own variance: named case ids and
  denominators (`18/18`, `judge errors 1 (n-010)`) rather than a rate to optimise.

**Borne.**

- On the machine that decides merges, the generation harness measures nothing. CI runs
  `make eval-generation` with no secret available — `git grep -n "secrets\." -- .github` prints
  nothing, by rule — so that step prints one line and exits `0`. Answer quality is unmeasured between
  the runs someone pays for by hand.
- Its numbers cannot be trended, so a real regression in answer quality can hide inside run-to-run
  movement. The committed report is the **second** complete run of unchanged code; run 1 is recoverable
  with `git show 286890b:eval/generation_report.md`. Read side by side (`sed -n '17,22p;34,39p'` of
  each): `normal` must_include coverage `0.7500` → `0.7222`, chunks cited per answer `1.1667` →
  `1.2222`, judge errors `0` → `1`, and answers the judge supported **18 of 18 → 15 of 18**. Same code,
  same endpoint, same `temperature: 0`.
- `eval/generation_report.md` is stale by construction. It is rewritten only when someone runs it, and
  nothing in `make check` or CI reports its age — `grep -n 'name:' .github/workflows/ci.yml` shows an
  upload step for `eval/report.md` and none for the generation artefact.
- Two artefacts means two provenance rules to enforce by hand: `eval/report.md` must be regenerated by
  the CLI, `eval/generation_report.md` must not be compared across models as if the model were a
  variable someone forgot to change.
- The shared golden loader is a shared failure mode with two different exit codes. A malformed
  `eval/golden.jsonl` breaks both harnesses at once, and they report it differently:
  `grep -n -A2 "except GoldenFileError" eval/retrieval_eval.py` ends in `return 2`, while
  `grep -n -A3 "except GoldenFileError" eval/generation_eval.py` ends in `return 1`. Same defect, two
  statuses — one more reason not to write a script that treats the two CLIs' codes alike.
- The retrieval gate's authority covers 18 of 30 cases. The other 12 are printed and never gate, and
  nothing rejects someone quoting the blended line anyway.

## What this looked like without the constraint

Stage 11 as planned was one gated judge eval. Under that design the deterministic and the live
measurement share an exit code, so the run below could only have been read as "the eval went red":
`eval/README.md` ("What the first live run found") records a full 30-case run that measured **10 of
30** cases, because `lumen.generation.REQUEST_TIMEOUT_SECONDS` was `20.0` against a median of 35.2 s —
the constant is `120.0` now (`grep -n "REQUEST_TIMEOUT_SECONDS = " src/lumen/generation.py`), with that
sample written into the file as its justification. With one merged harness, a provider timeout and a
retriever regression produce the same signal.

The same section records the trap in its general form — *an exit `0` with 20 of 30 cases unanswered is
the same failure as a check that passed because it ran nothing* — and `docs/agent-dossier.md` I14 is the
parent's own version of it: a script that exited `0` because the file it ran was empty. A merged harness
that skips half its legs and still prints one verdict is that failure with a build gate attached.

The alternative shortcut was available and was not taken. When both gates failed the repo committed the
red artefact: `git show ca3bd59:eval/report.md | sed -n '/## Gates/,/^```$/p'` prints
`recall@5 over normal (18 cases): 0.7778 >= 0.80 FAIL` and `mrr over normal (18 cases): 0.6713 >= 0.70
FAIL`. And `git log --oneline --date=short --format='%h %ad %s' -- eval/golden.jsonl` returns **one**
commit,
`af28c16 2026-11-09 eval: hand-labelled golden dataset (pre-tuning)` — the labels have never been
touched. A gate that can be relaxed is a description of the retriever you already have.

## Reconsider when

- Repeated live runs give a measured distribution wide enough to put a threshold on. Today two runs
  exist and they differ; `eval/README.md` draws the conclusion — *a threshold on `supported` would have
  failed between these two runs with no code change and no corpus change.*
- A second model or a real judge appears, so that `LLM_JUDGE_MODEL` stops being unset and `supported`
  stops being a model grading its own output.
- The cross-check stops agreeing. If the two harnesses ever disagree about the same case ids, start at
  the shared loader — the `from eval.retrieval_eval import …` line at the top of
  `eval/generation_eval.py` — before blaming either measurement.
- Live generation gets cheap and fast enough to run on every push — then the split stops being about
  cost and becomes only about determinism, which is a better reason to keep it.
