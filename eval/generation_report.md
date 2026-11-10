# Lumen generation eval

- Model that answered: `qwen3.8-flash-next-iq3_xxs` — mode `live`, `lumen.generation.answer()`, prompt_version `v1`.
- Model that judged: `qwen3.8-flash-next-iq3_xxs` — `eval.generation_eval.ask_judge`, JUDGE_PROMPT_VERSION `v1`.
- **The same model answered and judged** (`qwen3.8-flash-next-iq3_xxs`, one endpoint). Every judge number below is a model grading its own output, so self-preference bias is a live configuration of this harness and not a footnote: read `supported` as that model's opinion of its own answer, never as correctness. Set `LLM_JUDGE_MODEL` to break the tie.
- **The judge never sees a golden label.** Its prompt carries the question, the retrieved context and the candidate answer, and nothing else. `eval/golden.jsonl` is human-authored and is the only independent signal here, so judge and golden stay two measurements, put side by side only in the calibration section.
- **Nothing in this file is a gate.** No threshold, no `--min-*` flag, no PASS/FAIL and no exit code for a bad number: Stage 11 ships the measurement, and a threshold picked before this distribution was seen is the anti-pattern `AGENT.md` records as I5.
- No `LLM_API_KEY` value appears in this file, in stdout, or in any error message.
- Golden file: `eval/golden.jsonl` — 30 cases (18 `normal`, 6 `ambiguous`, 6 `abstention`).
- Scoring k: each case's own `k` from the golden file (values present: 5).
- Command: `python -m eval.generation_eval --golden eval/golden.jsonl --report eval/generation_report.md`

## Deterministic metrics per case type

Computed from the answer text and the labels alone, with no judge involved.

| bucket | cases | must_include coverage | abstention match | context offered | context cited |
| --- | --- | --- | --- | --- | --- |
| normal | 18 | 0.7500 (18/18) | 0.8889 (18/18) | 5.0000 (18/18) | 1.1667 (18/18) |
| ambiguous | 6 | n/a (0/6) | 1.0000 (6/6) | 5.0000 (6/6) | 2.0000 (6/6) |
| abstention | 6 | n/a (0/6) | 1.0000 (6/6) | 5.0000 (6/6) | 0.0000 (6/6) |

- **`must_include coverage`** is the share of a case's `must_include` strings present in the answer, matched as case-insensitive substrings. A case that lists none — all six `abstention` and all six `ambiguous` cases do — contributes `None`, not `0.0`, which is why the `(included/total)` count travels with every mean.
- **`abstention match`** is `answer.abstained == should_abstain`, per bucket, never blended: the cases labelled `should_abstain` and the answerable ones ask different questions, and a mean over both moves when the dataset's composition changes rather than when the answerer does.
- **`answer_cites_nothing` — the "looks grounded, is not" detector.** A non-abstaining answer that names no chunk id. Per bucket: normal 0 · ambiguous 0 · abstention 0.
- **`cited_but_not_retrieved`** — a cited chunk id retrieval never offered. Per bucket: normal 0 · ambiguous 0 · abstention 0. This is `0` today by construction, because `lumen.generation` extracts citations from the retrieved list only; it is reported anyway, as the tripwire for the day citation extraction gets smarter than that.
- **`context offered` vs `context cited`** — the mean number of chunk ids retrieval put in front of the answerer against the mean number the answer actually named; the gap is how much offered context went unused.
- Cases outside a mean: `normal`: answer errors 0, judge errors 0; `ambiguous`: answer errors 0, judge errors 0; `abstention`: answer errors 0, judge errors 0. A provider failure on one case is a hole in the measurement, not a crash: the case is named here and leaves every denominator that needs answer text.

## Judge verdicts per case type

One `httpx` POST per case, `temperature: 0`, no `max_tokens`, expecting `{"supported": bool, "should_have_refused": bool, "reason": str}`.

| bucket | cases | judged | judge errors | supported true | supported false | should_have_refused true | should_have_refused false |
| --- | --- | --- | --- | --- | --- | --- | --- |
| normal | 18 | 18 | 0 | 18 | 0 | 2 | 16 |
| ambiguous | 6 | 6 | 0 | 6 | 0 | 0 | 6 |
| abstention | 6 | 6 | 0 | 6 | 0 | 6 | 0 |

