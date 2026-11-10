# Acceptance report

Everything below was executed, not asserted. Where a figure is quoted, the command that prints it is
quoted beside it, and most of them were last run in a **fresh clone of the published default branch in a
throwaway directory** (`/tmp/acc/repo`), with a venv built by `make install` — not in the working
directory the build happened in.

- Repository: **<https://github.com/nolram/fastapi-rag-eval-harness>** — public, single branch `main`, no
  force pushes, no other user or organisation involved.
- Acceptance commit for the measurement section: `4cf0c13`. The Stage 13 guard (`tests/test_ci_workflow.py`)
  and this file land after it; the suite count moves from 143 to 153 with that commit, and every count here
  says which side of it it was taken.
- No API key, token or credential appears in any file, commit, log or CI artifact. The only key used during
  the build lived in the operator's `~/.pi/agent/models.json` and in a shell environment variable; the two
  live generation runs were performed by the parent, and no subagent and no CI step ever received one.

## 1. The measurement, as it stands

`make eval-retrieval` on the fresh clone, literal output, exit code `0`:

````text
.venv/bin/python -m eval.retrieval_eval --golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr 0.70 --report eval/report.md
# Lumen retrieval eval

- Corpus: 12 documents / 64 chunks (`src/lumen/corpus`, loaded by `lumen.chunking.load_corpus`).
- Golden file: `eval/golden.jsonl` — 30 cases (18 `normal`, 6 `ambiguous`, 6 `abstention`).
- Command: `python -m eval.retrieval_eval --golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr 0.70`
- Scoring k: 5, applied uniformly to every case.

## Gates

```text
recall@5 over normal (18 cases): 0.8333 >= 0.80 PASS
mrr over normal (18 cases): 0.7269 >= 0.70 PASS
```

## Aggregates per case type

| bucket | cases | pass | recall@5 | precision@5 | mrr | nDCG@5 |
| --- | --- | --- | --- | --- | --- | --- |
| normal (gated) | 18 | 15/18 | 0.8333 (18/18) | 0.1667 (18/18) | 0.7269 (18/18) | 0.7534 (18/18) |
| ambiguous | 6 | 6/6 | 1.0000 (6/6) | 0.4000 (6/6) | 1.0000 (6/6) | 1.0000 (6/6) |
| abstention | 6 | n/a | n/a (0/6) | n/a (0/6) | 0.0000 (6/6) | n/a (0/6) |
````

The two gate lines are scoped to the `normal` bucket and say so in their own text. The blended
all-30 number is printed below this in the report and is labelled *not a gate*; blending is the move this
repo exists to refuse, because the abstention cases contribute `None` recall and structural `0.0`
reciprocal rank, and the ambiguous cases have two ground truths where a normal case has one.

Three `normal` cases still fail per-case and stay failing on purpose: **n-007, n-015, n-016**. They are
vocabulary and paraphrase failures, diagnosed in `docs/study-guide.md` exercises 1–2, and they are the
reason the `normal` row says 15/18 rather than 18/18.

`make check` on the same fresh clone: ruff clean, `Success: no issues found in 8 source files`, suite
green with one pre-existing `StarletteDeprecationWarning`. With `LLM_API_KEY` set to a fake value the
result is unchanged, which is the mechanical form of "no test spends money".

## 2. Determinism, re-proved rather than remembered

The claim in `AGENT.md` is that the report is byte-reproducible, and the version of that claim standing
since Stage 8 was proven on the machine that wrote the code (incident I8 says why that is worth little).
Re-measured on the published clone:

```console
$ git clone https://github.com/nolram/fastapi-rag-eval-harness /tmp/acc/repo
$ cd /tmp/acc/repo && make install && make eval-retrieval
$ git status --short eval/report.md      # empty: regenerated file differs from the committed one by nothing
$ md5sum eval/report.md
593d2b1cb5cdf705e2c8432e0f786e3a  eval/report.md
```

Same digest as the working directory's committed file. Different directory, different venv, zero shared
state with the build, and the same 4-decimal figures — including on the `abstention` row, where every
`top_score` is identical to the committed report.

## 3. The first run, and what it refused

Retained verbatim, because a repo that shows only its green state is showing the wrong thing. Recovered
with `git show ca3bd59:eval/report.md` — the report from Stage 6, the first time the golden set was ever
scored, written *after* the labels were frozen and *before* any tuning:

```text
recall@5 over normal (18 cases): 0.7778 >= 0.80 FAIL
mrr over normal (18 cases): 0.6713 >= 0.70 FAIL

| normal (gated) | 18 | 14/18 | 0.7778 (18/18) | 0.1556 (18/18) | 0.6713 (18/18) | 0.6979 (18/18) |
| ambiguous      | 6 | 6/6   | 1.0000 (6/6)   | 0.4000 (6/6)   | 1.0000 (6/6)   | 1.0000 (6/6)   |
| abstention     | 6 | n/a   | n/a (0/6)      | n/a (0/6)      | 0.0000 (6/6)   | n/a (0/6)      |
```

