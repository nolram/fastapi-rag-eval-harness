# 0003 — The evals live in this repo and import `lumen` directly

## Status

Accepted — 2026-11-10. Describes `eval/` as it stands from `ca3bd59`
(Stage 6, when the harness first existed) through `d15906c`, including the generation harness added in
Stage 11.

## Context

Every eval decision in [0001](0001-separate-retrieval-and-generation-evals.md) and
[0002](0002-manual-golden-dataset-before-llm-judge.md) — a gate that must be re-runnable in a fifth of a
second, a frozen dataset that is the repo's only human signal, a judge whose numbers cannot be trended —
presupposes one thing: the measurement runs against *this* code, on *this* commit, and produces an
artefact that can be diffed in the same pull request as the change that moved it.

That is not where eval harnesses usually go. The common alternatives are a separate eval repo (a second
checkout, so the number describes a different revision), a published package (a version pinned in a
dependency list, so the number describes an older build), or an eval service (a deployment, which can be
absent — and absence, as this repo has already learned twice, exits `0`).

This repo also has a hard external constraint that makes placement load-bearing rather than stylistic:
the dependency set is frozen at five runtime and three dev packages — `sed -n '/^dependencies/,/^]/p
pyproject.toml` lists the five, and the `dev` extra below it the three — and adding anything is "a
decision you were not given" (`AGENT.md` §2). A hosted eval, a client library, or a scoring server would
each need a dependency and an approval that were never granted.

## Decision

The evals are a directory in this tree, not a fourth thing next to it.

**They are code in the repo, run from the repo root.** `ls eval/` returns `golden.jsonl`, `metrics.py`,
`retrieval_eval.py`, `report.py`, `report.md`, `generation_eval.py`, `generation_report.md`, `README.md`,
`__init__.py`. Both are invoked as modules of that in-tree package — `grep -n 'eval-retrieval\|eval-generation'
Makefile` shows `$(PY) -m eval.retrieval_eval …` and `$(PY) -m eval.generation_eval`. Neither depends on
a server: `grep -n "serve" Makefile` shows `serve` is a separate target with a separate purpose, and
`grep -rn "httpx\|uvicorn" eval/retrieval_eval.py` is empty.

**They are not distributed.** `grep -n -A2 "packages.find" pyproject.toml` returns `where = ["src"]` and
`include = ["lumen*"]`, so `eval` is absent from the built distribution and there is no eval version to
be stale. The corollary is that `python -m eval.retrieval_eval` only resolves from the repo root, which
is exactly where `Makefile` runs it from.

**They import the library directly, in one direction only.** `eval/retrieval_eval.py:29-30` imports
`load_corpus` and `retrieve` from `lumen.chunking` / `lumen.retrieval`, and `eval/generation_eval.py:36-40`
imports `config`, `generation`, `prompts`, `retrieval` — the same `answer()` the FastAPI service calls,
not an HTTP copy of it. `grep -rn "from eval\|import eval" src/` prints nothing. `AGENT.md` §3 states the
rule and its reason: "the library must not know the eval exists."

**Tests may import the harness, and do.** `grep -rn "from eval\|import eval" src/` is empty — that is the
rule. `grep -rln "^from eval\|^import eval" tests/ --include='*.py'` names three test files, and a fourth
(`tests/test_api.py`) mentions `eval/report.md` in a docstring to cite a threshold value rather than guess
it. A suite that could
not import the harness could not assert on it: `grep -n "GATE_MIN_RECALL" tests/test_retrieval.py` finds
`GATE_MIN_RECALL = 0.80` feeding the Stage 8 gate assertions, whose own comment reads "If this fails, fix
the retriever. Not this assertion, not the 0.80, not golden.jsonl."

