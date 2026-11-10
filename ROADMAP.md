# ROADMAP — `fastapi-rag-eval-harness`

**Where to read this file from:** Stages 0–13 are history and are closed. If you are deciding what to do
next, start at **The objective gap**, then Stage 14.

**Status: Stages 0–13 complete, plus Stage 8.5 — the build is finished and accepted** (bootstrap, corpus,
chunker, BM25 retrieval, `golden.jsonl`, metrics, retrieval eval harness, README + agent dossier, quality
gates, CI, AGENT.md, the FastAPI service, one honest retriever tuning pass, the LLM-as-judge generation
eval, the three ADRs + study guide, and the acceptance run in `docs/acceptance.md`). **`make check` has been
green since Stage 8.5** —
the two Stage 8 gates failed on purpose from Stage 8 until the one-variable tuning pass made them
pass by fixing a real defect, not by moving them.
Implementation was delegated stage-by-stage to subagents and reviewed by the parent before committing;
each stage's gate is recorded below.

**The build is complete. The objective is not met.** Stages 0–13 produced a well-evaluated toy system and a
26-incident diary; the objective stated at the start was to understand **good practices for using LLMs in
day-to-day software development**. Those artefacts contain the raw material for that understanding and do
not contain the understanding. Stages 14–16 exist to close that gap or to say plainly that it cannot be
closed here. The gap itself is documented below, because a plan that does not record why it changed is a
plan that will change again for the same reason.

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

## Stage 12 — ADRs, study guide, README (with real numbers only) — **complete**

Planned and built as specified, with one divergence in provenance rather than in content:

| planned | built | why it differs |
| --- | --- | --- |
| 7 exercises: "the 6 from §8" + "audit an agent against `AGENT.md`" | 7 exercises, defined here | §8 of the source prompt was never recorded in this repo, so its six exercises existed only as a count. The ROADMAP's one surviving hint — that `--k` overrides uniformly, exercise 3 — is honoured. The set was written from the repo's own measured behaviour, which is the honest substitute for a list that was never committed. |
| ADRs and guide delegated to children | ADRs by a child that timed out after delivering them; guide **written by the parent after its child died at the ceiling with zero bytes** | I22. Two children, one shared 30-minute ceiling. |
| README finalisation | 6 corrections, 3 of them **false statements** | "Both targets currently exit non-zero on `main`" and "the retriever misses the gates" had been stale since Stage 8.5, and `2 failed, 86 passed` had been stale since Stage 8. Both were true when written. I19 again, in the file that carries the numbers. |

`docs/adr/` — 0001 (why two harnesses with opposite gating contracts), 0002 (why the labels came first and
why they are frozen), 0003 (why the evals live in-tree and what that costs: `eval/` is linted and **not**
type-checked). Each carries a **Borne** section listing what the decision costs, because a decision record
without costs is marketing. 0003 also caught a rotted claim in `AGENT.md` (I24), now fixed by quoting
commands instead of their output.

`docs/study-guide.md` — 7 exercises, each with Read / Do / What to expect / The trap it teaches. Every
expected output was produced by running the command, including the one where a corpus rename makes both
gates go **up** — `0.8333` to `0.8889` recall and `0.7269` to `0.7824` MRR over `normal`, while the
documentation gets factually wrong. Those two figures are the only numbers this stage quotes that no
committed artefact contains: they are reproducible by the `sed` in exercise 2 and were reverted with
`git checkout -- src/lumen/corpus/`, which is precisely why the exercise is in the guide instead of the
figures being trusted here.

**Gate result:** no figure in the README that cannot be traced to a committed artefact. The one 4-decimal
figure that does not appear in `eval/report.md` is `10.6220`, and it is an artefact of the checking regex —
the first seven characters of `10.622093619866167`, a real `/search` score under `## The API`, reproducible
exactly by `retrieve('how many requests per minute', k=1)`. Study-guide exercise 7 is that investigation.

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

### Stage 13 — complete

