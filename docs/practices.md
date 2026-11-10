# Practices for working with coding agents

Ninth artefact of this build, and the one the build was actually for. Stages 0–13 produced a well-evaluated
toy system and a diary of 26 defects; this file is the diary distilled into things you would do differently
on Monday.

It is written for the person operating the agent, not for the agent. `AGENT.md` is the contract you give the
machine; this is the contract you give yourself.

**Scope, stated before the claims.** Nineteen delegated runs, one model on both sides of every exchange, one
operator, no control arm, one project, no second operator, and no comparison against building the same thing
without agents. That supports rules of thumb. It does not support "agents do not help" — the repo never
measured a build done without them. Where a practice below rests on that thin evidence, the practice says so.

## How to check anything here

Every practice cites a commit, a command and what it prints, or a ledger row. Check them:

```bash
git log --oneline -- eval/golden.jsonl                          # practice 6
grep -n "def test_the_harness_notices" tests/test_retrieval.py  # practice 7
grep -n "^| 1[4-9] " docs/agent-dossier.md                      # practices 2, 3
grep -n "I25\|I23\|I24\|I26" docs/agent-dossier.md              # the incident index
```

A practice with no resolvable pointer was not supposed to get into this file. If you find one, it is a bug in
the file and the bug is the point of the entry criterion.

---

## 1. Write the gate that can detect the objective failing

**The rule.** Before the first stage, write one acceptance criterion that can fail while every build gate
passes. If all your gates are about the build, you will get a perfect build of the wrong thing.

**When it applies.** Always. Most strongly when the objective is a capability ("learn X", "understand Y")
rather than an artefact ("ship Z").

**The evidence.** This ROADMAP has thirteen stages and every gate in it is per-stage: *`make check` green*,
*gates FAIL by design*, *no number without provenance*. Not one gate asked *"is this teaching what it was for?"*
Thirteen stages passed all of them. `grep -n "check.*green" ROADMAP.md` will show you gates that were
satisfiable by a project nobody needed.

**The counter-example, in the same repo, done right.** Stage 4's gate could have failed for reasons unrelated
to code: *`golden.jsonl` written before any chunker or BM25 tuning, verified by commit order, not prose.* That
is an objective-shaped gate. It is why the labels are trustworthy, and `git log --oneline -- eval/golden.jsonl`
returns one line as its proof.

**What obeying costs.** You have to define success before you can flatter yourself, which is uncomfortable and
happens once. Write it as a question a stranger could answer, not as a feeling.

---

## 2. Delegate inside the orientation window, not the difficulty window

**The rule.** What kills a delegated run is not how hard the task is; it is how much of the repo the agent has
to read before it can write a line. Budget by *files to read*, not by *difficulty*.

**When it applies.** Every delegation. It gets more true as the repo grows and the validation loop gets wider.

**The evidence.** All 19 runs measured, computed from the ledger by a command now pasted in `ROADMAP.md`:

```console
runs 1-6 (Stages 0-5)          runs=6 avg=13.6m ceilings=0
runs 7-13 (Stages 6-10)        runs=7 avg=18.6m ceilings=2
runs 14-19 (Stages 8.5-13)     runs=6 avg=30.0m ceilings=6
```

Six of six completed before Stage 6 closed; **zero of six** after Stage 8.5. And the last six had the *best*
briefs of the series — one write target, pre-set decisions, priority order inside the file, a validation block.
Brief quality stopped being the lever around Stage 8.

The pair that makes it visible: **run 9 finished a whole test file in 6 m 45 s**; **run 13 needed 28 m 34 s and
missed the ceiling by 86 seconds** — for a FastAPI service over an API that was already documented. Same model,
same harness, same brief discipline. The difference was how much had to be read.

**The counter-example.** Run 17 (three ADRs, ~274 lines of prose) died at 30 minutes *having written all three
files* and produced no handoff. Run 18 (a 452-line study guide) died having written nothing. Both briefs were
structurally identical. The ADR child banked 452 lines because three small files force interleaved
write-verify; the guide child spent the whole budget reading one big target before writing it. File count is
not a proxy for effort; it is a proxy for *how early the first verified artefact exists*.

