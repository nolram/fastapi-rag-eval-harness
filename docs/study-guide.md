# Study guide — seven exercises on keeping an LLM system honest

This repo is a demonstration of eval discipline, not of volume: 12 fictional documents, 30 hand-labelled
cases, two harnesses, and 21 recorded incidents. The fastest way to read it is to be wrong about it in a
controlled way. Each exercise below is a small experiment with a predicted observation, and every
prediction was produced by running the command on this checkout — not by reasoning about what the code
*should* do. Where a prediction is surprising, that is the lesson, not a mistake in the exercise.

**What you need.** `make install`. No API key, no network, no LLM: all seven exercises run offline. Two of
them read `eval/generation_report.md`, which is a committed record of a live run someone paid for
separately — you read that artefact, you do not reproduce it.

**Before you start, run this and read the output:**

```console
$ make check
$ pytest -q
```

The first prints `143 passed`. The second does not: it ends in
`ModuleNotFoundError: No module named 'lumen'` and `5 errors during collection`. Both are correct.
`make check` runs `.venv/bin/pytest`, which has the package installed; bare `pytest` is a different
interpreter that does not. The red you just produced says nothing about the code and everything about how
you invoked it. Every number in this guide is quoted from the runner that works, and the discipline of
*checking which interpreter printed the result* is itself one of the things this repo learned slowly.

Two files are read-only for everyone, human or agent: `eval/golden.jsonl` (the labels) and
`eval/metrics.py` (the ruler). `AGENT.md` §2 says so and explains why. No exercise here asks you to edit
them, and an exercise that appears to require it has a copy step instead.

One more mechanical fact you will need: **`make eval-retrieval` rewrites `eval/report.md`, which is
tracked.** Run it as often as you like; undo with `git checkout -- eval/report.md`. That is the only
restoration any exercise needs.

---

## 1. Read the corpus and the report side by side

*~15 min. Read-only.*

**Read.** `eval/report.md` — the case table under `## Per-case results` — and `src/lumen/corpus/sla.md`.
Find the row for `n-016`: it is one of the three `FAIL` rows in the gated bucket, with `recall@5` `0.0000`
and `top_score` `6.1028`.

**Do.**

```console
grep -n '"id":"n-016"' eval/golden.jsonl
grep -n "^| n-016 " eval/report.md
grep -n -i "maintenance" src/lumen/corpus/sla.md
```

Then answer in one paragraph, in your own words, why BM25 missed it.

**What to expect.** The case asks, in a customer's words: *"When do you take the API down on purpose, and
how much notice do we get first?"* and expects chunk `sla#4`. The retrieved list is
`versioning#2, sla#5, rate_limits#5, guidelines_brazil#1, guidelines_us#1` — five chunks, none of them
`sla#4`. The grep into the corpus lands on a heading that reads `## Scheduled maintenance windows` and a
sentence containing `Maintenance runs on Tuesdays from 02:00 to 04:00 UTC` and
`announced 72 hours in advance`. Those two strings — `02:00 to 04:00 UTC` and `72 hours` — are exactly
what the case's `must_include` asks the answer to contain. So the retriever missed the one chunk that
holds the answer while the answer's own content is sitting in the corpus, findable, correct.

Now run `grep -c -i "purpose\|notice\|down" src/lumen/corpus/sla.md`. It prints `0`: none of the customer's
three words appears anywhere in that document. The corpus says *scheduled maintenance* and *announced*;
BM25 has nothing else to work with.

One more detail worth noticing, because it is the shape of most real retrieval failures. The retrieved list
*does* contain a chunk from the right document — `sla#5`, latency targets — while the expected one is
`sla#4`, whose title is literally `Service level agreement — Scheduled maintenance windows`. The retriever
found the neighbourhood and missed the room.

**The trap it teaches.** That a retrieval failure is a *vocabulary* failure, not a *knowledge* failure. The
system is not missing the fact. It is unable to walk from the reader's words to the author's words, and no
amount of re-ranking fixes a walk that has no edges. No incident number covers this one — it is the
failure the golden set exists to catch, and `n-016` has sat in the report as evidence since Stage 6 rather
than being tuned or labelled away, which `docs/adr/0002-manual-golden-dataset-before-llm-judge.md` argues
is the valuable state.