Both gates failed. They were **not relaxed and the labels were not touched**; `eval/golden.jsonl` has one
commit in its history (`af28c16`, its introduction) — `git log --oneline -- eval/golden.jsonl` proves it
in one line. The gates stayed red from Stage 8 to Stage 8.4, across four stages of other work, and closed
only when Stage 8.5 changed one variable in the analyser — a suffix-folding rule in `tokenize()` that
every rule in that function has to survive: an English generalisation stated without the eval in the room.

The generation eval has never gated anything. It ships no `--min-*` flag, by decision: its judge-dependent
quantity moved from 18-of-18 to 15-of-18 judge-supported answers between two live runs of unchanged code
at `temperature: 0` (`eval/generation_report.md`, and the reasoning in `docs/adr/0001`).

## 4. What is in the repository

`git ls-files` at the acceptance commit — 50 files, and this is the whole tree:

```text
.env.example               README.md                docs/agent-dossier.md      eval/generation_report.md
.github/workflows/ci.yml   ROADMAP.md               docs/study-guide.md        eval/golden.jsonl
AGENT.md                   docs/adr/0001-*.md       eval/README.md             eval/metrics.py
Makefile                   docs/adr/0002-*.md       eval/__init__.py           eval/report.md
pyproject.toml             docs/adr/0003-*.md       eval/generation_eval.py    eval/report.py
                                                                eval/retrieval_eval.py   src/lumen/…        tests/…
```

Expanded: `src/lumen/` holds 8 Python modules plus `py.typed` and 12 fictional corpus documents;
`eval/` holds 5 modules, the golden set, and the two committed reports; `tests/` holds 7 test modules;
`docs/` holds the dossier, the study guide and 3 ADRs. `git ls-files | wc -l` prints `50` at `4cf0c13`,
`51` with the Stage 13 guard added, `52` with this file. There is no vendored data, no model weights, no
fixture directory of real records: the corpus is fictional on purpose, and so is every identifier in it.

## 5. What the fresh clone confirmed about the documentation

Documentation drift is the most frequent defect in this build (I7, I19), so the guide's own promises were
re-run on the clone rather than trusted:

| promise in the docs | re-run on the clone |
| --- | --- |
| study-guide 1: `n-016` retrieves `versioning#2 sla#5 rate_limits#5 guidelines_brazil#1 guidelines_us#1` | identical, in that order |
| study-guide 1: `grep -c -i "purpose\|notice\|down" src/lumen/corpus/sla.md` prints `0` | prints `0` |
| study-guide 3: k-sweep table — `k=1` 0.6667 across the row at 12/18; `k=10` changes only precision, 0.1667 → 0.0833 | identical at all three k |
| study-guide 7: README figures traceable to `eval/report.md` | 11 figures, 0 orphans |
| README: generation eval skips cleanly with no key | prints the skip line, exit `0`, writes no report |
| README: the API score `10.622093619866167` | reproduced by `retrieve('how many requests per minute', k=1).scores[0]` |

### The distinction that had to be made measurable

The other half of incident I20 was not testable locally, so it was measured against GitHub's own history
instead. "A red build" and "a file GitHub refused to parse" look identical from a green `make check`, and
from the API they differ in exactly one field:

```console
$ gh api repos/nolram/fastapi-rag-eval-harness/actions/runs/42705424022/jobs --jq .total_count   # f3d0a7a
1
$ gh api repos/nolram/fastapi-rag-eval-harness/actions/runs/42755857614/jobs --jq .total_count   # 3a06640
0
$ gh api repos/nolram/fastapi-rag-eval-harness/actions/runs/42832145715/jobs --jq .total_count   # 4cf0c13
1
```

| commit | run | name | jobs | conclusion | what it was |
| --- | --- | --- | --- | --- | --- |
| `f3d0a7a` | 42705424022 | `CI` | 1 | failure | a genuine red build: gates failing, reported as such |
| `3a06640` | 42755857614 | **`.github/workflows/ci.yml`** | **0** | failure | the unparseable workflow inherited from `08b6e0b` (I20) |
| `4cf0c13` | 42832145715 | `CI` | 1 | success | the acceptance commit |

Two signals, then, not one. A parsed workflow is named after itself (`CI`); a rejected one is named after
its *file path* and has zero jobs. `tests/test_ci_workflow.py` reproduces the second signal statically —
it asserts every workflow declares a non-empty `jobs:` mapping — so the shape of a zero-jobs run is caught
before a push, and this table is the evidence that the property it guards is the property GitHub actually
enforces.

