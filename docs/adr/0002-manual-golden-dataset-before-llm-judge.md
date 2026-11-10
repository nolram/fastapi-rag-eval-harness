# 0002 — Hand-label the golden dataset before any LLM judge exists

## Status

Accepted — 2026-11-10. Describes `eval/golden.jsonl` as committed at
`af28c16` (2026-11-09) and still unchanged at `d15906c`, and the LLM judge added in Stage 11
(`286890b` and the two commits after it).

## Context

A RAG eval needs a human opinion somewhere in it, or every number it prints is the system grading
itself. This repo has exactly one human opinion, written once, early: `eval/golden.jsonl` — 30 cases,
18 `normal` / 6 `ambiguous` / 6 `abstention`, each carrying `expected_chunk_ids`, `must_include`,
`must_not_include`, `should_abstain`.

Its value comes from when it was written, not from its size. Stage 4 is the only stage in `ROADMAP.md`
whose heading carries a warning: `grep -n '^## Stage' ROADMAP.md | grep ⚠` returns one line of the 17
stage headings,
`## Stage 4 — eval/golden.jsonl (**before** any tuning) ⚠ mandated ordering`, and the stage's own gate
is "**No BM25 parameter tuning in this stage**." The parent's audit of the child that wrote it is in the
same section: *0 of its 12 commands imported or ran `lumen.retrieval`.*

The ordering is checkable from history. `git log --oneline --date=short --format='%h %ad %s' --
eval/golden.jsonl src/lumen/chunking.py src/lumen/retrieval.py | head -12` prints, newest first:

```text
97867a2 2026-11-10 feat: fold inflectional suffixes so BM25 has morphology without a lexicon
af28c16 2026-11-09 eval: hand-labelled golden dataset (pre-tuning)
b34f5f1 2026-11-09 feat: pure-python BM25 retrieval
fbcadf8 2026-11-09 feat: heading-aware markdown chunker
```