---

## 2. Break something on purpose and watch the number go the wrong way

*~10 min. Writes `src/lumen/corpus/*.md` and `eval/report.md`. Restore at the end.*

**Read.** `AGENT.md` §6 (eval discipline) and the `## Gates` block at the top of `eval/report.md`.

**Do.** Note the baseline first:

```console
make eval-retrieval
```

The gate block prints `recall@5 over normal (18 cases): 0.8333 >= 0.80 PASS` and
`mrr over normal (18 cases): 0.7269 >= 0.70 PASS`, and the case table has three `FAIL` rows: `n-007`,
`n-015`, `n-016`. Now rename a domain term across the corpus — the change a product team makes when it
decides a feature should be called something else:

```console
sed -i 's/[Ww]ebhook/callback/g' src/lumen/corpus/*.md
make eval-retrieval
```

Read the new gate block and the new `FAIL` list. Then restore:

```console
git checkout -- src/lumen/corpus/
make eval-retrieval
git status --short          # only your own edits should remain
```

**What to expect.** The gates do not go red. They go **up**: `0.8889` recall and `0.7824` MRR, both
comfortably PASS, and `n-015` leaves the failure list, leaving two `FAIL` rows instead of three. You have
made the corpus factually wrong about the product it documents — `src/lumen/corpus/webhooks.md` is the
documentation of a feature called a webhook, and it no longer uses the word — and both numbers that
measure retrieval quality moved in the direction of *better*.

Open `n-015` to see why. Its query is *"How do I prove that a POST hitting my callback URL really came from
Lumen?"* and it expects `webhooks#1`. Before the edit, the retriever answered it with
`idempotency#1, auth#2, data_retention#1, auth#3, versioning#2` — the customer's word `callback` was in
the query and nowhere in the corpus, so the right chunk had nothing to be found by. After the edit, the
corpus speaks the customer's language and the case passes.

That is the whole lesson in one diff. The metric improved for a reason that has nothing to do with the
system getting better: I paid for a number by moving the docs away from the truth, and nothing in
`make check` or CI objected, because both of them assume the corpus is a premise. Read it again as the
warning it is: **a metric that can rise when the system gets worse is not a guardrail, it is a dial** —
and I turned it by damaging the thing being measured.

**The trap it teaches.** Goodhart, in a form you can reproduce in ten seconds: the pressure a gate creates
is not to fail it but to satisfy it by the cheapest available edit, and the cheapest edit often sits in the
premises rather than the model. `docs/agent-dossier.md` I11 is that pressure arriving from the other
direction — a rule written to protect the gates nearly caused a silent rewrite of them, and the child that
hit it stopped, named all four conflicts, and asked. The repo's defence is not a smarter metric. It is that
`eval/golden.jsonl` is read-only, the corpus has an owner, and the diff above would be reviewed as the
breaking change to documentation that it is.

---

## 3. Change k and watch one metric refuse to move

*~10 min. Read-only — do not pass `--report`, and nothing is written.*

**Read.** The docstring on `reciprocal_rank` in `eval/metrics.py`, the `--k` row of the options table in
`eval/README.md`, and `AGENT.md` §4 on what `--k N` does to a per-case dataset.

**Do.**

```console
for k in 1 5 10; do
  echo "===== k=$k ====="
  .venv/bin/python -m eval.retrieval_eval --golden eval/golden.jsonl --k $k \
    | grep -E "Scoring k|^\| normal"
done
.venv/bin/python -m eval.retrieval_eval --golden eval/golden.jsonl --k 1 --min-recall 0.80 >/dev/null
echo "exit=$?"
```

**What to expect.**

| k | recall@k normal | precision@k normal | mrr | cases passing |
| --- | --- | --- | --- | --- |
| 1 | `0.6667` | `0.6667` | `0.6667` | 12/18 |
| 5 | `0.8333` | `0.1667` | `0.7269` | 15/18 |
| 10 | `0.8333` | `0.0833` | `0.7269` | 15/18 |

