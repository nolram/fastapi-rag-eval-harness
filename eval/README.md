# `eval/` — the retrieval eval harness

Everything here is deterministic and offline: **no LLM, no network, no clock, no randomness**. The
same checkout always produces byte-identical output, which is what makes the numbers quotable in the
top-level README and safe to gate CI on.

| file | role |
| --- | --- |
| `golden.jsonl` | 30 hand-labelled cases (18 `normal`, 6 `ambiguous`, 6 `abstention`). Written **before** any ranker tuning, and never edited to chase a metric. |
| `metrics.py` | `recall_at_k`, `precision_at_k`, `reciprocal_rank`, `ndcg_at_k`, `mean_excluding_none`. Abstention-safe: an empty ground truth is `None`, never `0.0`. |
| `retrieval_eval.py` | The CLI. Reads and validates `golden.jsonl`, scores every case with BM25 from `lumen.retrieval`, aggregates per case type, sweeps score thresholds, decides the gate verdicts. Computes; never formats. |
| `report.py` | Renders an `EvalResult` to markdown. Formats; never computes. |
| `report.md` | The rendered report, written by `--report`. Regenerate, never hand-edit. |

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
recall@5 over normal (18 cases): 0.7778 >= 0.80 FAIL
mrr over normal (18 cases): 0.6713 >= 0.70 FAIL
```

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
cases score **4.4589 – 7.2278**; the 18 answerable `normal` cases score **4.5290 – 16.5259**. The
ranges overlap, so **no** value of `t` separates them: 5 of 6 abstention cases outscore the weakest
normal case, and 7 of 18 normal cases are outscored by the strongest abstention case. Nothing in the
`0.5–4.0` sweep covers even one abstention, and the smallest threshold that would cover all six
(`t > 7.2278`) removes the answer from **7 of 18** normal cases — 38.9% of the questions the system
can currently answer, traded for 7 refusals. Any constant that "works" is a property of *this*
corpus, *this* tokenizer and *this* day: it moves when a document is edited, when the corpus grows,
or when the tokenizer changes. A normalised signal — the margin between the top two scores, or a
classifier judged on labels — is the version of this decision that transfers.

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

## Stage 6 outcome, as measured

At the time of writing the gates **fail** and CI is red: `recall@5 = 0.7778` (needs `0.80`) and
`mrr = 0.6713` (needs `0.70`) over `normal`; 14/18 normal cases pass. Four normal cases miss their
chunk inside the top 5 — `n-002`, `n-007`, `n-015`, `n-016` — while all 6 ambiguous pairs are
retrieved together. That is a BM25 recall problem on paraphrased queries, and it is the input to
Stage 8, not a reason to move a threshold.