- `judged + judge errors = cases`. A reply that did not parse is a judge error: the case is counted, named above, and excluded from every judge denominator. It is never recounted as `supported: false`, because "the judge could not be read" is not a verdict.
- A case the answerer failed to answer occupies a judge slot too, so the two id lists above overlap by construction: one upstream failure is one failure, counted once as an answer error and once as an ungradeable answer. Only the judge errors that do not appear among the answer errors are faults of the judge itself.
- `supported` and `should_have_refused` are two independent opinions — one about this answer, one about the question. Neither column is a score.

## Calibration: the judge measured against the golden labels

- **This is not accuracy.** The judge shares a model with the answerer, so an agreement here can be two systems agreeing with each other rather than with the world, and the golden set is 30 hand-labelled items, so each denominator below is a number that one edited case would move. The table describes this run; the disagreements underneath it are the part worth reading.
- 6 cases are labelled `should_abstain`, which is the entire denominator of the first check.
- 1 `normal` cases had an answer that contained one of their `must_not_include` strings — the only cases where the human label says "this answer is wrong" and the judge has something to agree or disagree with. A clean answer to a `must_not_include` case is not evidence either way and is not counted.

| check | agreement | agreed / compared | excluded (no usable judge verdict) |
| --- | --- | --- | --- |
| human says refuse vs judge should_have_refused | 1.0000 | 6/6 | 0 |
| human says must_not_include violated vs judge supported=false | 0.0000 | 0/1 | 0 |

### Disagreements (1) — read them, do not average them

| id | type | check | human label | judge verdict | judge reason |
| --- | --- | --- | --- | --- | --- |
| n-018 | normal | human says must_not_include violated vs judge supported=false | violates must_not_include: at most 3 retries | supported=True | The context states that 503 is retryable and always includes Retry-After: 30, and that retrying a write with the same Idempotency-Key cannot duplicate the resource because the stored response is replayable for 24 hours. It also specifies using at most 3 retries and reusing the same key. |

## Per-case results

Sorted by case id, so two runs of the same dataset put the same row in the same place.

| id | type | abstained | should_abstain | must_include coverage | cited ids | judge supported |
| --- | --- | --- | --- | --- | --- | --- |
| a-001 | abstention | yes | yes | n/a | (none) | yes |
| a-002 | abstention | yes | yes | n/a | (none) | yes |
| a-003 | abstention | yes | yes | n/a | (none) | yes |
| a-004 | abstention | yes | yes | n/a | (none) | yes |
| a-005 | abstention | yes | yes | n/a | (none) | yes |
| a-006 | abstention | yes | yes | n/a | (none) | yes |
| m-001 | ambiguous | no | no | n/a | guidelines_brazil#1 guidelines_us#1 | yes |
| m-002 | ambiguous | no | no | n/a | guidelines_brazil#2 guidelines_us#2 | yes |
| m-003 | ambiguous | no | no | n/a | guidelines_brazil#3 guidelines_us#3 | yes |
| m-004 | ambiguous | no | no | n/a | guidelines_brazil#4 guidelines_us#4 | yes |
| m-005 | ambiguous | no | no | n/a | guidelines_brazil#5 guidelines_us#5 | yes |
| m-006 | ambiguous | no | no | n/a | guidelines_brazil#6 guidelines_us#6 | yes |
| n-001 | normal | no | no | 1.0000 | rate_limits#1 | yes |
| n-002 | normal | no | no | 1.0000 | auth#4 | yes |
| n-003 | normal | no | no | 1.0000 | data_retention#3 | yes |
| n-004 | normal | no | no | 1.0000 | errors#4 | yes |
| n-005 | normal | no | no | 1.0000 | idempotency#3 idempotency#1 errors#2 | yes |
| n-006 | normal | no | no | 0.5000 | pagination#1 | yes |
| n-007 | normal | yes | no | 0.0000 | (none) | yes |
| n-008 | normal | no | no | 1.0000 | sla#1 sla#4 | yes |
| n-009 | normal | no | no | 1.0000 | versioning#2 | yes |
| n-010 | normal | no | no | 1.0000 | webhooks#2 | yes |
| n-011 | normal | no | no | 1.0000 | guidelines_us#7 | yes |
| n-012 | normal | no | no | 1.0000 | guidelines_brazil#7 | yes |
| n-013 | normal | no | no | 0.5000 | rate_limits#5 | yes |
| n-014 | normal | no | no | 1.0000 | pagination#4 | yes |
| n-015 | normal | yes | no | 0.0000 | (none) | yes |
| n-016 | normal | no | no | 0.0000 | versioning#2 | yes |
| n-017 | normal | no | no | 0.5000 | versioning#3 | yes |
| n-018 | normal | no | no | 1.0000 | errors#5 idempotency#1 idempotency#3 | yes |