**The acceptance run was performed on a fresh clone of the published default branch in `/tmp`, with a venv
built by `make install`** — not in the working directory the build happened in — because every claim this
repo makes is a claim about what a stranger gets. Results, all re-runnable and written up in
`docs/acceptance.md`: `make install` from empty; `make check` green; `make eval-retrieval` exit `0` with
both gates PASS; the regenerated `eval/report.md` **byte-identical** to the committed one
(`md5 593d2b1cb5cdf705e2c8432e0f786e3a`, `git status --short` empty) — I8's determinism claim re-proved
two directories and one venv away from where it was written; the generation eval skipping cleanly with no
key (exit `0`, no report written); and every expected output the study guide promises, re-measured on the
clone (exercise 1's five chunk ids in order, the `grep -c` printing `0`, the whole k-sweep table, 11 README
figures with 0 orphans).

**The I20 debt is paid in both halves.** `tests/test_ci_workflow.py` (10 tests, stdlib only, no PyYAML —
which is not installed and stays uninstalled per `docs/adr/0003`) scans `.github/workflows/*.yml` for the
exact defect and asserts a non-empty `jobs:` mapping. Its value is measured, not assumed: fed the historical
file, `unquoted_colon_names` returns **one hit, line 73 of `08b6e0b:.github/workflows/ci.yml`** and zero on
the current file. The dynamic half was measured against GitHub's API rather than asserted, and it produced
a finding worth keeping — a rejected workflow is reported as a run whose **name is the file path** with
`jobs=0` (run 42755857614), while a real red build has `jobs=1` and the workflow's own name (run
42705424022). That is the "red build vs rejected file" distinction the stage asked for, in one field.

**The final report** is `docs/acceptance.md`: repo URL, the literal per-bucket eval output, the first run
kept red and verbatim (`git show ca3bd59:eval/report.md` — recall@5 `0.7778` FAIL, MRR `0.6713` FAIL,
14/18, left failing for five stages, labels untouched — `git log --oneline -- eval/golden.jsonl` is one
line long), the `git ls-files` tree at 52 files, twelve divergences from the brief with reasons, and the
list of what the run does *not* establish.

**What this stage also cost** is in `docs/agent-dossier.md` as I25 and I26: the guard was written twice,
once by each actor, because I announced a child's death without running the one command that would have
told me it was alive — and its write, five minutes before its death notice, is the version that shipped.
And the dossier's own totals line carried a derivation command that did not derive (`361-301` printed the
minutes added, sitting where `210` minutes lost was claimed), in the file whose thesis is that a number
must name the command that produced it.

---

## The objective gap — why Stages 14–16 exist

**What was asked for:** a senior-level exercise whose subject was how to work well with LLM agents in
ordinary engineering work.

**What was built:** a deterministic retrieval eval, a gated CI, a service, and a chronological incident log.
Good artefacts. Neither is the thing asked for: the log is organised by *when the defect happened*, and what
the objective needs is organisation by *what to do next time*. A reader who wants "how do I run an agent on a
task on Tuesday" finds `I1 … I26` in discovery order and no prescriptions.

**Where the plan, not the execution, failed.** Every gate in this ROADMAP is per-stage — *`make check` green*,
*gates FAIL by design*, *no number without provenance* — and not one is per-learning. The plan measured the
build thoroughly and the objective not at all. That is why thirteen stages could pass every gate and still
miss the target, and it is the single most transferable defect in the file: **an agent will satisfy the
acceptance criteria you wrote, so the acceptance criteria are the whole job.** Adding Stages 14–16 with no
gate that could detect this failure again would be repeating it.

**Measured facts the remaining stages must respect.** Computed from the ledger in
`docs/agent-dossier.md` — every row is a real run with a measured wall-clock time:

