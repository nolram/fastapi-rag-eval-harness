# ROADMAP — `fastapi-rag-eval-harness`

**Status: Stages 0–11 complete, plus Stage 8.5** (bootstrap, corpus, chunker, BM25 retrieval,
`golden.jsonl`, metrics, retrieval eval harness, README + agent dossier, quality gates, CI, AGENT.md,
the FastAPI service, one honest retriever tuning pass, the LLM-as-judge generation eval). Stage 12
(ADRs + study guide + README finalisation) is next. **`make check` has been green since Stage 8.5** —
the two Stage 8 gates failed on purpose from Stage 8 until the one-variable tuning pass made them
pass by fixing a real defect, not by moving them.
Implementation was delegated stage-by-stage to subagents and reviewed by the parent before committing;
each stage's gate is recorded below.

Recorded decisions so far that later stages must respect:
- Chunk ids are 1-based per document (`rate_limits#1` = first `##`); `doc_id` keeps the `.md` suffix.
- All 30 golden cases use `k: 5`; the CLI `--k` overrides uniformly (study-guide exercise 3).
- `must_include` / `must_not_include` are **answer-level** constraints (generation eval), reported as
  information in the retrieval eval but never a retrieval gate: a retrieved chunk that happens to
  contain a `must_not_include` token is not a retrieval failure.
- Matching of `must_include` is case-insensitive and each string is guaranteed to sit on a single
  source line (the chunker preserves line breaks).
- Golden labels were written without ever running the ranker (audited), so a Stage 6 failure is a
  retriever miss, not a labelling artifact.
- **Gate scoping (decided before any number was computed):** the `--min-recall` / `--min-mrr` gates
  apply to the `normal` bucket only, because blending buckets is exactly what this repo forbids and
  the 6 abstention cases have `Recall = None` and `RR = 0.0` by construction. The CLI must still
  print the global, diluted figures as a non-gated transparency line, and `tests/test_retrieval.py`
  asserts the gates against the same bucket the CLI gates on.
- `reciprocal_rank` is deliberately **not** "at k": a ground truth at rank 6 still contributes
  1/6, which is why MRR is reported next to the score distribution rather than alone.
- **`make eval-retrieval` exits 2, not 1** (measured, Stage 6): `make` exits 2 for any failed recipe, so
  the CLI's exit-code contract (0 pass / 1 gate failed / 2 golden invalid) is only visible when the module
  is run directly. CI must gate on *non-zero* and read the printed verdict to tell a failed gate from a
  corrupt golden file. Recorded in `eval/README.md`.
- The abstention threshold sweep found **no separating threshold at all**: abstention `top_score`s span
  4.46–7.23, answerable `normal` scores span 4.53–16.53, so the ranges overlap and the cheapest
  threshold covering all 6 abstentions (`t > 7.23`) removes the answer from 7 of 18 `normal` cases.
  This is a measured result, not a gap to close; any later "just add a threshold" change must cite it.

Execution plan for the TASK prompt (Stages 0–6 are done; the rest is still ahead of us). Each stage
has: goal, files, commands, exit gate, and commit. Stages are ordered so that the two mandated orderings
hold:

1. `golden.jsonl` is written **before** any chunker/BM25 tuning (Stage 4 before Stage 6).
2. README numbers are pasted **only** from a real `make eval-retrieval` run (Stage 6 output → Stage 12).

Checkpoints where I stop and report to you: **C1 (Stage 0), C2 (Stage 6), C3 (Stage 8), C4 (Stage 13)**.

---

## Stage 0 — Bootstrap (repo first, no logic)

**Do first, per §9:** `gh repo create` **before** `git init` (create remote public repo from an empty
dir, then `git init` inside it). Never hardcode username — use `gh api user -q .login`.

- Env already verified: `gh` logged in as **`nolram`**, Python **3.12.3**, git 2.54, GNU Make 4.3,
  `uv`/`ruff`/`mypy`/`pytest` present. `fastapi`/`pydantic-settings` **not** installed → venv in Stage 0.
- Files: `.gitignore`, `.env.example`, `pyproject.toml` (runtime deps: `fastapi`, `uvicorn`,
  `pydantic`, `pydantic-settings`, `httpx`; dev: `pytest`, `ruff`, `mypy`; ruff + `mypy --strict`
  config; `packages = ["src/lumen"]`), `Makefile` (`install fmt lint typecheck test check serve
  eval-retrieval eval-generation`), `src/lumen/__init__.py`, empty `eval/`, `tests/`, `docs/adr/`.
