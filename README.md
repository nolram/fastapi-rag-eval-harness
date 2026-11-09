# fastapi-rag-eval-harness

A small retrieval-augmented service for a fictional B2B insurance API ("Lumen") wrapped around the
thing that actually makes RAG defensible: a hand-labelled dataset, a deterministic eval that runs in
under a second with no network, and quality gates that are allowed to fail. The retrieval is BM25
written in plain Python on purpose — the subject of this repo is the **measurement**, not the ranker.
Every number below comes from a real `make eval-retrieval` run on this commit, including the two that
are in **red**.

There are two studies in this repo, and they are about different things. The code studies **how to keep
an LLM system honest** — a hand-labelled dataset, a deterministic judge, gates that are allowed to
fail. [`docs/agent-dossier.md`](docs/agent-dossier.md) studies **what it is like to have LLM agents build
it** — nine delegated runs, two of them timed out, a build left red, and the audit that caught this
README citing a number the report did not contain.

> **30 hand-labelled cases on a 12-document toy corpus are for regression detection, not a quality
> claim.** A Recall@5 of 0.78 here means *"this harness can see a regression"*, not *"the product is
> 78% good"*. Anyone quoting these numbers as product quality is misusing this repo.

## Status: what is built, what is planned

This repo is being built in public, stage by stage; [`ROADMAP.md`](ROADMAP.md) is the running record.

| | |
| --- | --- |
| ✅ Built | corpus · markdown chunker · BM25 retrieval · golden dataset · metrics · **retrieval eval CLI + report** · README + agent dossier · **eval gates as tests** |
| 🔜 Planned | FastAPI `/search` + `/ask` (Stage 7) · CI (Stage 9) · `AGENT.md` (Stage 10) · LLM-as-judge generation eval (Stage 11) · ADRs + study guide (Stage 12) |

