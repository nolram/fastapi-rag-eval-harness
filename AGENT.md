# AGENT.md — working agreement for AI agents in this repo

Every rule here is either a command you can run or is marked **[judgement]**. Where a line cites
`I1`–`I8` it points at an incident in [`docs/agent-dossier.md`](docs/agent-dossier.md) that has a
commit, a committed file, or a recorded run duration behind it.

## 1. Role

An agent here is a short-lived writer with exactly one assigned write target, working in a tree shared
with a parent and with sibling children — the dossier's I1 is what happens when a brief asks for four
deliverables at once: two 30-minute timeouts, 60 minutes of what is now a three-hour build. Your output is a claim
the next reader can re-run: a measurement, a failing gate, a diff — never a summary of intent. You do
not set the contract; the gate thresholds, the golden labels, the metric definitions and the bucket the
gates apply to are already decided, and they are read-only for you.

## 2. Hard constraints

Each of these is checkable; run the command, do not assert the result.

- **The dependency set is frozen.** Five runtime (`fastapi`, `uvicorn`, `pydantic>=2`,
  `pydantic-settings`, `httpx`) and three dev (`pytest`, `ruff`, `mypy`), exactly as listed in
  `pyproject.toml`. No addition, upgrade-pin or extra without explicit human approval. Check:
  `git diff --name-only | grep -x pyproject.toml` — any hit is a decision you were not given.
- **No paid LLM call from `make check`, from pytest, or from CI.** Nothing on the `make check` path
  reads `LLM_API_KEY`, and the workflow has no `secrets.` reference. Check:
  `git grep -n "secrets\." -- .github` must print nothing. Adding the first one is a human edit to
  `.github/workflows/ci.yml`, never a side effect of a feature.
- **`make eval-retrieval` stays networkless and cheap.** Measured on this checkout: `~0.05 s` wall
  (best of 3, `0.053 s`) for
  `.venv/bin/python -m eval.retrieval_eval --golden eval/golden.jsonl --k 5 --min-recall 0.80
  --min-mrr 0.70`; `eval/README.md` records ~0.15 s for the full run over 30 cases. It must stay
  under ~2 s. Any `httpx`, clock, or randomness import reaching `eval/` voids the determinism claim.
- **`eval/golden.jsonl`, `eval/metrics.py` and the two gate thresholds (`0.80`, `0.70`) are read-only.**
  Never edit data, metric or threshold to make a check pass. If a gate fails, leave it failing and
  report it: `git show ca3bd59 --stat` is this repo committing a red gate on purpose.
- **A corpus change that rewrites the golden labels is a labelling bug to report, not a fix to commit.**
  Name the case ids that broke; do not retype a case to match new chunk output. The harness already
  refuses invented chunk ids, emptied expectations, duplicate case ids and a drift in the 18/6/6 split
  with exit `2` and a full list of problems.
- **This repo is public and the domain is fiction.** "Lumen" does not exist. Never introduce a real
  person's name, a real email address, a token, or real customer data. Check that both print nothing:
  `git grep -InE "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}" | grep -v example.com` and
  `git grep -InE "sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"`. The only address allowed
  in this tree is `you@example.com`, and it appears in the corpus on purpose.
- **English** for code, docstrings, commit messages and docs.
- **One commit per logical change**, prefixed `feat:` / `eval:` / `test:` / `docs:` / `chore:` / `ci:`.
  Check: `git log --pretty=%s | grep -vcE "^(feat|eval|test|docs|chore|ci):"` prints `0`. Read the
  number, not the exit status: `grep -vc` exits **1** when the count is zero, so the conforming state is
  the one that looks like a failure to a shell.

## 3. Repo map

| path | what lives there | who may import it |
| --- | --- | --- |
| `src/lumen/` | heading-aware markdown chunker, pure-Python BM25 (`k1=1.5`, `b=0.75`), `py.typed` | `eval/`, `tests/`, the Stage 7 app; it imports nothing in this repo but itself |
| `src/lumen/corpus/` | the 12 fictional API documents — the only knowledge the system has, 64 chunks | package data read by `lumen.retrieval`; nothing imports it |
| `eval/` | `golden.jsonl`, `metrics.py`, `retrieval_eval.py` (computes), `report.py` (renders), `report.md`, `README.md` | `tests/` only; `eval/` may import `lumen` |
| `tests/` | 70 pytest tests, including the two gate assertions and the corpus tripwire | nothing imports `tests/` |
| `docs/agent-dossier.md` | the incident ledger of the build, append-only | prose; imports nothing |
| `ROADMAP.md`, `README.md` | decisions as they were made; the front page with measured numbers | prose |
| `.github/workflows/ci.yml` | enforcement: `make check`, `make eval-retrieval`, the README test-count diff, `eval/report.md` upload | the CI runner |
| `Makefile`, `pyproject.toml` | nine targets; ruff at 100 columns; mypy strict over `src/` | humans and CI |