- Gate: `make install && make check` exits 0 on the skeleton; repo is **public** on GitHub.
- Commit: `chore: bootstrap repo skeleton, tooling and make targets`
- **C1 checkpoint**: confirm dep list (AGENT.md §2 forbids new runtime deps without approval) + repo name/visibility.

## Stage 1 — Fictional corpus (`src/lumen/corpus/*.md`)

- 12 docs: `auth rate_limits pagination idempotency webhooks errors versioning data_retention sla
  guidelines_us guidelines_brazil refunds`. 15–40 lines each, `# Title` + `## Subsection`.
- Hard, scannable numbers (e.g. rate limit `120 rpm`, burst `200/10s`).
- Deliberate cross-market contradictions (`guidelines_us.md` = 30 days notice,
  `guidelines_brazil.md` = 60 days) → makes `ambiguous` cases testable.
- **Forbidden:** crypto/Bitcoin, public pricing, real names/emails (`you@example.com` only).
- Gate: `grep -ri "bitcoin\|crypto\|price"` clean over corpus; every doc has ≥2 `##` headings.
- Commit: `feat: fictional Lumen API corpus`

## Stage 2 — Heading-aware chunker

- `src/lumen/chunking.py`: `Chunk(id="<doc_stem>#<n>", doc_id, title, text)`, `chunk_document()` /
  `load_corpus()` (resolve corpus dir via `Path(__file__).parent`, since corpus ships inside the package).
- `tests/test_chunking.py`: id format, ordering, every chunk keeps its heading in `title`, no empty chunk.
- Gate: `pytest tests/test_chunking.py` green.
- Commit: `feat: heading-aware markdown chunker`

## Stage 3 — Pure-Python BM25 + `retrieve()`

- `src/lumen/retrieval.py`: tokenizer, inverted index, `BM25.build(chunks, k1=1.5, b=0.75)`,
  `.search(query, k) -> list[tuple[Chunk, float]]`, `retrieve(query, k=5) -> RetrievedResult`
  (`chunks`, `scores`, `top_score: float | None`). ~50 lines, no faiss/sentence-transformers.
- Purity: no `random`, no time, no network → same input ⇒ same output.
- Gate: two identical `retrieve()` calls return identical ids+scores; `pytest` green.
- Commit: `feat: pure-python BM25 retrieval`

## Stage 4 — `eval/golden.jsonl` (**before** any tuning) ⚠ mandated ordering

- 30 hand-labelled cases: **18 `normal` / 6 `abstention` / 6 `ambiguous`**, schema
  `id, type, query, expected_chunk_ids, expected_doc_ids, must_include, must_not_include, k,
  should_abstain, clarify, notes`. Include the 3 literal examples from §4.3.
- Chunk IDs must reference the **real** chunker output from Stage 2/3 (print the id list first).
- Add `eval/validate_golden.py` (or a `--validate` flag): JSONL parses, ids unique, counts per type,
  every referenced chunk id exists, `abstention` ⇒ `expected_chunk_ids == [] and should_abstain`.
- Gate: validator clean. **No BM25 parameter tuning in this stage.**
- Commit: `eval: hand-labelled golden dataset (pre-tuning)`
- **DONE.** 30 cases (18/6/6), 12/12 docs covered by `normal`, `k=5` uniform. Parent-side audit:
  every `must_include` verified case-insensitively inside its expected chunk *and on one line*; no
  `must_not_include` contradicts its own expected chunk; ambiguous queries never name a market;
  abstention topics grep-verified absent. Parent audited the child's session transcript: 0 of its 12
  commands imported or ran `lumen.retrieval`. Validation is not a committed file — it belongs in
  `eval/retrieval_eval.py` (Stage 6), so §3's file tree stays exact.

## Stage 5 — Metrics with abstention-safe semantics

- `eval/metrics.py`: `recall_at_k`, `precision_at_k`, `reciprocal_rank`, `ndcg_at_k`.
  Empty `relevant` ⇒ `None` for Recall/Precision/nDCG, `0.0` for RR.
- Aggregator **excludes `None` from means** and reports the excluded count per metric (never silently zeroes).
- `tests/test_metrics.py`: hand-computed expected values, incl. the `None` path and partial-relevance nDCG.
- Gate: `pytest tests/test_metrics.py` green.
- Commit: `eval: retrieval metrics with abstention-safe aggregation`
- **DONE.** Parent-side audit, independent of the child's tests: hand-checked recall/precision/RR/nDCG
  on a 3-relevant / 5-slot case; precision denominator proven to be `k` and not `len(retrieved)`;
  duplicate ids do not inflate credit; `k=0` raises; and a **brute-force reference nDCG cross-checked
  against 3000 random orderings with 0 mismatches**. Also confirmed the repo's central claim in
  numbers: two rankings with identical Recall@5 = 1.0 score nDCG@5 1.0 vs 0.501.

