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

Each gate is measured over the `normal` bucket only, and the bucket name is printed on its line for that reason. Blending the three case types into one number is what this repo exists to refuse: the abstention cases contribute `None` recall and `0.0` reciprocal rank by construction, and the ambiguous cases have two ground-truth chunks where a normal case has one. A `FAIL` on any line above makes the CLI exit 1, which is what blocks CI.

If a gate fails, the thing to change is the retriever — not the threshold, not the golden labels, not this report.

## Aggregates per case type

| bucket | cases | pass | recall@5 | precision@5 | mrr | nDCG@5 |
| --- | --- | --- | --- | --- | --- | --- |
| normal (gated) | 18 | 15/18 | 0.8333 (18/18) | 0.1667 (18/18) | 0.7269 (18/18) | 0.7534 (18/18) |
| ambiguous | 6 | 6/6 | 1.0000 (6/6) | 0.4000 (6/6) | 1.0000 (6/6) | 1.0000 (6/6) |
| abstention | 6 | n/a | n/a (0/6) | n/a (0/6) | 0.0000 (6/6) | n/a (0/6) |

- The `(included/total)` count after every mean is how many cases that mean actually covers. `mean_excluding_none` refuses to turn `None` into `0.0`, so `n/a (0/6)` means "no case in this bucket has a value", not "the score is zero".
- `pass` is not one definition across rows. A `normal` case passes when its one expected chunk is in the top k. An `ambiguous` case passes only when **both** market chunks are in the top k — the markets are near-tied on purpose, so retrieving one half of a contradiction is not a win. An `abstention` case has **no** verdict: whether it *should* abstain depends on a score threshold that is not fixed, so it is judged only by the sweep below and never enters a gate.
- The `abstention` row has no recall, precision or nDCG at all, and every `mrr` in it is `0.0`. Its real signal is the `top_score` distribution below — which is why MRR is reported next to the score distribution and never alone.

### Global line — all 30 cases blended, **not a gate**

```text
recall@5 0.8750 (24/30) · precision@5 0.2250 (24/30) · mrr 0.6361 (30/30) · nDCG@5 0.8151 (24/30)
```

Printed for transparency only, and diluted by construction: it blends 18 single-answer cases with 6 two-answer cases and 6 cases with no answer at all, so its recall is computed over fewer cases than its MRR, and its MRR carries six structural `0.0`s. It is not comparable between datasets and it is never the gate — the `normal` row above is.

## Abstention analysis

### `top_score` distribution — 6 abstention cases

These cases have no ground truth, so all the retriever can say about them is how loudly it matched *something*.

| case | top_score |
| --- | --- |
| a-001 | 4.3303 |
| a-004 | 5.4295 |
| a-006 | 5.6277 |
| a-005 | 5.6790 |
| a-003 | 5.7296 |
| a-002 | 9.2034 |

- min 4.3303 · median 5.6534 · max 9.2034 · cases with `top_score is None`: 0/6
- Not one case scored `None`: every one of these queries shares at least one non-stopword term with some chunk, so the retriever always returns *something* and the raw `top_score` carries the entire abstention decision.

### Threshold sweep — t from 0.5 to 4.0, step 0.5

A threshold `t` makes the system abstain when `top_score < t`; otherwise it truncates the retrieved list to the chunks scoring `>= t`. The two recall columns are recall@k recomputed on that truncated list — what the answer can still see after the cut, the cost side of the same knob.