There is **no top-level `corpus/` directory** (`ls corpus` → "No such file or directory"): the corpus
lives inside the package, so the chunker can read it as package data. The load-bearing rule is the
direction of imports — `eval/` may import `lumen`, and `src/lumen/` must never import `eval`, because
the library must not know the eval exists:

```console
$ grep -rn "from eval\|import eval" src/ tests/ || echo "src and tests never import eval"
tests/test_metrics.py:15:from eval.metrics import (
tests/test_retrieval.py:14:from eval import report
tests/test_retrieval.py:15:from eval.retrieval_eval import (
```

Read that output exactly: the three hits are all in `tests/`, which is allowed and intended — a test
suite that cannot import the harness cannot assert on it. `src/` contributes zero hits. If a change ever
adds a fourth line from `src/`, revert it.

## 4. Conventions

- **Chunk ids are 1-based per document**: `rate_limits#1` is the first `##` section of
  `src/lumen/corpus/rate_limits.md`. The chunker's `doc_id` keeps the `.md` suffix
  (`rate_limits.md`); the chunk id drops it. Recorded in `ROADMAP.md`.
- **`k=5` uniformly.** All 30 golden cases carry `"k":5` — `grep -c '"k":5' eval/golden.jsonl` prints
  30 — and `--k N` overrides every case to the same
  depth — it is not a per-case knob, so `--k 1` and `--k 10` are how you ask about another depth
  without touching the dataset.
- **`reciprocal_rank` is deliberately not "at k"**: a ground truth at rank 6 still contributes `1/6`.
  That is why MRR is printed beside the `top_score` distribution and never alone.
- **`must_include` / `must_not_include` are answer-level constraints.** They belong to the generation
  eval. `make eval-retrieval` reports them as an informational column and nothing more: a retrieved
  chunk containing a `must_not_include` token is **not** a retrieval failure, they never change `pass`,
  and they never move a gate.
- **Matching is case-insensitive, and each expected string sits on one source line** — the chunker
  preserves line breaks, so a `must_include` string that spans two lines in the document will never
  match. Split it at the source instead of making the matcher smarter.
- **[judgement] When a convention and the code disagree, the code wins and you file the disagreement.**
  Do not quietly document what the code "should" do.

## 5. Working protocol

1. Read the tree before you write it: `git status --short`, then `make check`. A dead child leaves
   untracked files behind and a red build with nothing announcing it (I2) — I2 left 7 `E501` errors in
   `eval/report.py` and three untracked 300–800-line files. Never start on an unexamined tree.
2. Read a big file by its surface first: `grep -n "^def \|^class " eval/retrieval_eval.py`, then open
   only those regions. Reading ~620 lines end to end spends the context you need for the edit.
3. Run the **narrowest** real command that could prove you wrong before the broad one: one test file
   (`pytest tests/test_retrieval.py -q`) before `make check`; the module directly rather than through
   `make` whenever the exit code is the claim.
4. Run `make check` before proposing a commit, and paste its literal tail — including the failing lines.
5. Paste command output. Never paraphrase it, never reconstruct it from memory, never "clean it up"
   (I3, I4). A block you typed rather than captured is not evidence.
6. State which command produced every number you quote. `0.7778` without `make eval-retrieval` is a
   decoration.
7. Re-run the eval before quoting any figure in prose, on this commit. A number you did not regenerate
   is not yours to cite.
8. Report red as the result. The expected outcome of a change to the retrieval path on today's `main`
   is a failing gate, described precisely, and a tree nobody has quietly softened.

### Bad vs good: the same red gates, two replies

