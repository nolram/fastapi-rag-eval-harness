# `eval/` — the two eval harnesses

`eval/retrieval_eval.py` and everything it touches are deterministic and offline: **no LLM, no
network, no clock, no randomness**. The same checkout always produces byte-identical output, which is
what makes those numbers quotable in the top-level README and safe to gate CI on.

`eval/generation_eval.py` is the one file in this directory that is allowed to open a socket, and it
is the only one with no gate. Everything below about determinism and thresholds describes the
retrieval harness; `## The generation eval` states what changes there.

| file | role |
| --- | --- |
| `golden.jsonl` | 30 hand-labelled cases (18 `normal`, 6 `ambiguous`, 6 `abstention`). Written **before** any ranker tuning, and never edited to chase a metric. |
| `metrics.py` | `recall_at_k`, `precision_at_k`, `reciprocal_rank`, `ndcg_at_k`, `mean_excluding_none`. Abstention-safe: an empty ground truth is `None`, never `0.0`. |
| `retrieval_eval.py` | The CLI. Reads and validates `golden.jsonl`, scores every case with BM25 from `lumen.retrieval`, aggregates per case type, sweeps score thresholds, decides the gate verdicts. Computes; never formats. |
| `report.py` | Renders an `EvalResult` to markdown. Formats; never computes. |
| `report.md` | The rendered retrieval report, written by `--report`. Regenerate, never hand-edit. |
| `generation_eval.py` | The second harness. Answers every golden case through `lumen.generation.answer()`, measures what is deterministic about the answer, and asks a judge two questions about it. **No threshold anywhere in the file.** |
| `generation_report.md` | The rendered generation report, written by `--report`. A measurement of one run against one endpoint: regenerate, never hand-edit, never compare across models as if the model were a variable you forgot to change. |
| `../tests/test_generation_eval.py` | Every HTTP leg of the generation harness is tested through `httpx.MockTransport`, with a network tripwire asserting no socket was opened. |

## Running it

```bash
.venv/bin/python -m eval.retrieval_eval \
  --golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr 0.70 --report eval/report.md
# or, with the defaults (eval/golden.jsonl, k=5, 0.80, 0.70) and no report file:
make eval-retrieval
```

Full run over 30 cases: **~0.15 s**. Options:

| flag | default | meaning |
| --- | --- | --- |
| `--golden PATH` | `eval/golden.jsonl` | JSONL dataset, one case per line. |
| `--k N` | `5` | Cut-off applied **uniformly to every case**, overriding each case's own `k`. Every golden case ships with `k: 5`, so `--k 1` and `--k 10` are how you ask "what does this dataset look like at another depth" without touching the dataset. |
| `--min-recall X` | `0.80` | Gate on `recall@k` over the `normal` bucket. |
| `--min-mrr X` | `0.70` | Gate on MRR over the `normal` bucket. |
| `--report PATH` | *(off)* | Also write the markdown to `PATH`. Without it the report only goes to stdout. |

### Exit codes

| code | meaning |
| --- | --- |
| `0` | Every gate cleared. |
| `1` | A gate failed. **This is what blocks CI.** |
| `2` | The golden file is unreadable, malformed, or inconsistent with the chunker output — or argparse rejected the arguments. The eval never ran; there is no verdict. |

A `FAIL` (exit 1) means the retriever got worse. A `2` means the dataset or the command is broken.
They are different problems and the exit code separates them.

**Under `make`, that distinction moves into the log rather than the status.** `make` exits `2` for any
failed recipe, so `make eval-retrieval` reports `2` both when a gate failed (the CLI said `1`) and when
the golden file is corrupt (the CLI said `2`). CI should gate on *non-zero* and read the printed verdict
to tell the two apart; run the module directly when you want the difference in the status code itself.

## If a gate fails