**The artefacts are committed, so they are reviewable.** `.gitignore` carries the comment "eval/report.md
is committed on purpose, caches are not", and the history shows what that buys: `git show --stat
--format='%h %ad %s' --date=short 97867a2` lists `src/lumen/retrieval.py | 210 +…` and
`eval/report.md | 108 +…` in one commit. The tokenizer change and the movement it caused in the measured
report are the same logical change, reviewed together.

**CI runs the same targets a human runs**, by name — `grep -n 'name:' .github/workflows/ci.yml` returns
`make check (ruff + mypy --strict + pytest)`, `make eval-retrieval (golden-set gates, rewrites
eval/report.md)`, `make eval-generation (no key, so it must skip with exit 0)` — installed via
`make install`, and it uploads `eval/report.md` as a build artefact. No second eval path exists to drift.

## Consequences

**Accepted.**

- Provenance is one command deep. Every figure in `README.md` is regenerable on the current commit by
  `.venv/bin/python -m eval.retrieval_eval --golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr
  0.70` — which is also how the `AGENT.md` rule "every 4-decimal figure in `README.md` appears verbatim in
  `eval/report.md`" stays enforceable at all.
- There is no second implementation of retrieval to drift out of sync, because there is no second
  implementation: the eval and `/search` call the same `retrieve()`.
- The gate can stay hostile, because it is cheap and local. A red `make eval-retrieval` costs nothing but
  the second it takes (measured on this checkout at `real 0m0.054s`, warm cache — quote the order of
  magnitude, not the hundredths), so the repo could afford to leave
  both gates failing through five stages (`git show ca3bd59:eval/report.md` prints both `FAIL` lines).
- A harness bug and a product bug surface in the same review. Stage 8.5's commit `97867a2` carries the
  fix, the tests, and the regenerated report; nothing had to be re-deployed for the numbers to be true.

**Borne.**

- **`make typecheck` does not cover `eval/`.** `grep -n 'typecheck' Makefile` returns the recipe
  `.venv/bin/mypy` — no paths — and the scope therefore comes from `pyproject.toml`'s `[tool.mypy]` block,
  `files = ["src"]`. On this checkout `make typecheck` prints `Success: no issues found in 8 source
  files`, and `ls src/lumen/*.py | wc -l` is `8`: it is checking the package and nothing else. The five
  files under `eval/` are type-checked by no target and no CI step. They pass today —
  `.venv/bin/mypy --cache-dir /tmp/mypy-eval-check eval/` prints `Success: no issues found in 5 source
  files` — but that is a manual invocation I ran while writing this, not a control. The next untyped edit
  to `eval/retrieval_eval.py` reaches `main` green.
- The asymmetry is sharper than it looks, because `eval/` *is* linted: `lint:` runs `ruff format --check .`
  and `ruff check .` over the whole tree (`grep -n "ruff" Makefile`). So `eval/` is held to 100 columns and
  rejected for an unused import, while its type annotations are unenforced — and its importers are the
  test suite, i.e. the thing that is supposed to catch regressions.
- Extending mypy to `eval/` is not an agent's edit to make. It means changing `files` in
  `pyproject.toml`, and `AGENT.md` §2 makes any `pyproject.toml` diff "a decision you were not given."
- The gate threshold now lives in two places that must be kept in agreement by hand:
  `grep -n 'min-recall' Makefile` gives `--min-recall 0.80 --min-mrr 0.70` and
  `grep -n "GATE_MIN_RECALL" tests/test_retrieval.py` gives `GATE_MIN_RECALL = 0.80`. Nothing in
  `make check` compares them; a one-sided edit makes the test suite gate on a number CI no longer applies.
- Committing generated artefacts means the tree can hold a stale one. `eval/report.md` is only as current
  as the last run of `make eval-retrieval`, and `eval/generation_report.md` is a record of one live run at
  one endpoint that no check will tell you is old. `AGENT.md` §8 concedes the same shape: prose drift
  passes CI except for the one line the workflow actually compares — `grep -n 'name:'
  .github/workflows/ci.yml` shows that step is "Docs consistency (README test count vs pytest collection)".
- The evals are unusable outside this checkout, by design. There is no installable way to run them, so a
  team that wants the same harness on another codebase copies files rather than importing a package.
- The working agreement used to quote its own import-direction grep as three `path:line:` pairs. Stage 11
  added `tests/test_generation_eval.py`, the real output became eight lines, and the pasted block kept
  asserting three — the rule never broke, only the snapshot of it (`docs/agent-dossier.md` I7, I19), in
  the one file whose purpose is preventing that failure. Fixed as of this commit by quoting commands whose
  output is stable — the `src/`-only grep plus a sorted list of file names — instead of a transcript.

## What this looked like without the constraint

The alternative that costs nothing up front is the separate eval repo. It removes the import-direction
rule entirely, and with it the reason the rule exists: the harness would import a published `lumen`, or
call `POST /search` over HTTP, and every number would describe a build someone else picked. `97867a2` —
the one commit where a tokenizer change moved the gates from `0.7778` / `0.6713` to `0.8333` / `0.7269` —
could not have carried its own report with it, and Stage 8.5's finding ("one case flipped, none
regressed, three still miss") would have been a claim about an endpoint rather than about `tokenize()`.

The service-shaped version is worse in a way this repo has measured twice. An eval that runs as a
deployment produces a green exit when the deployment is simply not there. `docs/agent-dossier.md` I14 is
exactly that: a script that exited `0` because the file it executed was empty. The live generation harness
already carries that hazard, and the mitigation is in-repo and explicit — with no `LLM_API_KEY` it prints
one line and exits `0` (`grep -n "SKIPPED_LINE" eval/generation_eval.py`), and CI's step name says so:
`make eval-generation (no key, so it must skip with exit 0)`. That mitigation works only because the skip
is a *named, visible* step in the same workflow that gates on the rest. Move the harness behind a service
and the skip stops being a step name and becomes an absence nobody notices.

## Reconsider when

- The eval has to run against a deployed system rather than the library — an external corpus, a hosted
  ranker, a vendor API. Then the eval must speak HTTP, and this ADR's central premise (same process,
  same code) is gone: write it as a separate decision, do not quietly add an `httpx` call to the
  deterministic harness, whose networklessness is the whole reason it gates.
- A second repo needs the same harness. Package it then — which requires the frozen dependency set and
  `[tool.setuptools]` to change, i.e. a human edit with a reason attached.
- Someone is willing to own `files = ["src", "eval"]` in `pyproject.toml`. This ADR asks for it, since the
  cost above is unenforced typing on the code that produces every quotable number; it is not a change an
  agent should make unilaterally.
- A `secrets.` reference appears in `.github/workflows/ci.yml` (`git grep -n "secrets\." -- .github` must
  stay empty). The moment CI holds a key, "the eval is local and cheap" stops being the rule and needs a
  successor decision about cost, rate limits, and what a flaky provider does to a merge.