**What obeying costs.** You write more, smaller briefs, and you review more times. And you must sometimes
decide not to delegate at all — which feels like failing the exercise.

---

## 3. A ceiling is a scheduling constraint; a death sentence needs a measurement

**The rule.** Put the time budget in the brief as a fact, and instruct the agent to bank finished work early.
And never record a run as failed without checking whether it produced something.

**When it applies.** Every run, and especially the recovery after one.

**The evidence.** `grep -n "timed out\|teardown" docs/agent-dossier.md` finds eight runs that ended at exactly
30:00 — 240 minutes of the 391.6 the ledger prints, just under two thirds of every delegated minute. Two of
those eight delivered complete work and died *afterwards*: run 17 with 452 lines on disk, run 19 with 144 lines
and 10 passing tests. The budget was not the problem; the budget plus an unread target was.

**The counter-example, which is mine and it is the best one here.** Incident I25. I wrote — to the user, and to
`ROADMAP.md` — that run 19's child "died with zero deliverables, third consecutive loss". Then I checked:

```console
$ stat -c '%y' /home/coder/.pi/agent/sessions/.../stage13-ci-guard.md
2026-07-30 14:37:52
```

The file was written at 14:37:52. The terminal state of the run was recorded at 14:42:59. The child delivered
five minutes *before* it was declared dead, and the file that survived on disk was **its** version, not mine. I
had read the transcript for style, not for artefacts. And I did not run `git status --short`, which is the
recovery rule this repo already had (I15) and which I had written myself.

**What obeying costs.** Every death now costs a command before it costs a narrative. Which is the whole point:
the narrative was free and wrong.

---

## 4. Verify before you narrate — including your own process state

**The rule.** Any sentence about what you, the agent, or the repo is doing needs the same evidence bar as a
sentence about the code. Claims about process feel safe because nobody checks. They are the least checked and
the most consequential, because they decide what you do next.

**When it applies.** To every status report you write, including the ones inside your own head.

**The evidence.** I found four false claims in this repository's narration *while writing this file*:

1. I had stated, in an earlier report, that Stage 8 created `tests/test_eval_gates.py` and that CI ran it as a
   separate step. **That file has never existed** — `git log --all -- tests/test_eval_gates.py` is empty. The
   Stage 8 gates landed in `tests/test_retrieval.py` (`git show --stat 60374ef`: one file, 88 insertions).
2. I had quoted the first red run's MRR as `0.6713`. `git show ca3bd59:eval/report.md` prints
   `mrr over normal (18 cases): 0.6729 >= 0.70 FAIL`. I was restating, not re-reading.
3. I had described the generation test suite as guarding a `socket` counter. `grep -rn "_guard_socket" tests/`
   is empty; the actual mechanism patches `httpx.post` and `httpx.Client.post`.
4. Worst: twice during this build I described **eval runs that never happened** — a `--limit 0` run and a
   `--limit 2` run of the retrieval harness, with report banners and exit codes. `--limit` is not a flag of
   `retrieval_eval` at all (`--help` lists five options, and `grep -rn "limit" eval/retrieval_eval.py` finds
   only a local variable named `limit` in the gate renderer); and the banner text I quoted, *no cases to
evaluate*, occurs nowhere in the code — `grep -rn --include='*.py' "no cases to evaluate" eval/ src/ tests/`
   prints nothing; the only occurrence in the repository is this sentence. I narrated
   tool output. `eval/report.md` is byte-identical to the accepted commit (`md5sum` prints
   `593d2b1cb5cdf705e2c8432e0f786e3a`) and `git status` is clean, so nothing false reached the repository — but
   the claim reached *you*, and it is the mechanism the practice above exists to stop.

None of these broke the build. All four would have made the next decision worse — and the fourth is the one
that should worry anyone reading this file, because it came from a session whose entire subject was provenance.