**Fix the retriever — not the threshold, not the labels, not the report.** `--min-recall` and
`--min-mrr` are a contract fixed in advance, not a dial to turn until the run is green; `golden.jsonl`
was written without ever running the ranker, so a miss is a real miss. The harness is deliberately
hostile to both shortcuts: re-typing a case as `abstention`, inventing a chunk id, emptying an
expectation, or duplicating a case id all fail validation with **exit 2** and a list of *every*
problem found in one pass, before a single metric is computed.

## How to read `report.md`

The report prints in a fixed order: gates → per-type aggregates → abstention analysis → per-case
table (last, so the summary is the first thing on screen).

**Gates.** Two lines, and each line names the bucket it was measured over:

```text
recall@5 over normal (18 cases): 0.8333 >= 0.80 PASS
mrr over normal (18 cases): 0.7269 >= 0.70 PASS
```

The last word is the verdict — `FAIL` on any line makes the CLI exit 1, and that line is the one to
quote when a gate misses.

The gates are **`normal` only** — a decision recorded in `ROADMAP.md` before any number was computed.
Blending the three case types into one mean is what this repo exists to refuse: abstention cases
carry `Recall = None` and `RR = 0.0` by construction, and ambiguous cases have two ground-truth
chunks where a normal case has one.

**Aggregates per case type.** One row per bucket, and `(included/total)` after every mean:
`n/a (0/6)` means *no case in this bucket has a value*, which is not the same claim as `0.0000`.
`mean_excluding_none` refuses to turn `None` into `0.0`, and the excluded count is always printed.

`pass` is **not** one definition across rows:

- `normal` — its one expected chunk is inside the top k.
- `ambiguous` — **both** market chunks are inside the top k. The US/Brazil pairs are deliberate
  near-ties, so retrieving only one half of a contradiction is not a win.
- `abstention` — **no verdict** (`n/a`). Whether such a case *should* abstain depends on a score
  threshold that is not fixed, so it is judged only by the sweep below and never enters a gate.

**The global line.** Printed under the aggregates, labelled **not a gate**, for transparency only.
It blends 18 single-answer cases, 6 two-answer cases and 6 no-answer cases: its recall is computed
over fewer cases than its MRR, and its MRR carries six structural `0.0`s. It is not comparable
between datasets. Read the `normal` row.

**Abstention analysis.** A `top_score` is *not* normalised: BM25 here sums idf-weighted term scores,
so the number grows with query length and term frequency and shrinks as a term appears in more
chunks, over 64 chunks in 12 documents. `reciprocal_rank` is deliberately **not** "at k" — a ground
truth at rank 6 still contributes `1/6` — which is exactly why MRR is printed next to the score
distribution and never alone.

Then the threshold sweep, `t = 0.5 → 4.0` step `0.5`. A threshold abstains when
`top_score < t`; otherwise the retrieved list is truncated to the chunks scoring `>= t`. Both sides
are reported on the same row, because one half of a contradiction is not a win:

| column | the question it answers |
| --- | --- |
| `abstentions covered` | how many of the 6 `X` cases the system now refuses |
| `normal lost` | how many answerable questions it now refuses instead |
| `ambiguous lost` | same, for the market pairs |
| `recall@k normal (truncated)` | recall recomputed **on the truncated list** — what the answer can still see after the cut |

**Why raw-score thresholds are fragile** (measured on this corpus, not asserted): the 6 abstention
cases score **4.3303 – 9.2034**; the 18 answerable `normal` cases score **4.5297 – 20.0727**. The
ranges overlap, so **no** value of `t` separates them: 5 of 6 abstention cases outscore the weakest
normal case, and 10 of 18 normal cases are outscored by the strongest abstention case. Nothing in the
`0.5–4.0` sweep covers even one abstention, and the smallest threshold that would cover all six
(`t > 9.2034`) removes the answer from **10 of 18** normal cases — 55.6% of the questions the system
can currently answer, traded for 10 refusals. Any constant that "works" is a property of *this*
corpus, *this* tokenizer and *this* day: it moves when a document is edited, when the corpus grows,
or when the tokenizer changes. **Measured, not argued:** Stage 8.5 changed the tokenizer and this
finding got *worse*, not better — the same cut went from costing 7 of 18 answerable cases to costing
10 of 18, and the overlap interval moved from `[4.5290, 7.2278]` to `[4.5297, 9.2034]`. A normalised
signal — the margin between the top two scores, or a classifier judged on labels — is the version of
this decision that transfers.

