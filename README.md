# fastapi-rag-eval-harness

![CI](https://github.com/nolram/fastapi-rag-eval-harness/actions/workflows/ci.yml/badge.svg)

A small retrieval-augmented service for a fictional B2B insurance API ("Lumen") wrapped around the
thing that actually makes RAG defensible: a hand-labelled dataset, a deterministic eval that runs in
under a second with no network, and quality gates that are allowed to fail. The retrieval is BM25
written in plain Python on purpose — the subject of this repo is the **measurement**, not the ranker.
Every number below comes from a real `make eval-retrieval` run on this commit.

There are two studies in this repo, and they are about different things. The code studies **how to keep
an LLM system honest** — a hand-labelled dataset, a deterministic judge, gates that are allowed to fail.
[`docs/agent-dossier.md`](docs/agent-dossier.md) studies **what it is like to have LLM agents build
it** — nineteen delegated runs, eight of them reaching a 30-minute ceiling, a build left red for five
stages, and the audit
that caught this README citing a number the report did not contain.

> **30 hand-labelled cases on a 12-document toy corpus are for regression detection, not a quality
> claim.** A Recall@5 of 0.83 here means *"this harness can see a regression"*, not *"the product is
> 83% good"*. Anyone quoting these numbers as product quality is misusing this repo.

Three ways in: **the code**, from the Quickstart down; **the measurement**, in
[`eval/README.md`](eval/README.md) and [`docs/adr/`](docs/adr/); **the process**, in
[`AGENT.md`](AGENT.md), [`docs/agent-dossier.md`](docs/agent-dossier.md) and
[`docs/study-guide.md`](docs/study-guide.md) — seven exercises, each with an expected output someone
actually ran.

## Status: what is built, what is planned

This repo is being built in public, stage by stage; [`ROADMAP.md`](ROADMAP.md) is the running record.

| | |
| --- | --- |
| ✅ Built | corpus · markdown chunker · BM25 retrieval · golden dataset · metrics · **retrieval eval CLI + report** · README + agent dossier · **eval gates as tests** · CI · `AGENT.md` · **FastAPI `/search` + `/ask`** · **retriever tuning (Stage 8.5)** · **LLM-as-judge generation eval (Stage 11)** · **ADRs + study guide (Stage 12)** |
| 🔜 Planned | acceptance run + final report (Stage 13) |

CI runs `make check`, `make eval-retrieval`, a README-consistency check and an upload of
`eval/report.md`, all under `if: always()` so one run reports every failing signal instead of stopping at
the first. It contains no secrets and no LLM step, so no CI run can ever spend money.

**The badge was red for five stages, and that was not an accident.** From Stage 6 to Stage 10 both
retrieval gates failed on `main`, and the repo stayed that way on purpose: the numbers in the README
were a target the code had not reached, and nobody lowered the bar to close the gap. Stage 8.5 closed
it from the only direction allowed — **one change to the analyser**, suffix folding in the tokenizer,
with the golden labels, the metrics and both thresholds untouched — and the commit that made CI green
says what it cost. That history is why the gates are worth reading now: a red badge that is never
explained teaches nothing, but a red badge that turns green through a named, single-variable change
teaches the whole method. See Stage 8.5 in [`ROADMAP.md`](ROADMAP.md).

There **is** an HTTP service now: `make serve` runs `GET /health`, `POST /search` and `POST /ask`.
Every response below was pasted from a real `curl` against a real `make serve` on this commit, and the
answerer is in **stub mode** because `LLM_API_KEY` is empty — which is the default, and the reason no
test or CI run can spend money.

## The API

```bash
make serve            # uvicorn lumen.main:app, port 8000
```

```console
$ curl -s localhost:8000/health
{"status":"ok","version":"0.1.0","chunk_count":64,"mode":"stub"}

$ curl -s -X POST localhost:8000/search -H 'content-type: application/json' -d '{"query":"how many requests per minute","k":1}'
{"query":"how many requests per minute","k":1,"hits":[{"chunk_id":"rate_limits#3","doc_id":"rate_limits.md","title":"Rate limits — Handling 429 Too Many Requests","snippet":"`429 too_many_requests` means the key exceeded its per-minute budget. Retry with exponential backoff\nstarting at 2 seconds, doubling per attempt, at most 5 attempts, plus jitter of up to 500 millisec…","score":10.622093619866167}],"top_score":10.622093619866167}

$ curl -s -X POST localhost:8000/ask -H 'content-type: application/json' -d '{"query":"how long is the refund window","k":1}'
{"query":"how long is the refund window","k":1,"answer":"No answer generated: the answerer is disabled because LLM_API_KEY is empty (mode='stub'). Retrieval ran normally and the retrieved chunk ids are listed in this response, but stub mode does not produce answer text. Configure LLM_API_KEY to enable live generation.","citations":[],"context_chunk_ids":["refunds#1"],"abstained":true,"mode":"stub","prompt_version":"v1"}
```

Nothing in those blocks is abbreviated by me. The `…` inside `snippet` is the service's own truncation
at 200 characters (`SNIPPET_MAX_CHARS`), present in the bytes the server sent. Four things the responses
are doing on purpose:

- **`mode` is reported on `/health` and on every answer.** A reader never guesses whether text came
  from a model or from a stub, and the stub never fabricates: it abstains and names the reason.
- **`context_chunk_ids` and `citations` are separate fields.** The first is everything retrieval
  offered; the second is only what the answer actually cited. Collapsing them is how a RAG demo looks
  grounded without being grounded. Stage 11's judge consumes exactly this pair.
- **`prompt_version` travels with the answer.** If answer quality moves, the version says whether the
  wording moved.
- **`k` is validated at the boundary:** `k=0` and `k=99` return `422` rather than being silently
  clamped, and an empty query is rejected too.

**No endpoint can make a paid call by accident.** With no `LLM_API_KEY`, `generation.answer()`
short-circuits before any network access; the live path is one plain `httpx` POST with
`temperature=0`, and the suite proves the key never reaches a `repr()` or an error message. The whole
suite was also re-run with `LLM_API_KEY` set to a fake value: still every test passing, so no test
makes a real request or depends on one.

## Quickstart

```bash
make install          # uv if available, else python3 -m venv + pip; installs runtime + dev deps
make check            # ruff + mypy --strict + pytest  (153 tests, no network, no LLM)
make eval-retrieval   # scores eval/golden.jsonl, rewrites eval/report.md, gates on the normal bucket
make eval-generation  # with no LLM_API_KEY this prints one skip line and exits 0 — CI has no key
```

**Both targets exit `0` on `main` now**, and the way to read that is the history behind it: they exited
non-zero for five stages, because the retriever missed the Recall@5 and MRR gates and nobody lowered the
bar. Stage 8.5 closed the gap by changing the analyser and nothing else. See
[Current numbers](#current-numbers-as-of-2026-11-10).

The same thing without `make`, which is what you want when you care about the exit code:

```bash
.venv/bin/python -m eval.retrieval_eval \
  --golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr 0.70 --report eval/report.md
echo $?   # 0 gates cleared · 1 a gate failed · 2 the golden file is invalid
```

**Why `make` reports `2` where the CLI reports `1`:** `make` exits `2` for any failed recipe, so under
`make` the difference between "a gate failed" and "the dataset is corrupt" moves into the log rather
than the status. CI should gate on *non-zero* and read the verdict.

## The one idea

Two evals, deliberately different, because they answer different questions at different prices. The
retrieval eval exists so that the expensive one is the only thing that ever has to be argued about.

| | Retrieval eval (`make eval-retrieval`) | Generation eval (`make eval-generation`) |
| --- | --- | --- |
| **Question** | Did the right chunk reach the prompt? | Was the answer grounded in what was retrieved? |
| **Cost** | Free — pure Python, no API key | Paid — two LLM calls per case, one to answer and one to judge |
| **Frequency** | Every commit, in CI, blocking | On demand, and never blocking; CI runs the target on every push and it skips, because CI holds no key |
| **Reproducibility** | Byte-identical output, twice a day for a year | Stable-ish at `temperature=0`, still not reproducible |
| **Ground truth** | 30 hand-labelled cases, written **before** any tuning | A model judging a model, calibrated against human labels |
| **Verdict power** | **Gate.** Fails the build. | **Telemetry.** Informs a human; below calibrated agreement it is a number, not a decision. |
| **Runtime** | Sub-second | Tens of minutes over 30 cases |

The asymmetry is the point: the cheap deterministic judge carries the regression risk, so the
probabilistic expensive judge never has to be trusted with a merge. `eval/metrics.py` and
`eval/golden.jsonl` are read-only by convention — see [Discipline](#discipline).

## Current numbers (as of 2026-11-10)

Verbatim from [`eval/report.md`](eval/report.md), produced by the command in the quickstart above
against 12 documents / 64 chunks and 30 cases (18 `normal`, 6 `ambiguous`, 6 `abstention`).

```text
recall@5 over normal (18 cases): 0.8333 >= 0.80 PASS
mrr over normal (18 cases): 0.7269 >= 0.70 PASS
```

Those two figures are also pytest assertions in `tests/test_retrieval.py`, and they were **failing here
for five stages** — Stage 8.5 moved them past the bar by changing the analyser and nothing else (see
[What Stage 8.5 changed](#what-stage-85-changed)). The whole suite is `pytest (153 tests, 0 failures)`;
a third test in that file is the tripwire — green, because it asserts that the harness **notices** when a
corpus document disappears (dropping `webhooks.md` flips one passing case to failing).

| bucket | cases | pass | Recall@5 | Precision@5 | MRR | nDCG@5 |
| --- | --- | --- | --- | --- | --- | --- |
| **normal (gated)** | 18 | 15/18 | **0.8333** | 0.1667 | **0.7269** | 0.7534 |
| ambiguous | 6 | 6/6 | 1.0000 | 0.4000 | 1.0000 | 1.0000 |
| abstention | 6 | n/a | n/a (0/6) | n/a (0/6) | 0.0000 (6/6) | n/a (0/6) |

Gates are measured over the **`normal` bucket only**. The blended figure over all 30 cases is printed
in the report for transparency and is never a gate — the 6 abstention cases contribute `None` recall
and `0.0` reciprocal rank *by construction*, so averaging across buckets produces a number that moves
when you add cases, not when the retriever changes.

Two findings worth more than the scores:

- **No raw-score threshold separates "not in the corpus" from "here is the answer".** The 6 abstention
  queries score **4.3303–9.2034**; the answerable normal queries score **4.5297–20.0727**. The ranges
  overlap, and the cheapest threshold that would cover all six abstentions removes the answer from
  **10 of 18** normal cases. The sweep recomputes recall on the truncated list precisely so that this
  cost is visible next to the benefit. Stage 8.5 made this finding *stronger*, not weaker: before the
  analyser changed, that same cut cost 7 of 18.
- **`k` trades recall for precision, visibly.** Raise `k` and more normal cases pass — but Precision@k
  falls faster than Recall@k climbs, so a report that quotes only recall gets better while the system
  gets noisier. Reproduce it with `--k 1` and `--k 10` against the command above. Deliberately, the
  figures from those runs are **not** quoted here: every metric number in this file appears verbatim in
  `eval/report.md`, which records only the shipped `k=5`.

## What Stage 8.5 changed

One commit, one variable: `tokenize()` in `src/lumen/retrieval.py` now folds regular English
inflections (`locked`/`locking`/`locks` → `lock`, `policies` → `policy`) before BM25 ever sees a term.
Nothing else moved — not the golden labels, not `metrics.py`, not the two thresholds, not `k1`, `b`, the
stopword list, or the chunker. The rules are a pure function of a single token: they never read the
corpus, the query or the golden set, which is what stops them becoming a lookup table for the eval data.

| | before | after |
| --- | --- | --- |
| Recall@5 over `normal` | gate failing | **0.8333** |
| MRR over `normal` | gate failing | **0.7269** |
| nDCG@5 over `normal` | lower | 0.7534 |
| Precision@5 over `normal` | lower | 0.1667 |
| `normal` cases passing | 14/18 | 15/18 |
| `ambiguous` bucket | 1.0000 | 1.0000 (untouched) |
| `"account gets locked"` probe | 0 candidates | 3, led by `auth#4` |

The honest shape of that table: **one case flipped and zero regressed** (`n-002`, whose paraphrase
``blocked`` now meets the corpus's ``lock``). Recall moved by exactly one case in eighteen, because
per-case recall is a fraction. Three normal cases still miss (`n-007`, `n-015`, `n-016`),
each retrieving a confident wrong document, and they are the interesting part of this repo now.

What the change costs, stated rather than buried: it merges some things it should not. `handling` and
`handled` become the term `handl`, which never meets `handle`, because restoring a dropped silent `e`
needs a dictionary this repo does not have. `too_many_requests` folds inside the underscore-joined
identifier. `make`, `making` and `made` stay three separate terms. Each of those is pinned by a test so
the behaviour is a decision, not an accident — the design deliberately prefers **splitting a word family
over merging two unrelated words**.

And the guard against overfitting was procedural, not aspirational: every rule had to be justified by an
English generalisation that could be stated to someone who had never seen the golden set. Rules were
considered and refused because their only merit would have been a named case. `eval/golden.jsonl` is
byte-identical to the file written before any tuning existed.

## How to run the generation eval

```bash
LLM_API_KEY=… make eval-generation                                  # all 30 cases, tens of minutes
LLM_API_KEY=… .venv/bin/python -m eval.generation_eval --limit 3    # a smoke run, one case per bucket
```

It answers every golden case through the same `lumen.generation.answer()` that `/ask` calls, measures
what is measurable without a model (`must_include` coverage, `must_not_include` violations, abstention
match, citation integrity — all per bucket, never blended), and then asks a judge exactly two questions
about each answer: `supported`, and `should_have_refused`. **It carries no threshold of any kind** — no
`--min-*` flag, no PASS/FAIL, and no exit code for an ugly number. What it prints instead is a
calibration section against the human labels, with every disagreement listed by case id, because a
judge's agreement rate is only as good as the disagreements you read.

**With no `LLM_API_KEY` it prints `skipped: no LLM_API_KEY`, writes no file, and exits `0`** — that is
the path CI takes, so the job is present on every push and spends nothing. Two properties worth knowing
before you trust a run: the judge never sees a golden label, and by default `LLM_JUDGE_MODEL` is empty,
which means the model that wrote the answer grades it, a bias the report prints in bold rather than
footnoting. Set `LLM_JUDGE_MODEL` to a second model to make that an experiment instead of a confession.
Everything is in [`eval/README.md`](eval/README.md); the numbers from the last real run are in
[`eval/generation_report.md`](eval/generation_report.md).

## Discipline

Three rules this repo holds itself to, because they are what makes the numbers mean anything:

1. **`eval/golden.jsonl` was written before any chunker or BM25 tuning existed**, by hand, from the
   corpus documents. The labels are not fitted to the ranker, so a miss is a real miss.
2. **Never edit `golden.jsonl`, `eval/metrics.py`, or a threshold to make a check pass.** If a gate
   fails, the thing to change is the retriever. The two gates above spent five stages committed
   *failing*, and when they finally passed it was because the analyser changed — see Stage 8.5.
3. **No paid LLM call from tests or `make check`.** Anything that costs money or touches the network
   is gated behind an explicit key and stays out of CI.

The harness enforces #2 mechanically: an invented chunk id, an emptied expectation, a duplicated case
id, or a drift in the 18/6/6 case split makes the eval exit **2** and list every problem before a
single metric is computed.

## Layout

```text
src/lumen/
  corpus/            12 fictional API documents (the only knowledge the system has)
  chunking.py        markdown -> chunks, heading-aware, ids like `rate_limits#1`
  retrieval.py       BM25 (k1=1.5, b=0.75), suffix-folding tokenizer, inverted index, title rep. x2
  config.py          the only module in the package that reads the environment
  schemas.py         request/response bodies; `k` validated at the boundary
  prompts.py         the answer prompt, versioned (`prompt_version` ships with every answer)
  generation.py      the answerer: stub without `LLM_API_KEY`, one plain POST with it
  main.py            FastAPI app: `GET /health`, `POST /search`, `POST /ask`
eval/
  golden.jsonl       30 hand-labelled cases: 18 normal / 6 ambiguous / 6 abstention
  metrics.py         recall@k, precision@k, reciprocal rank, nDCG@k, abstention-safe means
  retrieval_eval.py  the CLI: validate -> score -> aggregate per bucket -> gate
  report.py          renders the markdown; no computation
  report.md          the generated report (committed, so the numbers are reviewable in a diff)
  generation_eval.py the generation harness: answer every case, measure, judge; gates on nothing
  generation_report.md the last live run, committed as a record of one run at one endpoint
  README.md          how to read the report, and why each column is there
AGENT.md             the working agreement for agents working in this repo
.github/workflows/   CI: make check, make eval-retrieval, a README-consistency check, report upload
ROADMAP.md           stage-by-stage plan, decisions recorded as they were made
docs/adr/            three architecture decision records: why the harnesses are split, why the
                     labels came first, why the evals live in-tree
docs/study-guide.md  seven exercises with executed expected-output, including one that breaks this
                     repo's own metric in ten minutes
docs/agent-dossier.md  nineteen agent runs, twenty-six incidents, and what each one cost
docs/acceptance.md     what was actually run at the end, the first red run kept verbatim, and every
                       divergence from the brief
```

Imports go one way: `eval` imports `lumen`, never the reverse. `mypy --strict` covers `src/`.

## Honest limitations

- **The corpus is fiction, written to be evaluated.** Twelve invented documents about a company that
  does not exist. "Lumen", its limits, its refund policy and its outage window are all made up so that
  nothing here can leak anything real.
- **The retriever is tuned once, and only once.** BM25 still runs with stock `k1`/`b`, a hand-picked
  stopword list and no lemmatisation; the single change made so far is the suffix folding described in
  [What Stage 8.5 changed](#what-stage-85-changed). There is no tuning loop here and no parameter was
  ever moved to chase a gate — which is the reason the passing gates mean something.
- **The golden set still never exercises the zero-candidate path.** Stage 7 measured that a plain
  paraphrase could retrieve nothing at all; Stage 8.5 fixed that particular query (`"what happens when my
  account gets locked"` now returns 3 candidates, led by `auth#4`). But **0 of the 30 golden cases
  retrieve zero candidates, before or after** — so nothing in this repo would notice if a future change
  re-broke it. Recall@5 is a statement about ordering among candidates, not about whether candidates
  exist, and the dataset is the reason that distinction is invisible. 28 of the 30 golden queries carry
  out-of-vocabulary terms, which is why the numbers still move under rephrasing.
- **Ambiguity is synthetic.** The US/Brazil document pairs collide on purpose to give the eval a case
  type most RAG demos skip. Real-world ambiguity is messier than 6 cases.
- **Thresholds were chosen before the first run**, at 0.80 / 0.70, and are a contract rather than an
  empirical optimum. Changing them to fit the observed scores would turn the gate into a description of
  the current retriever.
- **Generation quality is measured and deliberately not gated.** `make eval-generation` says whether an
  answer covered its `must_include` strings, broke a `must_not_include`, cited an id the retriever never
  returned, and whether a judge thought the context supported it — but nothing fails a build on any of
  it, because a threshold chosen before seeing the distribution would be a description of one model on
  one day. Two things cap how far its numbers reach: the judge is the answering model itself unless
  `LLM_JUDGE_MODEL` says otherwise, and the calibration denominators are hand-carved out of 30 cases —
  six labelled `should_abstain`, and only the answers that actually broke a stated prohibition.
- **The ADRs and the study guide exist, and they are not a guarantee either.**
  [`docs/adr/`](docs/adr/) records three decisions with their costs; [`docs/study-guide.md`](docs/study-guide.md)
  is seven exercises whose expected outputs were produced by running the commands. Both are prose, and
  prose about current state has a half-life — the dossier's I19 is this repo's name for that. What is
  enforced is in `.github/workflows/ci.yml`, and it is much less than what is written: only the README's
  test count is compared mechanically. [`AGENT.md`](AGENT.md) exists too, and its own §8 lists what it
  cannot enforce — read that section before treating it as a guarantee.