**The counter-example, done right.** Incident I24: a brief told the child to grep `AGENT.md` §3 and report
results. It reported that my own documented command output did not reproduce, and its handoff cited the
README lines that contradicted it. That is how a stale transcript survived two green CI runs and got caught —
one line of the brief, turned into a check the child could run against my work.

**What obeying costs.** You will discover that a large fraction of what you thought you knew about your own
session is confabulation, and you will have to say so out loud more often than is comfortable.

---

## 5. A digit in prose needs a mechanism, or it needs to be a shape

**The rule.** Every number in prose is either (a) produced by a command in the same document, (b) held by a
test that fails when the value drifts, or (c) expressed as a shape plus a pointer to the artefact that owns it.
"Approximately the right number, as of when I wrote it" is the third thing people mistake for the first.

**When it applies.** Any document that outlives the session that wrote it.

**The evidence.** CI holds exactly one number: the README's test count, against `pytest --collect-only -q`
(`.github/workflows/ci.yml`, the step named *Docs consistency*). That is why three count-drifts during Stage
13 were caught at the only place they were held, and why everything else rotted silently: the study guide
asserted `143 passed` in an exercise header and `5 errors` in a transcript, both correct the day they were
written and both falsified by the very next stage.

The failure mode is not "the number was wrong once". It is that **a hardcoded count in a document about a test
suite is a statement with a maturity date.**

**The counter-example, and the hole behind it.** `AGENT.md` §7 asks every 4-decimal figure in the README to
appear verbatim in `eval/report.md`. The sweep is exercise 7 of `docs/study-guide.md`; run it **raw**, and it
reports one orphan:

```console
$ for n in $(grep -oE '[0-9]+\.[0-9]{4}' README.md | sort -u); do
    printf "%s  report.md=%s\n" "$n" "$(grep -c -- "$n" eval/report.md)"; done | grep '=0$'
10.6220  report.md=0
```

The guide already explains that this is not a README defect: `10.6220` is the first seven characters of
`10.622093619866167`, a `score` inside a real `/search` response pasted under *The API*, and the regex chopped a
17-digit float into a figure that was never a figure. Fix the check and it is clean — verified on this checkout:
11 figures, every one of them present in `eval/report.md`.

What the guide does not say, and what running it twice shows, is that the fix works by **narrowing the
population rather than validating the number.** `grep -c "10.62209" eval/report.md` prints `0`, and always
will: that figure is a service response, not an eval output, so it is outside §7's intent and inside its
regex. §7 governs numbers quoted *from the eval*, and neither the rule nor the loop says which population it
actually scans. So "every figure verified" really means "every figure the regex recognised and the eval could
have produced" — and a green sweep is silent about the class it declines to look at. Add to that that **CI runs
neither form of the sweep**: the mechanism's whole life is one command and a reader who asks what it cannot see.

That is the same shape as exercise 2's corpus rename — a metric that improved because the measurement stopped
covering the hard case — which is why the practice is not "add a check" but "state the population a check
scans, next to the check". **Found while writing this file, and closed in the rule while I was at it:**
`AGENT.md` §7 now names its population and says explicitly that a service-captured score is governed by the
next bullet, so the raw sweep's orphan is documented as expected rather than rediscovered by the next reader.

**What obeying costs.** You write more commands in documents and fewer confident sentences, and sometimes a
command tells you the rule you wrote has a scope you never stated. Readers find that less fluent. It is the
right trade, because fluent and false is the failure this whole file exists to avoid.

---

## 6. Freeze the ground truth; tune the system

**The rule.** Fix the labels first, then change only the system, one variable at a time. If you can edit the
answer key, you have not built a measurement; you have built a mirror.

**When it applies.** Any time you are tempted to "adjust the dataset". Which is always.

**The evidence.** `git log --oneline -- eval/golden.jsonl` prints exactly one line:

```console
af28c16 eval: hand-labelled golden dataset (pre-tuning)
```

Written before the chunker and BM25 existed, and never touched across ten stages — including four golden cases
that disagreed with the label schema and were *not* changed. Meanwhile `git log --oneline -- src/lumen/retrieval.py`
prints two lines (`b34f5f1` then `97867a2`), and the second one — a suffix folding rule with a stated English
generalisation and one pre-registered cut — moved recall@5 from `0.7778` to `0.8333` and turned the first
`make check` in the project's history green. **One variable, and the harness could name the gain.**