**Per-case results.** `id`, type, verdict, expected and retrieved chunk ids, `top_score`, the four
metrics, an informational column, and the query. `must_include` / `must_not_include` are
**answer-level** constraints — they belong to the generation eval. They are matched
case-insensitively against the concatenation of the retrieved chunk texts and printed only as a
reading aid: a retrieved chunk that happens to contain a `must_not_include` token is **not** a
retrieval failure. They never change `pass` and never move a gate.

## Determinism

`retrieval_eval.py` computes; `report.py` renders. Neither imports a clock, a random source, nor a
network client; floats print at a fixed 4 decimals, buckets and sweep rows iterate in a fixed order,
and abstention scores are sorted by `(score, case id)` so equal scores cannot swap places on a
different run. Two runs are byte-identical, and stdout and `--report` differ only by the file's
trailing newline. `tests/test_retrieval.py` asserts this.

## Stage 6 and Stage 8.5 outcome, as measured

**Stage 6 through Stage 10 the gates failed and CI was red**: `recall@5 = 0.7778` (needs `0.80`) and
`mrr = 0.6713` (needs `0.70`) over `normal`; 14/18 normal cases passed, and four missed their chunk
inside the top 5 — `n-002`, `n-007`, `n-015`, `n-016`. That state was committed, and nothing lowered
the bar. **Stage 8.5 changed the tokenizer** (suffix folding in `lumen.retrieval.tokenize`) and the
gates now pass at `0.8333` / `0.7269` with **15/18** normal cases. One case flipped (`n-002`), none
regressed, and three still miss — `n-007`, `n-015`, `n-016` — while all 6 ambiguous pairs are
retrieved together. That is a BM25 recall problem on paraphrased queries, and it is the input to
Stage 8, not a reason to move a threshold.

---

# The generation eval (`make eval-generation`)

```bash
make eval-generation                     # skipped, exit 0, nothing written — the CI shape
LLM_API_KEY=… make eval-generation       # the real run: 30 cases, two calls each, many minutes
LLM_API_KEY=… .venv/bin/python -m eval.generation_eval --limit 3   # one case per bucket
```

It answers every golden case through the same `lumen.generation.answer()` the service calls, then
measures. Nothing it prints can fail a build.

## What it measures, and with what

**Deterministic, from the golden labels** — no judge, no model, no cost:

- `must_include` coverage: the share of a case's required strings present in the answer,
  case-insensitively. A case listing none yields `None` and leaves the denominator, which is the same
  rule `metrics.py` enforces for retrieval. `(included/total)` travels with every mean so a reader can
  see a denominator shrink.
- `must_not_include` violations: the forbidden strings the answer actually contains, named.
- `abstention match`: `answer.abstained == should_abstain`, per bucket, never blended.
- Citation integrity: `cites_nothing` (answered, cited nothing — looks grounded, is not) and
  `cited_but_not_retrieved` (cited an id the retriever never returned, which is an invention).

**Judged** — one LLM call per answered case, answering two questions:
`supported` (does the context support *this* answer) and `should_have_refused` (should *this question*
have been refused). The rubric is exactly those two plus a `reason`; a reply that is not exactly
`{supported, should_have_refused, reason}` with JSON booleans is a **judge error**: counted, named,
removed from every judge denominator, and never re-counted as a `supported: false` verdict, because
"the judge could not be read" is not a verdict.

## The two things that make the judge numbers readable

- **The judge never sees a golden label.** Its prompt carries the question, the retrieved context and
  the candidate answer. `golden.jsonl` stays the only human signal, so judge and golden are two
  measurements and the calibration section is where they meet.