## Stage 6 — Deterministic retrieval eval + report

- `eval/retrieval_eval.py` CLI: `--golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr 0.70`.
  No LLM, no network, runs < 2s. Per case: `retrieved_ids`, `top_score`, recall@k, precision@k, RR,
  nDCG@k, `pass`. Aggregates **per `type`** — never one global mean.
- Abstention analysis: `top_score` distribution + **threshold sweep 0.5→4.0 step 0.5** reporting
  correct abstentions vs `normal` cases lost, with a written note on why raw-score thresholds are fragile.
- `eval/report.py` → `eval/report.md`; `eval/README.md` explains how to read it.
- Exit code ≠ 0 below the gate (this is what blocks CI).
- Gate: `make eval-retrieval` completes in <2s; report.md exists.
- Commit: `eval: deterministic retrieval eval harness`
- **C2 checkpoint**: paste literal output — Recall@5, Precision@5, MRR, nDCG@5, threshold curve,
  **even if the numbers are bad**. Bad metrics are the point of the exercise.
- **DONE — gate FAILED, and that is the recorded outcome.** Measured over the `normal` bucket (18 cases,
  k=5): Recall@5 **0.7778** (gate 0.80 → FAIL), MRR **0.6713** (gate 0.70 → FAIL), Precision@5 0.1556,
  nDCG@5 0.6979; 14/18 normal cases pass. `ambiguous` 6/6. Run time 0.06s, byte-identical stdout and
  `report.md` across runs. **Nothing was retuned to make this green**: `golden.jsonl`, `metrics.py`, the
  thresholds, the corpus and the ranker are untouched. `tests/test_retrieval.py` still has no gate
  asserts (reserved for Stage 8), so `make check` is green while `make eval-retrieval` exits non-zero —
  that gap closes in Stage 8, which will fail until the retriever improves.
- Known debt, deliberately not paid now: `eval/retrieval_eval.py` + `eval/report.py` are 1.04k lines
  (622 + 418), more than this repo's "less code" premise wants. Two delegated attempts to trim it hit
  the 30-minute child timeout; a third pass is welcome but must keep the rendered output byte-identical,
  which is why `/tmp` reference captures and `diff` are the acceptance test for it.

## Stage 6.5 — README + agent dossier (added mid-build at the operator's request)

Not in the original plan. The operator asked for a README before Stage 8 because the repo had no
readable thesis, then asked for a third deliverable: a public record of **how** agents built it.

- `README.md` written now, **Stage 12 finalises it**: it must gain the API quickstart + `curl` once the
  service exists, and keep the rule that no number appears here that is absent from `eval/report.md`.
- `docs/agent-dossier.md` is a **living document**, append-only per stage: a ledger row for every
  delegated run (wall-clock from the run metadata) plus an incident write-up. First cut covers Stages 0–6
  (`ca3bd59`): 9 runs, 2 h 28 m of delegated time, 40% of it burned on two Stage 6 timeouts.
- Divergence recorded: the README promised no `curl` because no endpoint exists; if Stage 7 slips, the
  README must be edited, not quietly left optimistic.
- Standing rules added by this stage, learned from the incidents:
  one write target per child brief; after any child death run `git status --short` then `make check`
  before relaunching; every "what it does" claim in docs must come from having run the command.

## Stage 7 — FastAPI service

- `config.py` (pydantic-settings), `schemas.py` (pydantic v2), `prompts.py` (`PROMPT_VERSION` +
  versioned templates), `generation.py` (`answer()`: real iff `LLM_API_KEY`, else explicit stub that
  abstains), `main.py` (`GET /health`, `POST /search`, `POST /ask`).
- Layering: corpus → chunking → retrieval → generation → main.
- `tests/test_api.py` with `TestClient`, `/ask` exercised with LLM **disabled** (no paid call in tests).
- Gate: `pytest tests/test_api.py` green; `make serve` answers the README `curl`.
- Commit: `feat: FastAPI search/ask endpoints`
- **DONE.** `tests/test_api.py`: 18 passed. Whole repo: **2 failed, 86 passed** (the two failures are
  still the Stage 8 gates; the delta is exactly the 18 new tests). `ruff check .` and `mypy src`
  (strict, 8 files) clean. `make serve` answered all three endpoints for real; the JSON in `README.md`
  is pasted from that run at `k=1` on port 8000, nothing abbreviated — the `…` in the snippet is the
  service's own 200-character truncation, verified: the chunk is 204 characters and the field arrives at
  exactly 200 ending in `…`.