The chunker and the ranker exist before the labels, because the labels had to name real chunk ids. What
the labels precede is the tuning: `git log --oneline af28c16..HEAD -- src/lumen/chunking.py
src/lumen/retrieval.py` returns exactly one commit, `97867a2`. That is the suffix-folding change that
moved the gates from `0.7778` / `0.6713` to `0.8333` / `0.7269` (`eval/README.md`, "Stage 6 and Stage 8.5
outcome, as measured") — and it moved them **against a dataset that never moved.**

The LLM judge arrived two stages later, in Stage 11. The decision this records is that it did not
become a labeller.

## Decision

**The labels are the only human signal, they were written before tuning, and they are frozen.**
`git log --oneline --date=short --format='%h %ad %s' -- eval/golden.jsonl` returns **one** line,
`af28c16`. Fourteen delegated runs later, the file has never been edited — not to fix a case, not to
erase a disagreement, not once. Its shape is one command deep: `grep -c . eval/golden.jsonl` prints `30`,
and `grep -o '"type":"[a-z]*"' eval/golden.jsonl | sort | uniq -c` prints `18 normal`, `6 ambiguous`,
`6 abstention`. `AGENT.md`'s read-only rule (`grep -n "two gate thresholds" AGENT.md`, one hit) makes it and
`eval/metrics.py` read-only for agents;
`ROADMAP.md`'s preamble states the payoff: "Golden labels were written without ever running the ranker
(audited), so a Stage 6 failure is a retriever miss, not a labelling artifact."

**The harness refuses the cheap way out.** `grep -n "problems.append" eval/retrieval_eval.py` lists the
validator's refusals — a chunk id that does not exist in the corpus, a duplicate case id, a drift from
the 18/6/6 split, an `abstention` case that expects a chunk or omits `should_abstain: true` — all
collected in one pass and raised as exit `2` before any metric is computed. Re-typing a failing case as
`abstention` to dodge a gate is not discouraged, it is rejected.

**The judge exists, and never sees a label.** `grep -n "JUDGE_TEMPLATE.format" eval/generation_eval.py`
returns one line, and its three substitutions are `question`, `context`, `answer`. The judge is asked
two questions about that — `supported` and `should_have_refused` — and a reply whose keys are not
exactly `{supported, should_have_refused, reason}` (`JUDGE_KEYS`, `eval/generation_eval.py:56`) is a
judge error: counted, named, and removed from the denominator, never re-counted as `supported: false`.

**Calibration is the judge measured against the human labels, in the two places only where a human
label actually means something.** `grep -n "human says" eval/generation_report.md` shows both rows: the
6 `should_abstain` cases against `should_have_refused` (agreement `1.0000`, `6/6`), and a violated
`must_not_include` against `supported: false` (`0/1`). Every disagreement prints the case id with both
readings side by side, so a reader can go and look.

**There is no second label file.** The Stage 0 plan called for `eval/calibration/labels.csv` with 20
rows of human verdicts; `ls eval/calibration` prints `No such file or directory`, and the Stage 11
planned-vs-built table in `ROADMAP.md` records the substitution and its reason: a second hand-label file
would be a second place for the same opinion to rot.

## Consequences

**Accepted.**

- The labels are evidence that could not have been produced later. The single strongest result in the
  repo depends on it: the retrieval harness fails `n-007`, `n-015`, `n-016`
  (`.venv/bin/python -m eval.retrieval_eval | grep "| n-" | grep FAIL`) and the generation harness, which
  cannot read those numbers, independently abstains on `n-007` and `n-015` and gets `0.0000` coverage on
  `n-016` (`grep -n "n-007\|n-015\|n-016" eval/generation_report.md`). Two instruments agreeing on three
  case ids is a finding; one instrument tuning its own inputs is a tautology.
- A judge verdict has something to be wrong against. `n-018` is a *disagreement* precisely because a
  human said "not this question's rule" and the judge said "not false either"; with no human label the
  judge's `supported: true` is the whole of the record.
- Retuning the retriever is safe. Stage 8.5 changed the tokenizer and the gates moved on their own
  (`eval/README.md`: one case flipped, none regressed) — a comparison that only means something because
  the ruler did not move with the object.
- The gate rule and the dataset share a provenance: `ROADMAP.md` fixes the `--min-recall 0.80` /
  `--min-mrr 0.70` thresholds and their `normal`-bucket scope under "decided before any number was
  computed", and those are the exact strings in `grep -n 'min-recall' Makefile`.

**Borne.**

- The non-renewable asset is 30 cases. `README.md` says so in its own blockquote: *30 hand-labelled
  cases on a 12-document toy corpus are for regression detection, not a quality claim.* Recall@5 `0.8333`
  is a statement about the harness's eyesight, and re-labelling at scale is expensive enough that the
  set does not grow — so the three retrieval misses `n-007`, `n-015`, `n-016` have stayed in it since
  Stage 6 as evidence rather than been labelled away or expanded away.
- The calibration denominators are tiny by construction: `6/6` and `0/1`. `eval/README.md` ("Technical
  debt this stage leaves open") states that these figures describe the run and rank nothing. One more
  hand-labelled disagreement would double the second denominator, and it has not been paid for.
- Because the labels are frozen and hand-written, one reading of "which chunk answers this" is
  load-bearing and unreplicated. `grep -rn "annotator" --include='*.py' src/ eval/ tests/` is empty:
  there is no annotation tooling and no second label set anywhere in the code, so no agreement rate can
  be computed — `eval/golden.jsonl` is the only place the human judgement is recorded.
- `must_not_include` conflates two things in one field — off-scope import versus contradiction — and
  `eval/README.md` records the split (`contradicts_docs` vs `off_scope_terms`) as open debt. Only 1 of
  the 4 off-scope guards fired in the recorded run, so 3 are latent landmines in a frozen file.
- Read-only is a rule, not a permission: `AGENT.md` §8 admits it "cannot make a read-only file physically
  read-only." The only control is `git diff --name-only` in review, and the file is 30 lines of JSON —
  a plausible-looking edit is one keystroke away.

## What this looked like without the constraint

`docs/agent-dossier.md` I21 is the experiment run in the wrong order. The generation eval produced one
label/judge disagreement — `n-018` — and the parent inferred from it that the label was the suspect one
and the judge the likelier one, and wrote that into three places. It is still in the commit message:
`git show -s --format=%B 286890b | grep -n "bad label"` prints
`most likely a bad label, left unfixed because re-labelling to satisfy a model is how a golden set
becomes a mirror`. The same paragraph reached `eval/README.md` and an open question in `ROADMAP.md`,
where item 6 now reads "`n-018`'s `must_not_include` — RESOLVED, and resolved against my own first
reading" (`grep -n "n-018" ROADMAP.md`). Read `git show 286890b` — the sentence is in public history and
stays there, because corrections go forward.

The next morning the operator asked what exactly needed deciding, and answering it required the audit
that had been skipped: check *all* the `normal` cases carrying a prohibition for whether the forbidden
string appears in the context their own query retrieves. Four of the ten do, and all four have the same
shape. The conclusion inverted — the label and the judge were never in conflict — and it was reached with
no API call, no key, no model, against a file of 30 lines written a fortnight earlier. **The human label
was the instrument; the judge was the thing being measured.**

The fix went into the tool rather than the prose, and it is why this ADR is about a dataset rather than
about a mistake: `grep -n "off.scope" eval/generation_report.md` shows the harness now recomputing and
printing the count every run ("4 of the 10 `normal` cases carrying a prohibition have that exact string
present in the offered context and in none of their expected chunks"), and
`grep -n "n-009" tests/test_generation_eval.py` finds `assert off_scope == {"n-007", "n-009", "n-010",
"n-018"}` — so editing a label to make a disagreement disappear now fails a test instead of quietly
winning an argument.

Had Stage 11 shipped the planned `eval/calibration/labels.csv`, the disagreement would have been
resolved by consulting a second human-shaped file, and the audit would have compared two guesses against
each other. That is the version where the loop closes and nothing outside the system can say no.

## Reconsider when

- The dataset needs to grow past hand-labelling — then split the schema first (`contradicts_docs` vs
  `off_scope_terms`, already recorded as debt), or every new case inherits the conflation.
- A second model is configured via `LLM_JUDGE_MODEL` and someone actually runs the two-model comparison;
  nothing in this repo has ever run it, so `supported: true` is still a model grading its own output.
- Judge–human disagreement stops being rare enough to read case by case. The rule today is "read the id
  list"; past a dozen rows per run, the calibration section needs a different shape than a table.
- A second human annotator appears. Two independent label sets would let the repo state an agreement
  rate instead of asking readers to trust one.