- **Self-preference bias is the default configuration.** `LLM_JUDGE_MODEL` is empty by default, which
  means the model that wrote the answer grades it, and the report prints that fact in bold rather than
  footnoting it. Set `LLM_JUDGE_MODEL` to a different model and the sentence changes — that variable is
  the only lever that turns the confession into an experiment.

Calibration compares the judge against the human labels in two places only, where the human label
actually says something: the six `should_abstain` cases against `should_have_refused`, and the
`normal` answers that broke one of their own `must_not_include` strings against `supported: false`.
Every disagreement is printed as an id with both readings beside it. **A rate whose id list you have
not read is a number, not a finding.**

## Skip mode is the shape CI sees

No `LLM_API_KEY` → one line naming the missing variable, exit `0`, **no report file written**. Exit `0`
is a decision: the absence of an endpoint is not a failed measurement, and a red check on a machine
that was never given a key trains people to ignore red. CI therefore runs `make eval-generation` on
every push with no secret available — the step is present, and it skips, and that is the assertion.

A golden-file error still exits `1`, and a bad `--k`/`--limit` exits `2`, exactly as the retrieval CLI
behaves. There is no third code for "the numbers were bad", because there are no numbers that are bad.

## What the first live run found, before it found anything about the answers

The first full 30-case run measured **10 of 30 cases**. Twenty answers never arrived, and the harness
reported them as named holes instead of crashing — which is the only reason the cause was findable.

The cause was in the service, not the eval: `lumen.generation.REQUEST_TIMEOUT_SECONDS` was `20.0`. The
endpoint is a reasoning model, and a 10-case uncapped sample of the same prompts measured min 3.8 s,
median 35.2 s, mean 47.4 s, p80 76.0 s, max 134.7 s — one case spent 5515 completion tokens thinking
about a rate limit. A 20-second ceiling truncated 6 of those 10. The constant is now `120.0`, with
that sample written into `src/lumen/generation.py` as its justification, because the ceiling a caller
can plan around is a design decision and 20.0 had never been measured against anything.

Two lessons that generalise past this repo:

- **A failing dependency should degrade a measurement, not end it.** A per-case `try` turned an
  would-be traceback into a table where the shape of the failures — a contiguous id range, all at the
  same elapsed time — named the bug in minutes.
- **Check what your green run measured before you read what it concluded.** An exit `0` with 20 of 30
  cases unanswered is the same trap as a check that passed because it ran nothing.

## What the complete run found (30 of 30 cases answered)

With the ceiling corrected, every case got an answer. Judging was not so clean: in the committed run one
judge reply did not parse, and `n-010` is named as a judge error and excluded from every judge
denominator rather than being recounted as `supported: false`.
Three results are worth carrying forward.

**The two harnesses agree on the same three cases, independently.** The generation eval found two
`normal` answers that abstained when the label says they should not have: `n-007` and `n-015`. Those are
two of the three cases the retrieval gate has been failing since Stage 6 — `n-007`, `n-015`, `n-016` —
and the third, `n-016`, did answer and covered none of its required strings. Nothing in either harness
knows about the other's numbers; they agree because the same root cause runs through both: the right
chunk never reaches the prompt, so the model correctly declines to invent it. **That is the argument for
running the cheap eval and the expensive one at the same time**, and it is the only cross-validation in
this repo that was not designed as cross-validation.

**The one disagreement was two correct answers describing different things — and my first read of it was
wrong.** One `normal` answer (`n-018`) contained its own forbidden string, `at most 3 retries`. The
judge said `supported: true` and quoted the context: `idempotency#3`, third of the five chunks offered
for that query, states *"Use at most 3 retries and reuse the same key for every attempt."* I wrote, in
the commit and below, that this was probably a bad label.

It is not, and the sentence was wrong before I measured anything: I had looked at one case. Auditing all
of them: **10 of the 18 `normal` cases carry a prohibition, and in 4 of them the forbidden string sits in
a chunk retrieval offers for that very query while appearing in none of the expected chunks** — `n-007`,
`n-009`, `n-010`, `n-018`, and every one of them the same shape. A prohibition placed in a *neighbour*
of the expected chunk is not the corpus calling a statement false. It is a guard against the retriever's
own noise: it hands the model five chunks when one is the source, and importing the next document's rule
is exactly the error worth catching. The label and the judge were never in conflict — the label says
"not this question's rule", the judge says "and not false either".