**The counter-example, and the version that still tempts people.** The corpus-rename experiment in the study
guide: rename `webhook` → `callback` across the corpus and *both gates go up* —

```text
recall@5 0.8333 -> 0.8889        mrr 0.7269 -> 0.7824
```

The docs got factually wrong and the numbers improved. Anyone tuning thresholds or corpus wording without
frozen labels can produce that graph, and cannot tell it from progress. That is why I21's `must_not_include`
conflation is recorded as a schema problem to fix rather than quietly tuned away.

**What obeying costs.** You keep the red run. The first run of this harness failed both gates, and its report is
still readable at `git show ca3bd59:eval/report.md`, five stages of red preserved instead of edited away. That
feels like a defect in the repository until the day it is the only reason anyone believes the green one.

---

## 7. Scope every gate to what it can actually judge

**The rule.** A gate must sit on a number that measures the thing you care about, over the population where
that number is defined. Blending is how you get a green light that means nothing.

**When it applies.** Every threshold you write, and especially the tempting global one.

**The evidence.** The committed report prints both, side by side:

```text
| normal (gated) | 18 | 15/18 | 0.8333 | ... | 0.7269 |   <- the gate sits here
recall@5 0.8750 (24/30) · mrr 0.6361 (30/30)              <- the Global line, never the gate
```

A lazy gate on the blended number would read **0.8750** — higher, prettier, and meaningless: its recall is
computed over 24 cases while its MRR is computed over 30, and those 30 carry six structural zeros from
abstention cases where there is nothing to find. The scoping is not cosmetic — it is load-bearing, and you can
watch it fail:

```console
$ .venv/bin/python -m eval.retrieval_eval --golden eval/golden.jsonl --k 5 --min-recall 0.85 --min-mrr 0.70
recall@5 over normal (18 cases): 0.8333 >= 0.85 FAIL
mrr over normal (18 cases): 0.7269 >= 0.70 PASS
$ echo $?
1
```

**A threshold the blended figure clears (0.8750 ≥ 0.85) fails the bucket that is actually gated.** Same code,
same corpus, same golden file: the only difference is which population the number is allowed to describe. That
is the entire content of the practice, printed by one command.

**The same rule, applied to the thing you cannot gate.** The generation harness has **no `--min-*` flags by
design**, and the file states why. Two runs against the same endpoint, same `temperature: 0`, same 30 cases:

```console
$ git show 286890b:eval/generation_report.md   # run 1
$ cat eval/generation_report.md                # run 2
```

Answers the judge called supported: **18 of 18, then 15 of 18.** `normal` must-include coverage: 0.7500 then
0.7222. Everything deterministic moved **zero**. A gate on `supported` would have failed between those two runs
with no code change — `temperature: 0` bounds sampling, not reasoning.

**And the same rule inverted, which is the rarest form.** Stage 8's tripwire
(`grep -n "def test_the_harness_notices" tests/test_retrieval.py`) resolves as
`test_the_harness_notices_when_a_critical_corpus_document_is_dropped`:
it deletes a corpus document, expects metrics to *worsen*, and **fails if the harness notices nothing**.
Passing means the measurement is alive. Almost nobody writes a gate whose red condition is "my tool got
quieter", and that is the only kind that protects you from your own instrumentation.

**What obeying costs.** You ship fewer green checks and more honest ones, and you must explain, in prose, why
the prettier number is not the gate. That explanation is `--min-recall` printed with the bucket name in the
same line so nobody can read it as a global claim.

---

## 8. Put a verification grep in the brief, and the child audits your work

**The rule.** Give the agent one command that tests *your* assumptions, not only its output. The cheapest
quality control in the whole build was a sentence.

**When it applies.** Every brief, whenever the brief contains a claim about the current state of the repo.