Two things are surprising and both are load-bearing.

Doubling the window from 5 to 10 changes **only precision**, which goes from `0.1667` to `0.0833`.
Recall, MRR and the pass count are identical. That is not a rounding artefact: it is a measurement. If the
three missing chunks were ranked 6th, 7th or 8th, widening the window would have caught them. They are not
in the top 10 at all — which is exactly the diagnosis exercise 1 pointed at, now expressed as a number
rather than a paragraph. The failures are far away, not just outside the window.

And MRR is not an *at-k* quantity. It scores a hit at rank 6 as `1/6` no matter what k you asked for, which
is why it can sit perfectly still while the definition of "retrieved" doubles around it. Check that claim
against the source rather than against my sentence: `grep -n -A4 "def reciprocal_rank" eval/metrics.py`.

The last command in the block exits non-zero (`exit=1`). `--k` is not a display knob: the gate compares the
thresholds against whatever bucket is measured at the k you passed, so asking for the top 1 is asking the
gate to judge a different system. Note that `--k` belongs to the CLI, not to `make` — `make eval-retrieval`
passes a fixed `--k 5`, so reaching for a different depth means running the module line from the Quickstart
with a different flag. Which is the same reason you should never put a bare `--k` in a CI line: the line
stops describing the measurement it claims to make.

**The trap it teaches.** Reading one metric as the system. Recall and precision move in opposite directions
with the same knob, MRR ignores the knob entirely, and a "worse" k can make a genuinely different claim.
This is incident I5's shape: the failure is not computing a number, it is quoting a number whose scope you
did not state. I13 states the rule from underneath it — a number must name the command that produced it —
and here that command is incomplete without the k. Two four-decimal figures that differ only because
someone passed a different window are the same incident wearing a decimal point.

---

## 4. Look for the abstention threshold that is not there

*~15 min. Read-only.*

**Read.** `## Abstention analysis` in `eval/report.md` — its subsections are
`### top_score distribution — 6 abstention cases` and
`### Threshold sweep — t from 0.5 to 4.0, step 0.5`, and the paragraph under the sweep is the argument.

**Do.**

```console
sed -n '/^## Abstention analysis/,/^## Per-case results/p' eval/report.md
```

That prints the whole analysis in one block: the score distribution, the sweep table, and the argument
under it. Then try to find, in the sweep table, a `t` that covers at least one abstention without losing a
case.

**What to expect.** You cannot, and the report says so in the table's own rows. For `t` from `0.5` to `3.5`
the sweep covers `0/6` abstentions and loses `0/18` normal cases — the knob does literally nothing. At
`t = 4.0` it still covers `0/6`, and `recall@5 normal` drops to `0.7222`: the first threshold that bites
bites the wrong side. The abstention scores run from `4.3303` to `9.2034`, and covering all six would need
`t > 9.2034`, above the top of the sweep — where the report's own note says the cut destroys 10 of 18
answerable cases (55.6%) and 3 of 6 ambiguous ones.

So the honest statement is a measured one, not a shrug: on this corpus **no raw-score threshold separates
"should abstain" from "can answer"**, because the distributions overlap. The abstention cases are not
low-scoring; they are ordinary queries that happen to have no answer, and BM25 hands back *something* for
every one of them — `cases with top_score is None: 0/6`. A cut that is a single number on that axis either
does nothing or does damage.

Note what the harness did with that finding: it put the sweep in the report, so a reader cannot propose a
threshold without also reading the column of what it costs. The negative result is an artefact, not a gap
in the write-up. I15 is the same principle from the other side: the generation harness printing its own
missing two thirds is what made a provider timeout visible. A harness reporting what it cannot do is
producing information, and the report is where that information belongs.

**The trap it teaches.** Believing an abstention mechanism is "just a threshold away", and shipping the
threshold because it is easy to write.

---

## 5. Why the human and the judge never actually disagreed

*~20 min. Read-only, no key.*

**Read.** `## Calibration: the judge measured against the golden labels` in
`eval/generation_report.md`, the `### Disagreements` subsection under it, plus the corresponding discussion
in `eval/README.md`.