## 6. Divergences from the brief, and why

The brief is not in this repo — it was a prompt, and only its consequences were committed. Listing them
from the build record rather than from the prompt is itself the divergence in item 12.

1. **Name.** The working directory is `fastapi-harness-case`; the published repository is
   `fastapi-rag-eval-harness`. Intentional, and the mismatch is recorded here so the reader stops guessing.
2. **Two stages were inserted that the plan did not contain.** Stage 6.5 (README + agent dossier) and
   Stage 8.5 (one tuning pass). 8.5 exists because leaving gates red forever would have made the gates
   decorative; it is scoped to one variable precisely so the improvement is attributable.
3. **The dossier became a second thesis.** What began as a build log became the record of what it is like
   to have agents build this: 19 delegated runs, 26 incidents as of this run (the log keeps growing — count
   them with `grep -c "^### I" docs/agent-dossier.md`), and the arithmetic in §7.
4. **Gates are bucket-scoped.** The brief implied thresholds; it did not say over what. Blending the three
   case types would have produced a number that moves when the dataset is edited, so `--min-recall` and
   `--min-mrr` apply to `normal` only, and the bucket name is printed inside each gate line.
5. **`k` is uniform.** Per-case `k` would let a case be made to pass by editing its own depth.
   `--k N` overrides every case at once and is documented as changing what the gate judges.
6. **`must_include` / `must_not_include` are answer-level.** They are informational in the retrieval
   report and never move a retrieval gate. Their `must_not_include` semantics were then re-derived from
   evidence (I21): a violation is an off-scope import, not a falsity claim, and the fix went into the
   harness, not into the labels.
7. **The generation eval measures and refuses to gate.** No thresholds, no CI spend, no key in CI.
   Justified by measurement, not taste: the cross-run drift above.
8. **A real endpoint was used, and it stays out of the repo.** Stage 11 ran against an operator-supplied
   OpenAI-compatible endpoint. Its host, port and address appear nowhere in the tree —
   `grep -rn "31438\|fd7a:" .` returns nothing outside `.venv/`. What *is* recorded is the model identifier
   (`strata-hades/qwen3.8-flash-next-iq3_xxs`) in the dossier header, because which model did this work is
   part of the evidence. No key, token or header value has ever been written to a file here.
9. **CI uses first-party actions only**, and every check step runs `if: always()` so one red run reports
   every failing signal instead of the first one. There is no `secrets:` block in the workflow at all, and
   their absence is the feature, asserted in prose because a test cannot easily assert the absence of a
   secret.
10. **Stage 12's exercise list could not be honoured as written.** The brief asked for "the 6 exercises
    from §8" of a prompt whose list was never recorded here — so it survived only as a count. Seven
    exercises were written from the repository's own measured behaviour.
11. **`eval/` is linted and tested but not type-checked** (`mypy` `files = ["src"]`). Recorded as a borne
    cost in `docs/adr/0003`, not hidden in a config file.
12. **Delegation cost more than planned, and Stage 13's code was not delegated.** Eight of nineteen runs
    ended at a 30-minute ceiling. `tests/test_ci_workflow.py` — the last piece of code this build needed —
    was written by the parent after a third consecutive total-loss run on a ~180-line brief (I25).

## 7. What this run did not establish

- **30 hand-labelled cases, one annotator, no second label set.** Every figure here is regression detection
  on a toy corpus of 64 chunks, not a quality claim about retrieval or about Lumen, which does not exist.
- **The judge is the answering model by default.** `LLM_JUDGE_MODEL` exists to separate them; nothing in
  this repo has ever run with them separated. Self-preference bias is unmeasured here, not excluded.
- **Calibration denominators are 6 and 1.** The disagreement analysis in `eval/generation_report.md` is a
  single observation and is labelled as one.
- **Nothing compares two runs.** The drift that justified refusing a generation gate is invisible to the
  harness: `eval/generation_report.md` is overwritten in place. A cross-run diff is unbuilt debt.
- **`must_not_include` still conflates two things** — off-scope and false — pending a schema split.
- **The CI guard is textual.** It catches the defect that happened and the zero-jobs symptom; it is not a
  YAML parser, and a different YAML mistake still reaches GitHub before anyone notices.

Delegated time, computed rather than recalled:
`python3 -c 'print(210+30, 361+30, (210+30)/(361+30))'` → `240 391 0.6138` — **240 minutes of 391, 61.4%,
spent inside runs that hit a ceiling and delivered nothing** (I17, I22, I25). The number is not a
complaint about the tooling; it is the budget a next build should start from.