The two retrieval gates are now enforced **in the test suite as well as in the CLI**, and they fail.
`make check` is red on `main` deliberately — see [Current numbers](#current-numbers-as-of-2026-11-09).
The plan for making them green honestly is Stage 8.5 in the roadmap: tune the retriever, never the
labels or the thresholds.

There is **no HTTP service yet**. No `curl` examples are provided because every endpoint they would
hit returns 404. When Stage 7 lands, the quickstart below gets an API section — a README that
documents an endpoint that does not exist is the kind of artifact this repo exists to criticise.

## Quickstart

```bash
make install          # uv if available, else python3 -m venv + pip; installs runtime + dev deps
make check            # ruff + mypy --strict + pytest  (70 tests, no network, no LLM)
make eval-retrieval   # scores eval/golden.jsonl, rewrites eval/report.md, gates on the normal bucket
```

**Both targets currently exit non-zero on `main`**, and that is the intended state of this commit: the
retriever misses the Recall@5 and MRR gates, so `make check` fails on two assertions and
`make eval-retrieval` fails on two gates. See [Current numbers](#current-numbers-as-of-2026-11-09).

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

| | Retrieval eval (`make eval-retrieval`) | Generation eval (planned, Stage 11) |
| --- | --- | --- |
| **Question** | Did the right chunk reach the prompt? | Was the answer grounded in what was retrieved? |
| **Cost** | Free — pure Python, no API key | Paid — one LLM call per case |
| **Frequency** | Every commit, in CI, blocking | On demand, and never blocking |
| **Reproducibility** | Byte-identical output, twice a day for a year | Stable-ish at `temperature=0`, still not reproducible |
| **Ground truth** | 30 hand-labelled cases, written **before** any tuning | A model judging a model, calibrated against human labels |
| **Verdict power** | **Gate.** Fails the build. | **Telemetry.** Informs a human; below calibrated agreement it is a number, not a decision. |
| **Runtime** | Sub-second | Minutes, and it can rate-limit |

The asymmetry is the point: the cheap deterministic judge carries the regression risk, so the
probabilistic expensive judge never has to be trusted with a merge. `eval/metrics.py` and
`eval/golden.jsonl` are read-only by convention — see [Discipline](#discipline).

## Current numbers (as of 2026-11-09)

Verbatim from [`eval/report.md`](eval/report.md), produced by the command in the quickstart above
against 12 documents / 64 chunks and 30 cases (18 `normal`, 6 `ambiguous`, 6 `abstention`).

```text
recall@5 over normal (18 cases): 0.7778 >= 0.80 FAIL
mrr over normal (18 cases): 0.6713 >= 0.70 FAIL
```

The same two figures now fail as pytest assertions in `tests/test_retrieval.py`, which is why
`make check` is red: `2 failed, 68 passed`. A third test in that file is the tripwire — green, because
it asserts that the harness **notices** when a corpus document disappears (dropping `webhooks.md` flips
one passing case to failing).

| bucket | cases | pass | Recall@5 | Precision@5 | MRR | nDCG@5 |
| --- | --- | --- | --- | --- | --- | --- |
| **normal (gated)** | 18 | 14/18 | **0.7778** | 0.1556 | **0.6713** | 0.6979 |
| ambiguous | 6 | 6/6 | 1.0000 | 0.4000 | 1.0000 | 1.0000 |
| abstention | 6 | n/a | n/a (0/6) | n/a (0/6) | 0.0000 (6/6) | n/a (0/6) |

Gates are measured over the **`normal` bucket only**. The blended figure over all 30 cases is printed
in the report for transparency and is never a gate — the 6 abstention cases contribute `None` recall
and `0.0` reciprocal rank *by construction*, so averaging across buckets produces a number that moves
when you add cases, not when the retriever changes.

Two findings worth more than the scores:

- **No raw-score threshold separates "not in the corpus" from "here is the answer".** The 6 abstention
  queries score **4.4589–7.2278**; the answerable normal queries score **4.5290–16.5259**. The ranges
  overlap, and the cheapest threshold that would cover all six abstentions removes the answer from
  **7 of 18** normal cases. The sweep recomputes recall on the truncated list precisely so that this
  cost is visible next to the benefit.
- **`k` trades recall for precision, visibly.** Raise `k` and more normal cases pass — but Precision@k
  falls faster than Recall@k climbs, so a report that quotes only recall gets better while the system
  gets noisier. Reproduce it with `--k 1` and `--k 10` against the command above. Deliberately, the
  figures from those runs are **not** quoted here: every metric number in this file appears verbatim in
  `eval/report.md`, which records only the shipped `k=5`.

## How to run the generation eval

**It does not exist yet** (Stage 11). `make eval-generation` is wired to `python -m eval.generation_eval`,
and that module is not in the repo, so the target currently fails with
`No module named eval.generation_eval` rather than doing nothing. When Stage 11 lands it will require
`LLM_API_KEY`; without the key it will exit 0 printing `skipped: no LLM_API_KEY` rather than failing, so
CI never needs a secret and no test ever spends money.

## Discipline

Three rules this repo holds itself to, because they are what makes the numbers mean anything:

1. **`eval/golden.jsonl` was written before any chunker or BM25 tuning existed**, by hand, from the
   corpus documents. The labels are not fitted to the ranker, so a miss is a real miss.
2. **Never edit `golden.jsonl`, `eval/metrics.py`, or a threshold to make a check pass.** If a gate
   fails, the thing to change is the retriever. The two failing gates above were committed failing.
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
  retrieval.py       BM25 (k1=1.5, b=0.75), inverted index, title repetition x2
eval/
  golden.jsonl       30 hand-labelled cases: 18 normal / 6 ambiguous / 6 abstention
  metrics.py         recall@k, precision@k, reciprocal rank, nDCG@k, abstention-safe means
  retrieval_eval.py  the CLI: validate -> score -> aggregate per bucket -> gate
  report.py          renders the markdown; no computation
  report.md          the generated report (committed, so the numbers are reviewable in a diff)
  README.md          how to read the report, and why each column is there
ROADMAP.md           stage-by-stage plan, decisions recorded as they were made
docs/agent-dossier.md  nine agent runs, six incidents, and what each one cost
```

Imports go one way: `eval` imports `lumen`, never the reverse. `mypy --strict` covers `src/`.

## Honest limitations

- **The corpus is fiction, written to be evaluated.** Twelve invented documents about a company that
  does not exist. "Lumen", its limits, its refund policy and its outage window are all made up so that
  nothing here can leak anything real.
- **The retrieval baseline is untuned.** BM25 with stock parameters, a hand-picked stopword list, and
  no stemming. It was not optimised against the golden set, and the failing gates describe it as it is.
- **Ambiguity is synthetic.** The US/Brazil document pairs collide on purpose to give the eval a case
  type most RAG demos skip. Real-world ambiguity is messier than 6 cases.
- **Thresholds were chosen before the first run**, at 0.80 / 0.70, and are a contract rather than an
  empirical optimum. Changing them to fit the observed scores would turn the gate into a description of
  the current retriever.
- **Generation quality is currently unmeasured.** Retrieval without a groundedness eval is half a
  harness; Stage 11 supplies the other half, and until then nothing here says anything about whether
  an answer was supported by its citations.
- **`AGENT.md`, the study guide and the ADRs are still to come** (Stages 10 and 12). They will be
  linked from here when they exist.