| t | abstentions covered | normal lost | ambiguous lost | recall@5 normal (truncated) | recall@5 ambiguous (truncated) | note |
| --- | --- | --- | --- | --- | --- | --- |
| 0.5 | 0/6 | 0/18 | 0/6 | 0.8333 (18/18) | 1.0000 (6/6) |  |
| 1.0 | 0/6 | 0/18 | 0/6 | 0.8333 (18/18) | 1.0000 (6/6) |  |
| 1.5 | 0/6 | 0/18 | 0/6 | 0.8333 (18/18) | 1.0000 (6/6) |  |
| 2.0 | 0/6 | 0/18 | 0/6 | 0.8333 (18/18) | 1.0000 (6/6) |  |
| 2.5 | 0/6 | 0/18 | 0/6 | 0.8333 (18/18) | 1.0000 (6/6) |  |
| 3.0 | 0/6 | 0/18 | 0/6 | 0.8333 (18/18) | 1.0000 (6/6) |  |
| 3.5 | 0/6 | 0/18 | 0/6 | 0.8333 (18/18) | 1.0000 (6/6) |  |
| 4.0 | 0/6 | 0/18 | 0/6 | 0.7222 (18/18) | 1.0000 (6/6) |  |

- No threshold in the sweep covers all 6 abstentions: at `t = 4.0` only 0 of 6 are covered, and recall@5 over `normal` is unchanged at 0.7222.
- Covering all 6 would need `t > 9.2034` (the highest abstention `top_score`, case `a-002`), above the top of this sweep — and that cut kills 10 of 18 `normal` cases (55.6%) and 3 of 6 `ambiguous` ones.
- No `t` in this range saves an abstention without also costing an answerable case: every threshold that does something does damage.

**Why a raw-score threshold is fragile.** The 6 abstention cases score 4.3303 to 9.2034, while the 18 answerable `normal` cases score 4.5297 to 20.0727. The two ranges overlap on [4.5297, 9.2034]: 5 of 6 abstention cases score at or above the *weakest* normal case, and 10 of 18 normal cases score below the *strongest* abstention case. No value of `t` separates the two groups: the scores that mean "not in the corpus" sit inside the same interval as the scores that mean "here is the answer". The cheapest threshold that would cover every abstention, `t > 9.2034`, removes the answer from 10 of 18 normal cases (55.6% of the questions the system can currently answer) — it trades a wrong answer for 10 refusals to answer. And that cut would not survive contact with the corpus: BM25 here is an unbounded sum of idf-weighted term scores, `idf = log(1 + (N - df + 0.5) / (df + 0.5))` over N = 64 chunks, so a score grows with query length and term frequency, shrinks as a term appears in more chunks, and moves whenever the corpus grows, a document is edited or the tokenizer changes. `t = 1.5` is a property of *this* corpus and *this* tokenizer, not a safety property: the same constant means something else once the corpus changes, and re-tuning it against these labels turns the abstention rule into a parameter of the eval itself. A normalised signal — the margin between the top two scores, or a classifier judged on labels — is the version of this decision that transfers.

## Per-case results

`must_include` / `must_not_include` are **informational** in this report. They are matched case-insensitively against the concatenation of the retrieved chunk texts and printed in the last column only: they constrain the *answer* (the generation eval), not retrieval, so a retrieved chunk that happens to contain a `must_not_include` token is not a retrieval failure. They never change `pass` and never move a gate.