- Layering held: `main` → `generation` → (`prompts`, `retrieval`) → `chunking`; `config.py` is the only
  module that reads the environment. `/health` and `/ask` both report `mode` (`stub` with no key), so
  the answerer's honest state is visible without probing it.
- **No paid call, proven two ways.** The suite monkeypatches `httpx.post`/`httpx.Client.post` to raise on
  any call, and the live path is exercised only through `httpx.MockTransport`. Independently, the parent
  re-ran the whole suite with `LLM_API_KEY` and `LLM_MODEL` set to fake values: still `2 failed, 86
  passed` — no test makes or needs a real request.
- Credential discipline tested, not asserted: the API key appears in the outgoing header but the tests
  assert it is absent from `repr()` of the result and of both failure modes (bad status, transport
  error). Exception messages carry a status code only; the provider body is never included, because an
  upstream error page can echo request material.
- `LUMEN_ABSTAIN_THRESHOLD` stayed `None` and **no score-based abstention was added**. The field carries
  a comment citing the measured overlap (4.4589–7.2278 vs 4.5290–16.5259, 7 of 18 cases lost) so that
  the next person cannot set it without confronting that number. Two tests pin both directions: nothing
  abstains on score by default, and a configured threshold abstains before any request leaves.
- **The finding of the stage (I10 in the dossier):** curling `/ask` with `"what happens when my account
  gets locked"` returned `context_chunk_ids: []` — 0 of its 4 tokens exist in the corpus vocabulary, and
  the corpus says "five failed attempts … lock the API key". Measured against the dataset: **0 of the 30
  golden cases retrieve zero candidates**, though 28 of 30 carry out-of-vocabulary terms (~50% of their
  tokens on average). The gates watch ranking among candidates and are blind to the absence of
  candidates. This is now recorded as a limitation in `README.md` and `AGENT.md` §8.
- Two traps I hit and recorded rather than hid: `pkill -f "uvicorn lumen.main"` matched the shell's own
  command line and killed the audit session (and again with `pgrep -f`) — use `pkill -f "[u]vicorn"` with
  a command line that does not itself contain the pattern, or kill by port. And the child run finished in
  **28 m 34 s**, 1 m 26 s inside the 30-minute ceiling that killed two Stage 6 children: this brief was
  six files, and it nearly cost the stage. Larger briefs still cost time proportionally; the next one
  gets split.

## Stage 8 — Quality gates as tests

- `tests/test_retrieval.py`: `Recall@5 >= 0.80`, `MRR >= 0.70`, each with the adjacent comment
  *"if this fails, fix the retriever, not the assertion"*; plus a test that **fails on purpose** when
  a critical corpus doc is dropped (monkeypatched corpus) — proof the harness detects regressions.
- Gate: `pytest tests/test_retrieval.py` green **or red**.
- **C3 checkpoint**: if a gate fails, **leave it failing** and explain which cases flipped and why.
  Do not relax thresholds, do not edit golden labels.
- Commit: `test: retrieval quality gates and regression tripwire`
- **DONE — RED, as agreed with the operator.** `pytest tests/test_retrieval.py`: 2 failed, 26 passed;
  whole repo: 2 failed, 68 passed. `make check` exits 2 (lint and mypy pass; the `test` recipe fails).
  `test_recall_at_5_over_the_normal_bucket_only_meets_the_gate` fails at 0.7778 vs 0.80 and
  `test_mrr_over_the_normal_bucket_only_meets_the_gate` at 0.6713 vs 0.70 — both read from
  `eval.retrieval_eval.run()`, neither recomputes nor hardcodes a score. The thresholds are literals in
  the test file, deliberately **not** the CLI defaults, so lowering the default reddens these tests
  instead of carrying them to green. No `skip`, no `xfail`, no tolerance, no bucket switch.
- Tripwire verified **non-vacuous** by an independent parent-side probe: dropping `webhooks.md` flips
  `n-010` (webhook retry window) from PASS to FAIL, 14 → 13 normal passes, and the monkeypatched corpus
  restores exactly (14 passes again, same case ids). The gate figures stayed 0.7778 / 0.6713 across three
  consecutive runs, so the patched index does not leak.