**Do.**

```console
grep -n "n-018" eval/generation_report.md
grep -n "off.scope" eval/generation_report.md
grep -n -i "off.scope\|must_not_include" eval/README.md | head
grep -n "off_scope == {" tests/test_generation_eval.py
```

Then write, in two sentences and without looking at the source, the difference between *"this answer states
something false"* and *"this answer imports a rule that belongs to another question"*.

**What to expect.** One disagreement row, `n-018`, and the calibration line next to it:
`human says off-scope import vs judge supported=false | 0.0000 | 0/1` — a denominator of one. Read the two
columns of that row together: the human label's prohibition fired, the judge answered `supported=True`, and
the judge's own `reason` explains the whole thing in passing — the context handed it the retry rule,
`Retry-After: 30`, and the `at most 3 retries` sentence all at once. Both readings are correct about
different questions. The label is not claiming the sentence is false; it is claiming the sentence is not
about *this* question. The judge was asked whether the answer is supported by the context, and it is.

Then read the count the harness now prints above that table:
`4 of the 10 normal cases carrying a prohibition have that exact string present in the offered context and
in none of their expected chunks`. Not one collision: four, all with the same shape, all of them the
*retriever* importing a neighbouring document, which the generation harness then charged to the model. And
the last command finds the test that locks those four ids — so editing a label to make the disagreement
vanish no longer wins an argument, it fails a test.

The whole resolution cost no model call, no key and no money. It cost one question — *what, exactly, is
being claimed here?* — asked after the finding was already three commits deep and public, and the audit it
triggered checked **all** the cases with a prohibition instead of the one that happened to fire.

**The trap it teaches.** Inferring "the label is wrong" from a single disagreement, and — worse — leaving
two different meanings in one schema field. `must_not_include` currently conflates *contradicts the docs*
with *out of scope for this question*, and `eval/README.md` records the split as open debt rather than
renaming it away. Incident I21, end to end: the wrong conclusion was published, the correction went
forward rather than rewriting history, and the permanent fix went into the tool that recomputes the count
every run instead of into the prose that argued about it.

---

## 6. Audit an agent — including the parent — against `AGENT.md`

*~20 min. Read-only.*

**Read.** All of `AGENT.md` (it is short and it is the contract), then the incident index of
`docs/agent-dossier.md`: run `grep -n '^### I' docs/agent-dossier.md` and read the bodies of I7, I14, I19,
I20, I21.

**Do.** Pick any incident. Find the sentence in `AGENT.md` that, if it had existed at the time, would have
prevented it. Then do the part that matters: **find an incident that no rule in `AGENT.md` prevents**, and
say why the rule is missing.

**What to expect.** Several incidents map cleanly. I14 — a script that exited `0` because the file it ran
was empty — is covered by the rule that a check which ran nothing is not a pass. I7 and I19 — prose that
outlived the state it described — are covered by the rule that every "what it does" claim must come from
having executed the command, and by the README-number checklist.

The candidates for *uncovered* are I20 and I21, and the reason is not an oversight: `AGENT.md` was written
at Stage 10, and both incidents are the **parent's** own, discovered after it was committed. I20 is a
GitHub Actions file that fails to parse because a step name contained `: ` inside a plain scalar — a
workflow that GitHub rejects in zero seconds with zero jobs, a failure `make check` cannot see because it
lives outside the toolchain. I21 is exercise 5 above. If you audit honestly you will notice something
uncomfortable about this repo's own story: the working agreement is a **lagging** document. It encodes
lessons already learned and is silent on the next one, which means a clean audit against it is weak
evidence that the next incident is also covered.

There is a live example you can verify without leaving the file. `AGENT.md` §3 once pasted the output of
its own import-direction grep — three `path:line:` pairs, true the day it was written. Stage 11 added a
test file; the real output became eight lines; the pasted block went on asserting three, and nobody
noticed until an ADR re-ran the command. Read the block now and note what replaced the transcript: two
commands whose output is stable, and a paragraph saying why a transcript was the wrong thing to paste.