```console
$ python3 - <<'PY'
import re
rows=[(int(m.group(1)), int(m.group(4))+int(m.group(5))/60, 'timed out' in l or 'teardown' in l)
      for l in open('docs/agent-dossier.md') if (m:=re.match(r'^\| (\d+) \| (.+?) \| `([0-9a-f]{8})` \| \*?\*?(\d+) m (\d+) s', l))]
for name,lo,hi in [('runs 1-6 (Stages 0-5)',1,6),('runs 7-13 (Stages 6-10)',7,13),('runs 14-19 (Stages 8.5-13)',14,19)]:
    b=[r for r in rows if lo<=r[0]<=hi]; print(f"{name:30} runs={len(b)} avg={sum(r[1] for r in b)/len(b):.1f}m ceilings={sum(1 for r in b if r[2])}")
PY
runs 1-6 (Stages 0-5)          runs=6 avg=13.6m ceilings=0
runs 7-13 (Stages 6-10)        runs=7 avg=18.6m ceilings=2
runs 14-19 (Stages 8.5-13)     runs=6 avg=30.0m ceilings=6
```

Six of six delegated runs completed before Stage 6 closed. **Zero of six completed after Stage 8.5**, and
those last six were briefed with the most discipline in the series: one write target each, pre-set design
decisions, priority ordering inside the file, an explicit validation block. The lever stopped being brief
quality around Stage 8 and became **orientation cost against a fixed ceiling** — reading the repo and
re-validating against a growing suite, which is work, not overhead.

That is a measurement, not a conclusion: n = 19, one model on both sides of every exchange, one operator,
and **no control arm**. It supports a rule of thumb. It cannot support "agents did not help", because the
repo never measured a build done without them.

## Stage 14 — Distil the practices (**A** — the artefact the objective asked for)

`docs/practices.md`, roughly 250–400 lines, 7–9 practices. **Written by the parent, not delegated**, and
that choice is itself evidence: it is judgment over thirteen stages of material only the parent read, and
the table above predicts a child would spend the ceiling reading. If the practices turn out to be true,
this decision should be explicable using them.

Structure per practice — *the rule* (one imperative sentence) · *when it applies* · *the evidence here*
(a commit hash, a command and what it prints, or a ledger row) · *the counter-example from this build* ·
*what obeying it costs*. No section without a cost is marketing and does not ship.

**Entry criterion, enforced by the gate below:** a practice that cannot point to a commit, a command output,
or a ledger row does not enter the file. The rule exists specifically to exclude "my opinion, formatted".

Candidates already paid for by the build:

| practice | the evidence, already in the repo |
| --- | --- |
| Delegate inside the orientation window, not inside the difficulty window | the block table above; runs 9 and 13 (6 m 45 s, and 28 m 34 s — 86 s from death) |
| A death sentence requires a measurement | I25: mtime 14:37:52 against terminal 14:42:59, and my own rule I did not run |
| A ceiling is a scheduling constraint, not an emergency | I17, I22, I25 — 240 m of 391 m; `parallel` shares one ceiling (I22) |
| Digits in prose need a mechanism; otherwise state a shape or a command | I26, `AGENT.md` §7, and the two counts this stage had to correct |
| Freeze the labels, tune the system | `git log --oneline -- eval/golden.jsonl` is one line; five stages of red preserved |
| Scope a gate to the bucket it judges | `--min-recall` on `normal` only; the blended 30-case number moves when the dataset is edited |
| Review budget is real and small | I23: four fluent, wrong incident citations in one draft I had just written |
| Put a verification grep in the brief and the child becomes a checker | I24: one sentence found a defect two green CI runs missed |
| Write the learning gate, not only the build gate | this section, written thirteen stages late |

**Gate.** Every commit hash cited in `docs/practices.md` resolves (`git cat-file -e <hash>`) — the one-liner
is printed in the file itself. Every number is either produced by a command in the same file or is a pointer
to an artefact that owns it; no number is restated. `make check` unaffected (no code changes). `README.md`
gains a pointer, and the study guide gains a cross-reference rather than a copy. Read the file once in full
after writing it: four of my last batch of citations were wrong, and that error class is citation-shaped.

**Out of scope, deliberately:** new code, new corpus documents, new golden cases, new evals. Stage 14 adds
no mechanism. Volume is not the thesis; adding cases to a frozen 30 would weaken the one non-renewable
asset in this repo.

Commit: `docs: the practices, distilled from twenty-six incidents`

## Stage 15 — The baseline experiment (**B** — mapped, not scheduled)

The only item that turns the dossier from descriptive to comparative. It cannot be skipped *silently*: the
question "did delegating help?" is unanswerable here as built, because there is no control arm.