- Divergence from the plan's wording: the prompt described the tripwire as "a test that **fails on
  purpose**" when a doc is dropped. It is implemented the other way round — it **passes** by asserting
  that the harness notices the loss. A test that is red in the committed state proves nothing about
  sensitivity and just adds noise to an already-red run.

## Stage 8.5 — make the gates honest-green (DONE)

Deferred by the operator at Stage 8, recorded here so it was not lost, executed after Stage 10.

- The gates may turn green **only** through the retriever: chunking and BM25 (`k1`, `b`, tokenisation,
  title weighting, stopword list), never through `golden.jsonl`, `eval/metrics.py`, the two thresholds,
  or the assertion text.
- Every change must be justified by a named case flipping, and `eval/golden.jsonl` must stay untouched —
  if a label is genuinely wrong, it is reported as a labelling bug, not silently repaired mid-tuning.
- Because `eval/report.md` is committed, each tuning step shows up as a diff of the report; the README
  numbers then have to be refreshed from the new run, which is the same single-source rule as Stage 12.
- Stage 7 added a second, better-motivated target than "raise Recall@5": **the zero-candidate path**.
  A stemming or synonymy change that turns `"account gets locked"` from 0 hits into a hit on `auth#4`
  is worth more than a reranking tweak, and it is justified by a named query rather than by a score.
- **DONE — one variable, one commit.** `tokenize()` in `src/lumen/retrieval.py` now folds regular English
  inflectional suffixes. `k1`, `b`, the stopword list, the title weight, `chunking.py`, both thresholds,
  `eval/metrics.py` and `eval/golden.jsonl` are untouched; `git diff --stat` for this stage lists
  `retrieval.py`, `tests/test_retrieval.py` and `eval/report.md` and nothing else.
- Hypothesis, owned by the parent and handed down: the corpus says *lock*, a user says *locked*; a purely
  lexical analyser cannot bridge that, and the fix is morphology-free suffix folding, not a synonym table.
  **Anti-overfitting rule stated before implementing:** every rule had to be justified by an English
  generalisation that could be explained to someone who had never seen the golden set; a rule whose only
  merit was a named case was deleted. Rules were considered and refused on those grounds — the record of
  that is in the module's own comments.
- Measured, by the parent's own script and not by the child's word: `normal` Recall@5 `0.7778 → **0.8333**`,
  MRR `0.6713 → **0.7269**`, nDCG@5 `0.6979 → 0.7534`, Precision@5 `0.1556 → 0.1667`, cases passing
  `14/18 → 15/18`. Gates: **both PASS**, `make eval-retrieval` exits 0, `make check` fully green
  (`96 passed`) for the first time in the repo's history.
- **Flip list, both directions: `n-002` FAIL→PASS; nothing PASS→FAIL.** Still failing: `n-007`, `n-015`,
  `n-016` — each now retrieves a confident wrong document rather than nothing, which is the more
  interesting failure and the reason the gates are not the end of the story.
- The `ambiguous` bucket is untouched at `1.0000` (6/6) and abstention still yields `mrr 0.0000` with
  `n/a` recall — the change did not buy its numbers by softening either bucket.
- The abstention finding got **stronger** under the change, which is the opposite of what a tuning pass
  usually produces: the overlap interval moved from `[4.5290, 7.2278]` to `[4.5297, 9.2034]` and the
  cheapest covering cutoff went from costing `7 of 18` answerable cases to `10 of 18` (55.6%). Recorded
  in `eval/README.md` as a re-measurement, not a restatement.
- Stage 7's blind spot: `"what happens when my account gets locked"` went from **0 candidates** to 3,
  led by **`auth#4`** — the exact chunk the golden set names for that fact. But **0 of the 30 golden
  cases retrieve zero candidates, before or after**, so nothing in this dataset would notice if a future
  change re-broke it. The limitation is reworded, not removed.
- Costs, stated rather than buried: `handling`/`handled` fold to the fragment `handl` and never meet
  `handle` (restoring a dropped silent `e` needs a lexicon); `too_many_requests` folds inside the
  underscore-joined identifier; `make`/`making`/`made` stay three terms. Each is pinned by a test, so the
  behaviour is a decision. The design prefers **splitting a word family to merging two unrelated words**.
- Four pre-existing assertions became false statements about the new analyser and were updated with the
  operator-approved narrow grant: two tokenizer expectations (`seconds → second`, `policies → policy`),
  the `--k` pass-count tripwire (`11/14/15 → 12/15/15`, re-measured, still non-vacuous because an ignored
  `--k` would print `15/15/15`), and the title-repetition fixture, whose premise the folding destroyed.
  The replacement fixture was **proved non-vacuous before being trusted**: at `TITLE_REPETITION = 1` the
  order flips to `[body#1, head#1]`. The first fixture I wrote for it passed for the wrong reason (BM25
  length preference, not title weight) and was thrown away.