```text
BAD   "Recall@5 came out 0.7778 against the 0.80 gate — only 0.0222 away, clearly
       noise. I set --min-recall to 0.75 in the Makefile so `make check` goes
       green, and reported the blended all-30 number as the headline (0.83,
       looking good). I also added an abstention cutoff at top_score < 7.0, so
       the six abstention cases now behave."

GOOD  "`make check`: 2 failed, 68 passed. Both failures are the Stage 8 gate
       assertions in tests/test_retrieval.py — the same two that fail on main.
       `make eval-retrieval` exits 2 (make masks the CLI's 1):
         recall@5 over normal (18 cases): 0.7778 >= 0.80 FAIL
         mrr over normal (18 cases): 0.6713 >= 0.70 FAIL
       I did not touch golden.jsonl, metrics.py, or either threshold. I did not
       blend buckets: the 6 abstention cases carry Recall=None and RR=0.0 by
       construction. I did not add a score cutoff: eval/report.md measures
       abstention top_scores 4.4589–7.2278 against answerable normal
       4.5290–16.5259, and the cheapest cut covering all six costs the answer in
       7 of 18 normal cases. The four misses are n-002, n-007, n-015, n-016 —
       that is input to Stage 8.5. The gate stays red."
```

The bad reply is the one an agent writes when it has understood the sentence "make `make check` pass"
and not the paragraph around it.

## 6. Eval discipline

- **Buckets are never blended into one headline score.** The 6 abstention cases have `Recall = None` and
  `RR = 0.0` *by construction*, and the ambiguous cases carry two ground-truth chunks where a normal
  case has one — so a blended mean moves when you add cases, not when the retriever changes.
- **The gates apply to the `normal` bucket only**, and that was recorded in `ROADMAP.md` **before any
  number was computed**. Ordering is the only honest defence: a rule written after the measurement is a
  description of the measurement.
- The CLI still prints the global, diluted figures under the heading "Global line — all 30 cases
  blended, **not a gate**". It is a transparency line, not a result: its recall is computed over fewer
  cases than its MRR, and its MRR carries six structural zeros.
- **`make eval-retrieval` exits `2`, not `1`.** Measured, Stage 6: `make` exits 2 for any failed recipe,
  so the CLI's contract (0 pass / 1 gate failed / 2 golden invalid) is only visible when you run the
  module directly. CI must gate on non-zero and read the printed verdict to tell a failed gate from a
  corrupt dataset — the repo raised this as a bug once and the bug was the expectation (I6).
- **An LLM-as-judge score is a proxy, never a target.** Do not optimise a rubric number directly, and do
  not let one block a merge. The generation eval does not exist yet: `make eval-generation` fails with
  `No module named eval.generation_eval` (measured), which is a fact to state, not "a placeholder".
- **"Just add an abstention threshold" is a proposal that must first refute the measurement.** No
  raw-score threshold separates the classes: abstention `top_score`s span `4.4589–7.2278`, answerable
  normal spans `4.5290–16.5259`, and the cheapest cutoff covering all 6 abstentions (`t > 7.2278`)
  removes the answer in **7 of 18** normal cases. A normalised signal — the top-two score margin, or a
  classifier judged on labels — is the version of this decision that transfers.

## 7. Definition of done

- [ ] `make check` run on the final tree; its literal tail pasted, failing tests named one by one.
- [ ] `make eval-retrieval` run after the last edit; `eval/report.md` regenerated by the CLI, never
      hand-edited (`--report eval/report.md`).
- [ ] The test count stated in `README.md` equals `pytest --collect-only -q` (70 today). CI enforces
      this and the repo shipped once with the README claiming 67 while the suite held 70 (I7).
- [ ] Every 4-decimal figure in `README.md` appears **verbatim** in `eval/report.md`. Two figures were
      once published there that were true measurements and simply not in the report — under this rule
      they were ineligible anyway (I3).
- [ ] No `curl` example documents an endpoint that does not exist. There is no service yet: `make serve`
      dies with `Error loading ASGI app. Could not import module "lumen.main"` (measured).
- [ ] Anything written about a target you did not run is marked *planned*; anything written about one
      you did run states what it printed (I4).
- [ ] Commit is one logical change with an allowed prefix; `git status --short` shows nothing outside
      your write target.

## 8. Honest limitations

What this document cannot enforce, stated plainly, because pretending otherwise is the failure it
documents:

- It **cannot detect a score computed on the wrong bucket.** A blended `0.90` over all 30 cases looks
  great and is meaningless; nothing here rejects it. Only the bucket name printed on the gate line —
  `over normal (18 cases)` — and a reader who checks it.
- It **cannot tell a 30-case toy dataset from a production one.** `README.md` says in its own header
  that 30 hand-labelled cases over 12 documents are for regression detection, not a quality claim; an
  agent can still quote `0.7778` as product quality and nothing here stops it.