**The evidence.** Incident I24: `AGENT.md` §3 told agents to verify with a grep whose *transcript* was baked in.
A child handed to that child ran the command, found my recorded output did not reproduce, and put it in its
handoff along with the README lines that contradicted it. Both were defects that survived two green CI runs.
The fix (`78cb311`) replaced the transcript with the command and its current output.

The pattern generalises past agents. In Stage 12 I asked the parent (me) to run every exercise in the study
guide before publishing its "expected output". Nine commands; four incident citations were wrong. If the guide
had gone out unread — the normal outcome — the repo would have shipped a learning artefact teaching four false
histories.

**The counter-example I did not commit to.** `docs/practices.md` has a gate: every commit hash it cites must
resolve. The one-liner lives in the file's own footer, so a reader runs it and not me. It exists because
practice 8 says my prose about my own repo is the least verified thing in it.

**What obeying costs.** Some briefs get longer by one line, and sometimes that line tells you your brief is
wrong before it executes.

---

## 9. Spend the review budget where the prose is

**The rule.** You have a fixed amount of attention per session. Code and metrics get it automatically because
they fail loudly. Documentation fails silently and fluently, so it needs to be assigned budget deliberately.

**When it applies.** Every stage that writes something meant to be read.

**The evidence.** Review of code was instrumented: ruff, `mypy --strict` over `src/` (`make typecheck` prints
how many files it checked), a test suite whose current size `pytest --collect-only -q` reports — CI compares
the README's claim against exactly that number — two retrieval gates, a workflow guard, and a docs-consistency
check. Review of prose was not instrumented, and what happened is
on the record — I23: four fluent wrong incident citations in 452 lines I had just written and read; the README
still claiming *"both targets currently exit non-zero"* after eight stages had made one green, still claiming
*"2 failed, 86 passed"* three suites ago, and still claiming the study guide and ADRs were "still to come"
after both existed.

The cheapest mechanism that worked: `make check` and the CI docs step hold the test count; the README's
4-decimal figures hold to `eval/report.md` — checked by hand, so the
unmeasured surface keeps being the prose.

**A cheap mechanism worth copying.** The type-checking boundary is a known, stated gap: `pyproject.toml` has
`files = ["src"]`, so `make typecheck` checks 8 files and `eval/` — which produces every citable number in this
repo — is linted and tested but never type-checked. `mypy eval/` is clean over 5 files now. The gap is small
because it was *named*; the prose gaps stayed large because nobody had said "this is a surface".

**What obeying costs.** You read your own words again, in full, once, with the intent of finding a lie. It is
the least pleasant thirty minutes in the build, and it is where the four wrong citations were.

---

## What this set is not worth

Three limits, stated rather than implied:

- **No control arm.** Every number here describes *this* build. Nothing establishes that agents beat not
  delegating, or that a different model, a shorter repo, or a second operator would land elsewhere in the
  tables. Stage 15 of the ROADMAP exists to fix exactly this and has not run.
- **Survivorship in the sample.** The 19 runs are one strategy by one operator under a fixed 30-minute ceiling
  that was never calibrated against work of this size. Just under two thirds of every delegated minute was
  spent inside a ceiling (240 m of the 6 h 31 m in the ledger), which is partly a fact about agent capability
  and partly a fact about a badly chosen budget.
- **Selection in the incidents.** 26 incidents were recorded because they were interesting. Practices 2–9 all
  come from defects, which biases the set toward catastrophe and away from the boring 200 runs where delegation
  was uneventful. Absence of an incident here is not evidence of a practice working.

## The one practice I would keep if I deleted the rest

> Every derived number states the command that computes it; every "what it does" claim in a document comes from
> having executed it.

It is the parent of the other eight, it costs one command per claim, and it is the only rule in this file that
would have caught the false statements I made about this project in writing — including the four I found
while writing it.

---

**Gate for this file.** Every commit hash cited above must resolve:

```console
$ grep -oE '\b[0-9a-f]{7,8}\b' docs/practices.md | sort -u | while read -r h; do
    git cat-file -e "$h^{commit}" 2>/dev/null || echo "UNRESOLVED: $h"; done
```

Prints nothing when the file is honest.