- The two gate assertions and their comments are byte-identical to Stage 8's, verified with
  `git diff tests/test_retrieval.py`.
- A trap for the next reader, because the digits collide: the `normal` gate now reads `0.8333`, which is
  exactly the number the **blended all-30** line printed before this stage. The blended line itself moved
  to `0.8750`. Quote the bucket name, or the coincidence does the misleading for you.
- Everything derived from the old scores went stale in the same instant and was re-captured, not
  reworded: the `curl` block in `README.md` (the pasted score moved from `13.20236738298299` to
  `10.622093619866167` because the analyser changed under it), the README badges, the `pytest` count the
  CI docs-check compares, and the abstention bands in `AGENT.md` and `eval/README.md`.
- CI on the first green commit: run `42739925820`, **`completed success`, 39 s** — the first passing
  run on `main` in this repo's history. What that run does **not** re-prove: the cross-machine
  byte-identity of `eval/report.md` on GitHub's runner. The Stage 9 proof checked the uploaded artifact
  against the local file, and this time `gh api …/actions/runs/{id}/artifacts`, `gh run view --log` and
  the anonymous web page all returned 404 (the commit's token can list runs but not read them), so no
  artifact was downloaded and **no byte comparison was made**. `gh run list` reporting `success` is real
  evidence that the gates cleared on a second machine; it is not evidence that the exact five decimals
  matched there. The claim is narrowed to exactly that, and the Stage 9 proof stands for the pre-8.5
  numbers only.

## Stage 9 — CI

## Stage 9 — CI

- `.github/workflows/ci.yml`: `make check` + `make eval-retrieval`, deps cache (`uv`/pip cache),
  **no LLM step**, no secrets.
- Gate: Actions run green on the pushed branch (or the failure is reported verbatim).
- Commit: `ci: run checks and retrieval eval on every commit`
- **DONE — first run on `main` is RED, as agreed.** Run `42705424022`: `Install via make install` ✅,
  `make check` ❌, `make eval-retrieval` ❌, `Docs consistency` ✅, `Upload eval/report.md` ✅. Both gate
  failures are the same two assertions with the same figures as locally — Recall@5 `0.7778`, MRR
  `0.6713` — which is an unplanned but useful result: **the golden eval reproduces bit-for-bit on
  GitHub's hardware**, so the determinism claim is no longer only a same-machine claim.
- Divergences recorded: only **first-party** actions (`checkout`, `setup-python`, `upload-artifact`) —
  no `setup-uv`, since every third-party action is an unpinned dependency in the supply chain. The plan
  said "`uv`/pip cache"; the runner image had **no `uv`**, so `make install` took the `python3 -m venv` +
  pip branch (proven in the step log: `Uninstalling pip-25.0.1 → Successfully installed pip-26.3`) and
  `cache: pip` is genuinely exercised. If a future runner image ships `uv`, the pip cache silently stops
  mattering — re-check the install step log rather than assuming the cache is live.
- Every check step is `if: always()`: one red run reports every failing signal instead of stopping at the
  first. `make eval-retrieval` keeps its exit code — no `-` prefix, no `|| true`, no `continue-on-error`,
  which would silently un-block `main`.

## Stage 10 — `AGENT.md` (the deliverable you most want to study)

- English, imperative, no marketing. Sections 1–10 exactly as specified: Role · Hard constraints ·
  Repo map (table path → contents → who may import; eval imports src, never reverse) · Conventions ·
  Working protocol · Eval discipline (no blended score; judge is a proxy) · Definition of done ·
  Honest limitations · When to stop and ask · Anti-patterns.
- Plus **exactly one** good-vs-bad agent reply example (short before/after) under §5.
- Gate: every §5 bullet from the prompt present; example count == 1.
- Commit: `docs: AGENT.md working agreement for AI agents`
- **DONE.** 247 lines, ten sections in the specified order, one example. Verified by command, not by
  reading: `grep -c "^## "` → 10; the example-count grep → 1; every quoted `make X` target exists in the
  Makefile (the only extra hits are the English phrases "make a"/"make an"/"make masks"); every quoted
  **eval CLI** flag (`--golden`, `--k`, `--min-recall`, `--min-mrr`, `--report`) appears in both
  `eval/README.md` and `eval/retrieval_eval.py` — the other `--flags` in the file belong to `git` and
  `pytest` commands, which is correct and was checked separately; the `src/`-never-imports-`eval` claim
  was checked before being written and the child pasted the three `tests/` hits, explaining why they are
  allowed rather than hiding them.
- Every 4-decimal figure the file quotes is present in the committed `eval/report.md` — including the
  score ranges `4.4589–7.2278` vs `4.5290–16.5259` (**the bands as they stood before Stage 8.5**; the
  current sweep in `eval/report.md` reads `4.3303–9.2034` vs `4.5297–20.0727`), which the child
  **recomputed from the harness** rather than inheriting the two-decimal version in `ROADMAP.md`. The
  blended "vanity" figure in the bad example (`0.83`) is not a hypothetical: `eval/report.md` line 34
  prints `recall@5 0.8333` — which is now the *`normal`* bucket's figure, and the blended line has
  moved to `0.8750`; quote the bucket name or the coincidence misleads.
- Claims I re-measured rather than trusted: `make eval-generation` really fails with
  `No module named eval.generation_eval`; `make serve` really dies with `Error loading ASGI app. Could not
  import module "lumen.main"`; the harness really exits `2` on an invented chunk id, an emptied
  expectation, a duplicate case id and a split drift; `k1=1.5`, `b=0.75`; ruff at 100 columns; mypy strict
  over `src/`; 30 cases with `"k":5`; 64 chunks / 12 documents; nine Makefile targets; the email and
  token greps print nothing.
- Two things I corrected on review, both of them the same failure mode — **a number that rots**:
  `0.055 s` (I measured `0.053 s`, best of three) became `~0.05 s (best of 3, 0.053 s)`, and "all 13
  commits conform" became the command that proves it for any future count:
  `git log --pretty=%s | grep -vcE "^(feat|eval|test|docs|chore|ci):"` → `0`. Percentages of the
  delegated total were also removed from the prose for the same reason — they change with every child —
  leaving absolute minutes instead. The third was mine: a check I wrote as "prints `0`" via `grep -vc`,
  which exits **1** when the count is zero, so the passing state reads as a shell failure; the line now
  says to read the number and not the status.
- **I9 in the dossier is the best argument this document works:** the brief I wrote said four runtime
  dependencies (copied from `ROADMAP.md`); `pyproject.toml` lists five. The child wrote five, and logged
  my error as a worked example under *When to stop and ask*.

## Stage 11 — LLM-as-judge generation eval (**measurement, not a gate**)

Planned at Stage 0, from the task prompt, as "**Gated** LLM-as-judge generation eval" with a
six-field rubric and a separate `eval/calibration/labels.csv`. Executed differently, and the
differences are the point:

| planned | built | why |
| --- | --- | --- |
| a gate on judge scores | **no `--min-*` flag anywhere in the file** | Fixing a threshold before seeing the distribution is the anti-pattern `AGENT.md` records as I5. Stage 8.5 is the standing proof that a threshold can only be argued from a measured distribution. |
| rubric `{groundedness, citation_correct, complete, abstained_correctly, verdict, rationale}` | `{supported, should_have_refused, reason}`, exactly three keys, enforced | A reasoning model asked for six numeric fields produced six confident numbers about one short answer. Two of them are the questions this repo can actually defend asking: is this answer supported by that context, and should this question have been refused. Anything else was decoration. |
| `eval/calibration/labels.csv`, 20 rows of human verdicts | calibration against the labels already in `eval/golden.jsonl` | `must_include` / `must_not_include` / `should_abstain` were hand-written before any ranker ran (audited), so they are already the independent human signal. A second hand-label file would be a second place for the same opinion to rot. `eval/calibration/` does not exist and `--calibrate` is not a flag. |
| generation figures into `eval/report.md` | `eval/generation_report.md` | `eval/report.md` is the retrieval artefact the README quotes and the README-vs-report CI check compares against. Two measurements, two artefacts, one provenance each. |
| `temperature=0`, `max_tokens` cap | `temperature=0`, **no** `max_tokens` | The endpoint is a reasoning model: a cap truncates the reasoning into `content: ""` with `finish_reason: length`, which a strict parser then miscounts as a judge's opinion rather than a transport hole. |

- **Gate that was kept:** the skip path must be safe — no key, exit `0`, no file written, no socket.
  Verified on three machines' worth of conditions: locally, by the test suite under a network
  tripwire, and by CI, which runs `make eval-generation` on every push with no secret available.
- Commit: `eval: llm-as-judge generation eval, measurement only`

## Stage 12 — ADRs, study guide, README (with real numbers only)

- `docs/adr/0001-separate-retrieval-and-generation-evals.md`,
  `0002-manual-golden-dataset-before-llm-judge.md`, `0003-evals-live-in-the-repo.md`.
- `docs/study-guide.md`: 7 exercises (the 6 from §8 + the "audit an agent against AGENT.md" one),
  each with **what to expect**.
- `README.md` in the required order: 3-sentence what/why → quickstart + `curl` → **"The one idea"**
  (cost/frequency/reproducibility table) → **Current numbers (as of <date>)** pasted verbatim from
  Stage 6/13 output with the *"30 hand-labelled cases … regression detection on a toy corpus, not a
  quality claim"* warning → how to run generation eval → pointer to `AGENT.md` + `study-guide` →
  honest limitations.
- Gate: no number in README that isn't in `eval/report.md`.
- Commit: `docs: ADRs, study guide and README`

## Stage 13 — Acceptance run + final report (§9/§11)

- `make check`, `make eval-retrieval`, `pytest -q`, `git ls-files`; push to `main` (no force push,
  no other user/org).
- **Carry this debt into the acceptance run (I20):** a workflow step name containing `: ` made the whole
  file unparseable and the run failed in 0 s with zero jobs, invisibly to `make check`. Add the
  dependency-free guard — a stdlib scan of `.github/workflows/*.yml` for a `name:` line with a second
  `: ` — and assert that a commit touching `.github/` produces a CI run **with jobs**, which is the check
  that actually distinguishes "red build" from "rejected file".
- **C4 final report**: (1) repo URL · (2) literal `make eval-retrieval` output per case type ·
  (3) pass/fail counts on the **first** run and any gate that failed (left failing) · (4) `git ls-files` tree ·
  (5) every divergence from the prompt and why.

---

## Commit sequence (logical, no mega-commit)

`chore: bootstrap` → `feat: corpus` → `feat: chunker` → `feat: BM25` → `eval: golden dataset` →
`eval: metrics` → `eval: retrieval harness` → `feat: fastapi` → `test: gates` → `ci:` →
`docs: AGENT.md` → `eval: generation judge` → `docs: adr/study-guide/readme`

## Standing rules carried across all stages

- Public repo ⇒ zero sensitive data; fictional domain "Lumen"; `.env` ignored, only `.env.example`.
- No paid LLM call from tests or `make check`.
- Never edit `golden.jsonl`, `metrics.py`, or test thresholds to make a check pass.
- Code and docs in English.

## Open questions (answer before Stage 0/1 to avoid rework)

1. **Path**: the prompt says `~/projects/fastapi-rag-eval-harness/`, but this session's cwd is
   `/home/coder/Developer/fastapi-harness-case` (and `~/projects` doesn't exist). Build here and
   name the repo `fastapi-rag-eval-harness`, or create `~/projects/...`?
2. **Dependencies** (Stage 0): approve `fastapi, uvicorn, pydantic, pydantic-settings, httpx`
   (runtime) + `pytest, ruff, mypy` (dev)? `httpx` is needed for `TestClient` and the judge call.
3. **LLM judge provider/model** for Stage 11 — and do you want it actually executed, or only the
   gated skip path + calibration harness (no key used in CI)? — **answered:** the operator supplied a
   self-hosted OpenAI-compatible endpoint and asked for the eval to be executed for real, with the key
   held in the environment and never in the repo. CI keeps no secret, so CI takes the skip path forever.
4. **Prompt gap**: §3's tree omits `eval/calibration/`, but §6 requires `eval/calibration/labels.csv`
   → I'll add the directory (noted as divergence). §9 is truncated mid-sentence and §10 is missing.
5. **Repo name/visibility**: `nolram/fastapi-rag-eval-harness`, public — confirm.
6. **`n-018`'s `must_not_include` — RESOLVED, and resolved against my own first reading.**
   The judge defended a sentence the label forbids, and my Stage 11 note called the label "probably a
   bug". Audited across the dataset: 10 of the 18 `normal` cases carry a prohibition, and in 4 of them
   (`n-007`, `n-009`, `n-010`, `n-018`) the forbidden string sits in a chunk retrieval offers for that
   query and in none of the expected chunks — always that shape. The label is a guard against retrieval
   noise ("not this question's rule"), not a falsity claim. **Operator decision: keep all 30 labels and
   fix the semantics in the report.** The fix went into the harness, not into prose: the report now
   computes and states the off-scope count every run, the calibration check is named
   `human says off-scope import vs judge supported=false`, and a test locks the four colliding ids so a
   future relabel that erases the collision fails loudly.