- It **cannot make an agent admit it stopped reading.** Context budget is invisible from the outside:
  the two children that timed out (I1) did not report confusion, they reported progress.
- It **cannot catch prose drift** except where CI happens to check. Today that is exactly one line — the
  README's test count. A stale claim about `make check` being green, a stale layout section, or a stale
  ROADMAP entry passes CI, as they did for one commit (I7).
- It **cannot make a read-only file physically read-only.** Nothing stops a child editing
  `golden.jsonl`; only `git diff --name-only` in review does.
- It **cannot prove a pasted block came from a terminal.** Re-running the command is the only control.
- It **cannot vouch for the dossier.** I1's timings and I3, I4, I7 are self-reported by the parent; the
  *Evidence* lines are the part you can check.

## 9. When to stop and ask

Stop and report instead of proceeding when you notice any of:

- You are about to edit `eval/golden.jsonl`, `eval/metrics.py`, or either threshold — even to "clarify"
  a comment next to one.
- A test has been red for more than one of your turns. The second turn means you no longer know why it
  is red; say so.
- You want a new dependency, any pin change, or a `secrets.` reference in CI.
- You are writing a docstring for a behaviour you have not executed on this checkout.
- The task spans more than one subsystem — chunking plus metrics plus the CLI is the shape that ate two
  30-minute timeouts (I1).
- The instruction and the code disagree. Write down both, with file and line, and do not pick a winner
  silently. (This brief described the runtime set as four; `pyproject.toml` lists five. The file wins.)
- You are asked to "make `make check` pass" without being told the gates are expected to fail.
- You have produced output that is better than the repo's red state and cannot name the command that
  produced each figure.
- A `git status --short` you did not cause appears in your tree.

## 10. Anti-patterns

| pattern | what it looks like | what it costs |
| --- | --- | --- |
| Gate relaxation dressed as a fix | "0.7778 vs 0.80 is rounding; bump `--min-recall` in the Makefile." | The gate becomes a description of the retriever you already have. Every future regression inside that slack is invisible. (I5) |
| Blended-score vanity | Reporting one Recall over "all 30 cases" because the `normal` row is red. | The 6 abstention cases inject `None` and structural `0.0`s; the number moves when you edit the dataset, not when the retriever improves. |
| Label archaeology | Retyping a failing case's `expected_chunk_ids`, or recasting it as `abstention`, to match what BM25 returned. | The one non-renewable asset in this repo: labels written before any tuning (I5). Exit `2` refuses some of it; review must catch the rest. |
| Paraphrased evidence | "tests passed", or a tidied-up reproduction of the console. | The next reader cannot tell a green run from a confident guess. I3's numbers were both real and unusable. |
| Prose drift after a code change | Stage 8 raised the suite 67 → 70 and made `make check` fail; `README.md` still said 67 and still called `make check` green (I7). | The front page misreports the build state to every reader who trusts it. Caught by luck, not by a control. |
| Documentation the code has not earned | Describing `make eval-generation` as "a placeholder" when the module is absent and the target fails (I4); a `curl` example against a route that 404s. | Docs start asserting state from intent. The reader runs the command and stops trusting everything else in the file. |
| Half-written inheritance | A child dies mid-write; the next one edits without looking. I2 left 7 `E501` errors and three untracked files behind. | Repair work you did not budget, on a tree you did not read. Recovery rule: `git status --short`, then `make check`, before touching anything. |
| Ambition-shaped brief | One run asked for the CLI, the renderer, the README, five tests and six checks — and timed out twice (I1). | 60 minutes of a three-hour build, for work a one-file brief cleared in 6 m 45 s. |
| Confident anomaly | "Exit code contract broken — `make` returned 2!" Reported before reading what `make` does with recipe status (I6). | A wrong bug report that lands in the roadmap and gets fixed by someone else's afternoon. |
| Single-machine determinism | "Byte-identical output," proven on the machine that wrote the code (I8). | The claim is worthless until a second machine reproduces it — CI did, at the same four decimals. |
| Number without provenance | Quoting a metric that no committed artifact carries (I3). | The reader re-runs the eval, finds nothing, and discounts every other figure beside it. |
| Threshold tourism | Picking `LUMEN_ABSTAIN_THRESHOLD` by eye when the sweep says no raw cutoff separates the classes. | 7 of 18 answerable questions refused, and the constant silently re-tunes itself every time the corpus changes. |

---

This document describes how work in this repo is done, and it is subject to the same rule as everything
else in it: a claim made here must be verifiable by a command.