**Question:** on a real day-to-day engineering task, at equal acceptance criteria, does agent delegation
beat non-delegation — and if it loses, where?

**Pre-registered design** (fixed before either arm runs, because a criterion invented afterwards is a
verdict with extra steps): one real task from the operator's actual work, 150–400 lines of expected change,
self-contained, no new dependencies · acceptance criteria written down first, by the operator, without
consulting the parent · two arms — parent writing alone, and parent delegating — on the same task type, same
session length · metrics recorded *at the time*: wall clock, review interventions needed, defects found
after "done", lines changed · one model in both arms, so the comparison is about delegation and not about
the model · order not chosen for convenience.

**The failure mode named in advance:** if the task is invented or resized to suit delegation, the result is
worthless — this is study-guide exercise 2's Goodhart shape wearing a lab coat. The corpus rename that made
both gates go up while the documentation got wrong is the same error as choosing an experiment that cannot
lose. If no real task is available, the honest outcome is *not run*, and Stage 16 says so.

Cost: roughly one day, and it needs the operator to supply both the task and the day. That dependency is
why this is mapped rather than scheduled.

Commit: `docs: a measured comparison, with its criteria fixed in advance`

## Stage 16 — Archive with the learnings (terminal stage)

**Trigger:** Stage 14 is done, and either Stage 15 was not run or its result does not earn a claim.

Archiving here is not deletion. The ledger is the only non-renewable asset in this repo — no. 19 runs with
honest timings cannot be recreated without redoing the build — so everything stays public and read-only.

- A banner at the top of `README.md`: what this repo is, what it is not, and that the primary reading is
  `docs/practices.md`, not the code.
- Reorder the README so **thesis B leads**: how the work was done is the more original subject, and the toy
  RAG is the instrument that made it observable.
- Final ROADMAP line: the objective restated, and met / partially met / not met, with the reason in one
  sentence.
- Closing dossier entry: the last stage's cost, including the cost of the plan itself changing.

Commit: `docs: archive — what was learned, what was measured, what was never measured`

### Definition of done for the whole effort

This is the gate the first thirteen stages lacked. A reader with no memory of the build, one hour, opening
at the README, can answer:

1. Which three practices would they change in how they work with agents — and where is the evidence?
2. Which claims in this repo are **unmeasured**, and do they trust those less?
3. How to reproduce any number they disagree with.

If Stage 14 cannot produce that outcome, the objective was not met, and Stage 16 says so in one sentence
rather than implying it. Archiving honestly is a passing grade; dressing it up is not.

## Standing rules added at the pivot

- No stage adds corpus documents or golden cases. The labels' value is that they are frozen and small.
- Default writer is the parent. Delegate only inside the orientation window, and state the ceiling in the
  brief as a scheduling constraint.
- Every new stage names the gate that would show the *objective* advanced — not only the build.
- A plan change is recorded as a section with its reason, never as a silent edit of the sections above.

---

## Commit sequence (logical, no mega-commit)

`chore: bootstrap` → `feat: corpus` → `feat: chunker` → `feat: BM25` → `eval: golden dataset` →
`eval: metrics` → `eval: retrieval harness` → `feat: fastapi` → `test: gates` → `ci:` →
`docs: AGENT.md` → `eval: generation judge` → `docs: adr/study-guide/readme` → `test: CI workflow guard` →
`docs: acceptance run` → … → **`docs: the practices, distilled` → `docs: a measured comparison` (if run) →
`docs: archive`**

## Standing rules carried across all stages

- Public repo ⇒ zero sensitive data; fictional domain "Lumen"; `.env` ignored, only `.env.example`.
- No paid LLM call from tests or `make check`.
- Never edit `golden.jsonl`, `metrics.py`, or test thresholds to make a check pass.
- Code and docs in English.
- Every derived number states the command that computes it. A "derivation" whose command prints something
  else is I26 and does not ship.
- A stage's gate must be able to detect the objective failing, not only the build failing. Stages 0–13
  could not pass this test; that is the reason Stages 14–16 exist.

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