**So the fix went into the tool, not into my prose.** The report now computes that count on every run
and states it — *"4 of the 10 `normal` cases carrying a prohibition have that exact string present in
the offered context and in none of their expected chunks"* — and the calibration check is named
`human says off-scope import vs judge supported=false`. A sentence I wrote once would have been corrected
once; a field the harness recomputes cannot drift back. `tests/test_generation_eval.py` also locks the
four case ids, so a future edit that moves a string out of a neighbouring chunk to make a disagreement
vanish fails a test instead of passing quietly.

The label was still not edited. `eval/golden.jsonl` is read-only for agents by rule, re-labelling to
erase a disagreement is how a golden set becomes a mirror, and now there is a third reason: **the
collision is the detector working**. What my wrong sentence established as a lesson stands anyway —
**a prohibition on a paraphrase-prone corpus can punish a true statement**, which is why the report says
*off scope* and not *wrong*, and why the disagreement list prints the judge's reason next to the human
label instead of collapsing both into a rate.

### The same eval twice is not the same measurement

The committed report is the **second** complete run of the same code against the same endpoint. Run 1's
figures are no longer in this file — run 2 overwrote the artefact that produced them — so they are
quoted from the report as committed: `git show 286890b:eval/generation_report.md`. Both runs used
`temperature: 0` in both legs:

| | run 1 | run 2 |
| --- | --- | --- |
| `normal` must_include coverage | 0.7500 | 0.7222 |
| chunks cited per `normal` answer | 1.1667 | 1.2222 |
| answers the judge supported | 18 of 18 | 15 of 18 |
| judge errors | 0 | 1 (`n-010`) |

What stayed identical is the part worth building on: the two wrong abstentions are `n-007` and `n-015`
in both runs, the one label/judge disagreement is `n-018` in both with the same stated reason, refusal
calibration is 6/6 in both, and every answer error count is 0. What moved is everything that depends on
the model's wording and the judge's reading of it.

That is the strongest argument in this repo against gating judge numbers: **a threshold on `supported`
would have failed between these two runs with no code change and no corpus change.** `temperature: 0`
bounds sampling, not reasoning — a model that thinks before answering can still take a different path
through the same context, and a model that grades prose is reading it fresh every time. Two runs is not
a distribution; it is enough to say the variance is not zero, which is enough to say no gate here has a
legitimate number to sit on yet.

### Technical debt this stage leaves open

- **Repeating this eval is not free, and repeating it is not the same measurement.** Two runs exist, they
  differ as shown above, and the harness cannot see it: nothing compares a report to the previous report,
  so drift is only visible through `git diff` by someone who thinks to look.
- **`must_not_include` measures scope, not truth, and the two are still one field.** `n-018` is resolved
  as a wording problem, not a label problem, but a case that imports a neighbour's rule and a case that
  states something the corpus denies are counted in the same column and would need different fixes.
  Splitting them is a schema change: `contradicts_docs` versus `off_scope_terms`. Only 1 of the 4
  off-scope guards fired in the recorded run, so the other 3 are latent.
- **Calibration denominators are tiny by construction**: six `should_abstain` cases for the refusal
  check, and one violated `must_not_include` for the breach check. The agreement figures describe this
  run; they do not rank anything.
- **The judge is the answering model** unless `LLM_JUDGE_MODEL` is set, so `supported: true` is not
  evidence of correctness. Nothing in this repo has ever run the two-model comparison.
- **`cited_but_not_retrieved` is `0` by construction** — citation extraction can only name ids that were
  offered to it. The metric is a tripwire for a future change, not a present result.
- **No second model, no baseline.** Every figure is one model on one day against one endpoint. There is
  no comparison in the repo that would say whether any of it is good.