| id | type | pass | expected | retrieved_ids | top_score | recall@5 | precision@5 | rr | nDCG@5 | must_include / must_not_include (informational) | query |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| n-001 | normal | PASS | rate_limits#1 | rate_limits#1 rate_limits#5 rate_limits#2 rate_limits#4 pagination#1 | 14.1536 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | What is the default rate limit for the API? |
| n-002 | normal | PASS | auth#4 | auth#4 auth#1 auth#3 idempotency#1 idempotency#2 | 5.4942 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | Something is brute-forcing my credentials and the key is now blocked. How long until I can try again? |
| n-003 | normal | PASS | data_retention#3 | auth#5 data_retention#5 refunds#5 data_retention#3 sla#3 | 4.5927 | 1.0000 | 0.2000 | 0.2500 | 0.4307 | ok | How far back could we roll the database if someone dropped a production table? |
| n-004 | normal | PASS | errors#4 | data_retention#4 rate_limits#3 errors#4 guidelines_us#2 sla#2 | 4.5297 | 1.0000 | 0.2000 | 0.3333 | 0.5000 | ok | The API said my payload was invalid but only showed a few problems. Is there a cap on how many it reports? |
| n-005 | normal | PASS | idempotency#3 | idempotency#3 idempotency#1 errors#1 webhooks#3 errors#2 | 13.5246 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | Is it safe for my worker to resend a write that timed out, and which status codes must it never retry? |
| n-006 | normal | PASS | pagination#1 | pagination#1 sla#5 data_retention#2 data_retention#1 auth#3 | 15.5024 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | How many records do I get from a list endpoint if I don't ask for anything, and how do I fetch the next one? |
| n-007 | normal | FAIL | refunds#3 | refunds#1 webhooks#4 data_retention#5 idempotency#5 refunds#4 | 5.1948 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | missing must_include: 3% administrative deduction, pro-rata; found must_not_include: refunded in full | Do you keep part of the money when a policy is cancelled later than two weeks in? |
| n-008 | normal | PASS | sla#1 | sla#1 sla#4 rate_limits#5 rate_limits#1 rate_limits#3 | 5.6790 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | How much downtime per month can you actually promise? |
| n-009 | normal | PASS | versioning#2 | versioning#2 versioning#1 versioning#4 versioning#3 versioning#5 | 8.8291 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | found must_not_include: 410 sunset_version | How much warning will we get before an API version we depend on disappears? |
| n-010 | normal | PASS | webhooks#2 | webhooks#1 webhooks#2 rate_limits#3 data_retention#2 sla#1 | 11.6163 | 1.0000 | 0.2000 | 0.5000 | 0.6309 | found must_not_include: at most 5 attempts | Our endpoint was down for about twenty minutes. How many times will Lumen keep pushing the event at us, and over what span of time? |
| n-011 | normal | PASS | guidelines_us#7 | guidelines_us#7 refunds#1 errors#4 auth#1 data_retention#1 | 11.3921 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | Binding a policy failed with an error saying the form was not registered. What does that mean? |
| n-012 | normal | PASS | guidelines_brazil#7 | guidelines_brazil#7 guidelines_brazil#3 guidelines_us#3 guidelines_brazil#4 guidelines_us#4 | 8.8233 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | Is an English translation of the policy documents good enough for the policyholder? |
| n-013 | normal | PASS | rate_limits#5 | rate_limits#5 versioning#1 errors#4 errors#3 auth#2 | 8.8576 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | Do requests that fail with a client error still eat into my quota? What about health checks? |
| n-014 | normal | PASS | pagination#4 | pagination#4 pagination#1 data_retention#1 versioning#3 data_retention#2 | 17.1672 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | Why do some records show up twice when I page through while new rows are being inserted? |
| n-015 | normal | FAIL | webhooks#1 | idempotency#1 auth#2 data_retention#1 auth#3 versioning#2 | 4.6787 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | missing must_include: X-Lumen-Signature, HMAC-SHA256, 300 seconds | How do I prove that a POST hitting my callback URL really came from Lumen? |
| n-016 | normal | FAIL | sla#4 | versioning#2 sla#5 rate_limits#5 guidelines_brazil#1 guidelines_us#1 | 6.1028 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | missing must_include: 02:00 to 04:00 UTC, 72 hours | When do you take the API down on purpose, and how much notice do we get first? |
| n-017 | normal | PASS | versioning#3 | versioning#3 pagination#4 pagination#5 errors#4 auth#4 | 20.0727 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | ok | You added a new enum member to a status field without bumping anything. Is that allowed? |
| n-018 | normal | PASS | errors#5 | errors#5 idempotency#1 idempotency#3 webhooks#2 idempotency#4 | 13.2823 | 1.0000 | 0.2000 | 1.0000 | 1.0000 | found must_not_include: at most 3 retries | Is a 503 worth retrying, or will retrying the write create the resource twice? |
| a-001 | abstention | n/a | (none) | versioning#4 auth#1 refunds#2 errors#3 versioning#1 | 4.3303 | n/a | n/a | 0.0000 | n/a | ok | Does Lumen support Bitcoin payments? |
| a-002 | abstention | n/a | (none) | auth#1 idempotency#1 pagination#2 rate_limits#1 auth#4 | 9.2034 | n/a | n/a | 0.0000 | n/a | ok | Can our engineers authenticate through Okta using SAML single sign-on instead of API keys? |
| a-003 | abstention | n/a | (none) | auth#2 auth#1 pagination#2 webhooks#4 guidelines_brazil#1 | 5.7296 | n/a | n/a | 0.0000 | n/a | ok | Is there a GraphQL interface or an official client library instead of hand-written REST calls? |
| a-004 | abstention | n/a | (none) | errors#5 idempotency#2 rate_limits#4 refunds#3 sla#4 | 5.4295 | n/a | n/a | 0.0000 | n/a | ok | Can we install and run Lumen on our own servers inside a private VPC? |
| a-005 | abstention | n/a | (none) | sla#1 sla#4 webhooks#4 rate_limits#5 rate_limits#1 | 5.6790 | n/a | n/a | 0.0000 | n/a | ok | What does the subscription cost per month in euros, and is there a free tier? |
| a-006 | abstention | n/a | (none) | webhooks#1 guidelines_brazil#1 guidelines_us#1 guidelines_brazil#3 guidelines_brazil#5 | 5.6277 | n/a | n/a | 0.0000 | n/a | ok | Does Lumen have a mobile app that sends push notifications to the policyholder? |
| m-001 | ambiguous | PASS | guidelines_us#1 guidelines_brazil#1 | guidelines_brazil#1 guidelines_us#1 refunds#1 versioning#2 data_retention#2 | 12.8270 | 1.0000 | 0.4000 | 1.0000 | 1.0000 | ok | How many days of cancellation notice do I need to give? |
| m-002 | ambiguous | PASS | guidelines_us#2 guidelines_brazil#2 | guidelines_brazil#2 guidelines_us#2 data_retention#1 sla#2 webhooks#4 | 15.6096 | 1.0000 | 0.4000 | 1.0000 | 1.0000 | ok | By when must a claim be submitted after the date of loss? |
| m-003 | ambiguous | PASS | guidelines_us#3 guidelines_brazil#3 | guidelines_brazil#3 guidelines_us#3 guidelines_brazil#7 data_retention#1 data_retention#3 | 5.4884 | 1.0000 | 0.4000 | 1.0000 | 1.0000 | ok | How long do I have after receiving the policy documents to back out of the contract? |
| m-004 | ambiguous | PASS | guidelines_us#4 guidelines_brazil#4 | guidelines_brazil#4 guidelines_us#4 data_retention#1 data_retention#5 data_retention#4 | 8.6243 | 1.0000 | 0.4000 | 1.0000 | 1.0000 | ok | Where is policy data physically stored, and can it ever leave that location? |
| m-005 | ambiguous | PASS | guidelines_us#5 guidelines_brazil#5 | guidelines_brazil#5 guidelines_us#5 guidelines_brazil#1 guidelines_us#1 guidelines_us#2 | 7.4115 | 1.0000 | 0.4000 | 1.0000 | 1.0000 | ok | How fast must we acknowledge and resolve a policyholder complaint? |
| m-006 | ambiguous | PASS | guidelines_us#6 guidelines_brazil#6 | guidelines_brazil#6 guidelines_us#6 idempotency#1 guidelines_brazil#2 guidelines_us#7 | 10.9206 | 1.0000 | 0.4000 | 1.0000 | 1.0000 | ok | How long must someone have been insured before a condition they already had is covered? |

