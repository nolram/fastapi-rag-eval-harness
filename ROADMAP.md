# ROADMAP — `fastapi-rag-eval-harness`

**Status: Stages 0–3 complete** (bootstrap, corpus, chunker, BM25 retrieval). Stage 4 (`golden.jsonl`)
is next. Implementation was delegated stage-by-stage to subagents and reviewed by the parent before
committing; each stage's gate is recorded below.

Execution plan for the TASK prompt. **Nothing is implemented yet.** Each stage has: goal, files,
commands, exit gate, and commit. Stages are ordered so that the two mandated orderings hold:

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

## Stage 5 — Metrics with abstention-safe semantics

- `eval/metrics.py`: `recall_at_k`, `precision_at_k`, `reciprocal_rank`, `ndcg_at_k`.
  Empty `relevant` ⇒ `None` for Recall/Precision/nDCG, `0.0` for RR.
- Aggregator **excludes `None` from means** and reports the excluded count per metric (never silently zeroes).
- `tests/test_metrics.py`: hand-computed expected values, incl. the `None` path and partial-relevance nDCG.
- Gate: `pytest tests/test_metrics.py` green.
- Commit: `eval: retrieval metrics with abstention-safe aggregation`

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

## Stage 7 — FastAPI service

- `config.py` (pydantic-settings), `schemas.py` (pydantic v2), `prompts.py` (`PROMPT_VERSION` +
  versioned templates), `generation.py` (`answer()`: real iff `LLM_API_KEY`, else explicit stub that
  abstains), `main.py` (`GET /health`, `POST /search`, `POST /ask`).
- Layering: corpus → chunking → retrieval → generation → main.
- `tests/test_api.py` with `TestClient`, `/ask` exercised with LLM **disabled** (no paid call in tests).
- Gate: `pytest tests/test_api.py` green; `make serve` answers the README `curl`.
- Commit: `feat: FastAPI search/ask endpoints`

## Stage 8 — Quality gates as tests

- `tests/test_retrieval.py`: `Recall@5 >= 0.80`, `MRR >= 0.70`, each with the adjacent comment
  *"if this fails, fix the retriever, not the assertion"*; plus a test that **fails on purpose** when
  a critical corpus doc is dropped (monkeypatched corpus) — proof the harness detects regressions.
- Gate: `pytest tests/test_retrieval.py` green **or red**.
- **C3 checkpoint**: if a gate fails, **leave it failing** and explain which cases flipped and why.
  Do not relax thresholds, do not edit golden labels.
- Commit: `test: retrieval quality gates and regression tripwire`

## Stage 9 — CI

- `.github/workflows/ci.yml`: `make check` + `make eval-retrieval`, deps cache (`uv`/pip cache),
  **no LLM step**, no secrets.
- Gate: Actions run green on the pushed branch (or the failure is reported verbatim).
- Commit: `ci: run checks and retrieval eval on every commit`

## Stage 10 — `AGENT.md` (the deliverable you most want to study)

- English, imperative, no marketing. Sections 1–10 exactly as specified: Role · Hard constraints ·
  Repo map (table path → contents → who may import; eval imports src, never reverse) · Conventions ·
  Working protocol · Eval discipline (no blended score; judge is a proxy) · Definition of done ·
  Honest limitations · When to stop and ask · Anti-patterns.
- Plus **exactly one** good-vs-bad agent reply example (short before/after) under §5.
- Gate: every §5 bullet from the prompt present; example count == 1.
- Commit: `docs: AGENT.md working agreement for AI agents`

## Stage 11 — Gated LLM-as-judge generation eval

- `eval/generation_eval.py`: exits 0 with `skipped: no LLM_API_KEY` when key absent.
  Strict JSON rubric `{groundedness, citation_correct, complete, abstained_correctly, verdict, rationale}`;
  `temperature=0`; `model` + `PROMPT_VERSION` recorded in `eval/report.md`.
- Judge prompt forbids external knowledge; grades only against `expected_chunk_ids` /
  `must_include` / `should_abstain`.
- `--calibrate` vs `eval/calibration/labels.csv` (20 rows `case_id,human_verdict,notes`, with real
  human-style justification in `notes`) → prints agreement rate; document that below an acceptable
  agreement the judge is **telemetry, not a gate**.
- Gate: runs offline-safe (skip path) and rubric schema is validated.
- Commit: `eval: gated llm-as-judge generation eval with calibration`

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
   gated skip path + calibration harness (no key used in CI)?
4. **Prompt gap**: §3's tree omits `eval/calibration/`, but §6 requires `eval/calibration/labels.csv`
   → I'll add the directory (noted as divergence). §9 is truncated mid-sentence and §10 is missing.
5. **Repo name/visibility**: `nolram/fastapi-rag-eval-harness`, public — confirm.