**The trap it teaches.** Treating a written agreement as coverage. Rules are written against the last
failure; the value of the document is that it makes the *known* failures checkable, and the honest reading
includes the part it does not reach. This repo's own version is incident I19: a claim about current state
has a half-life equal to the interval between state changes, and `AGENT.md` is a pile of claims about
current state.

---

## 7. Check the README's numbers, then check your check

*~15 min. Read-only.*

**Read.** `README.md`, the `Current numbers` section. `AGENT.md` §7 (Definition of done) states the rule:
every 4-decimal figure in the README must appear verbatim in `eval/report.md`, so that no figure is quoted
from a run nobody can reproduce.

**Do.**

```console
for n in $(grep -oE '[0-9]+\.[0-9]{4}' README.md | sort -u); do
  printf "%s  report.md=%s\n" "$n" "$(grep -c -- "$n" eval/report.md)"
done
grep -n "Docs consistency" .github/workflows/ci.yml
```

**What to expect.** Twelve distinct 4-decimal figures, and exactly one that reports `report.md=0`. Chase
it: `grep -n "10.6220" README.md`. It is not an eval number at all — it is the first seven characters of
`10.622093619866167`, the `score` inside a real `/search` response pasted under `## The API`, and your
regular expression chopped a 17-digit float into a 4-decimal figure that was never a figure. The check
found nothing wrong with the README and something wrong with the check.

Fix the check and re-run it with word boundaries — `grep -oE '\b[0-9]+\.[0-9]{4}\b' README.md` — and the
orphan is gone: eleven figures, every one of them present in `eval/report.md`. That is the whole exercise in
one extra character. A rule stated in prose was turned into a shell loop by an imprecise reader, and the
first result it produced was a bug report about the documentation instead of a bug report about the loop.

Prove the float is honest rather than assuming it. It does not need a server, because the retriever is a
library and deterministic:

```console
.venv/bin/python -c "
from lumen.retrieval import retrieve
r = retrieve('how many requests per minute', k=1)
print(r.chunks[0].id, repr(r.scores[0]), r.scores[0] == 10.622093619866167)
"
```

It prints `rate_limits#3 10.622093619866167 True` — the pasted response reproduces exactly on the current
commit.

Now the second half, which is the part with teeth. `grep -n "Docs consistency" .github/workflows/ci.yml`
returns one line, and that step compares one thing and one thing only: the test count claimed in the
README against what `pytest --collect-only` reports. Everything else in the rule above — the 4-decimal
figures, the gate values, the score bands — is held by convention and by whoever reads the diff. Decide
which of the repo's promises are enforced and which are merely written, because those two categories fail
differently: the enforced one fails loudly in CI, and the written one fails silently, years later, when
someone quotes a number that no longer exists.

**The trap it teaches.** Two, stacked. First, that a mechanical check is a claim about the world and can be
wrong in ways its author cannot see — mine produced a violation out of a truncated float, and I6 is the
dossier's name for that genre: a confident bug report raised by pattern-matching an anomaly, dispelled only
by going and looking. Second, that "we have a rule for that" and "CI enforces that" are different
sentences, and the gap between them is where documentation quietly rots — I19 is that rot, measured.

---

## What each exercise is actually about

| # | The property under test | The one-line failure it demonstrates |
| --- | --- | --- |
| 1 | Retrieval fails on vocabulary, not knowledge | The answer is in the corpus and unreachable |
| 2 | A metric can improve while the system gets worse | I edited the data to please the ruler |
| 3 | Every metric has a scope, and some metrics ignore the knob | `--k` is not a display setting |
| 4 | A negative result is a measured result | No threshold separates abstain from answer |
| 5 | A schema field with two meanings produces phantom disagreements | Off scope is not the same as false |
| 6 | A written agreement is a lagging document | The uncovered incidents are the next ones |
| 7 | Checks are claims too, and unenforced rules rot silently | My regex invented a violation |

If you do only one thing after this guide, run exercise 2. It takes ten minutes, it produces a metric
improvement that is entirely bad, and it is the closest thing this repo has to a proof that the discipline
it argues for is about the failure mode you have not met yet.
