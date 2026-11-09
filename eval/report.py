"""Markdown rendering for the retrieval eval — formatting only.

Takes the computed ``EvalResult`` from ``eval.retrieval_eval`` and returns text; ``write_report``
puts it on disk. Nothing is measured or decided here, which is what keeps ``eval/retrieval_eval.py``
free of formatting and makes identical input produce byte-identical output: fixed float precision,
no clock, no dict ordering luck, no template engine, no classes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.retrieval_eval import Bucket, CaseResult, EvalResult, Mean, SweepRow

# The bucket labels the report names in prose and in the `pass` column. They are spelled here rather
# than imported from ``eval.retrieval_eval``, which imports this module.
_GATED, _AMBIGUOUS, _ABSTENTION = "normal", "ambiguous", "abstention"


def render_markdown(result: EvalResult) -> str:
    """The whole report in one string, in the fixed order: gates, aggregates, abstention, cases."""
    return "\n".join(
        [
            *_header(result),
            *_gates(result),
            *_aggregates(result),
            *_abstention(result),
            *_cases(result),
        ]
    )


def write_report(path: Path, result: EvalResult) -> None:
    """Write ``render_markdown`` to ``path``, byte-identical to what the CLI prints."""
    path.write_text(f"{render_markdown(result)}\n", encoding="utf-8")


def _header(result: EvalResult) -> list[str]:
    composition = ", ".join(f"{bucket.n_cases} `{bucket.case_type}`" for bucket in result.buckets)
    total = sum(bucket.n_cases for bucket in result.buckets)
    command = (
        f"python -m eval.retrieval_eval --golden {result.golden_path} --k {result.k}"
        f" --min-recall {_threshold_value(result.min_recall)}"
        f" --min-mrr {_threshold_value(result.min_mrr)}"
    )
    note = []
    if result.file_k_values != (result.k,):
        file_k = ", ".join(str(value) for value in result.file_k_values)
        note.append(
            f"- Note: `--k {result.k}` overrides each case's own `k`; the golden file's "
            f"per-case `k` value(s) are: {file_k}."
        )
    return [
        "# Lumen retrieval eval",
        "",
        f"- Corpus: {result.n_documents} documents / {result.n_chunks} chunks "
        "(`src/lumen/corpus`, loaded by `lumen.chunking.load_corpus`).",
        f"- Golden file: `{result.golden_path}` — {total} cases ({composition}).",
        f"- Command: `{command}`",
        f"- Scoring k: {result.k}, applied uniformly to every case.",
        *note,
        "",
    ]


def _gates(result: EvalResult) -> list[str]:
    return [
        "## Gates",
        "",
        "```text",
        *(
            f"{gate.metric} over {gate.bucket} ({gate.n_cases} cases): "
            f"{_number(gate.observed)} >= {_threshold_value(gate.threshold)} "
            f"{'PASS' if gate.passed else 'FAIL'}"
            for gate in result.gates
        ),
        "```",
        "",
        "Each gate is measured over the `normal` bucket only, and the bucket name is printed "
        "on its line for that reason. Blending the three case types into one number is what "
        "this repo exists to refuse: the abstention cases contribute `None` recall and `0.0` "
        "reciprocal rank by construction, and the ambiguous cases have two ground-truth chunks "
        "where a normal case has one. A `FAIL` on any line above makes the CLI exit 1, which "
        "is what blocks CI.",
        "",
        "If a gate fails, the thing to change is the retriever — not the threshold, not the "
        "golden labels, not this report.",
        "",
    ]


def _aggregates(result: EvalResult) -> list[str]:
    def cells(bucket: Bucket) -> list[str]:
        # `pass` is only defined for the two buckets that have a retrieval verdict at all.
        verdict = (
            "n/a"
            if bucket.n_pass + bucket.n_fail == 0
            else f"{bucket.n_pass}/{bucket.n_pass + bucket.n_fail}"
        )
        label = f"{bucket.case_type} (gated)" if bucket.case_type == _GATED else bucket.case_type
        return [label, str(bucket.n_cases), verdict, *(_mean(mean) for mean in bucket.means)]

    overall = result.overall
    figures = " · ".join(
        f"{label} {_mean(mean)}"
        for label, mean in zip(_metric_headers(result.k), overall.means, strict=True)
    )
    return [
        "## Aggregates per case type",
        "",
        *_table(
            ["bucket", "cases", "pass", *_metric_headers(result.k)],
            [cells(bucket) for bucket in result.buckets],
        ),
        "",
        "- The `(included/total)` count after every mean is how many cases that mean actually "
        "covers. `mean_excluding_none` refuses to turn `None` into `0.0`, so `n/a (0/6)` means "
        '"no case in this bucket has a value", not "the score is zero".',
        "- `pass` is not one definition across rows. A `normal` case passes when its one "
        "expected chunk is in the top k. An `ambiguous` case passes only when **both** market "
        "chunks are in the top k — the markets are near-tied on purpose, so retrieving one "
        "half of a contradiction is not a win. An `abstention` case has **no** verdict: "
        "whether it *should* abstain depends on a score threshold that is not fixed, so it is "
        "judged only by the sweep below and never enters a gate.",
        "- The `abstention` row has no recall, precision or nDCG at all, and every `mrr` in it "
        "is `0.0`. Its real signal is the `top_score` distribution below — which is why MRR is "
        "reported next to the score distribution and never alone.",
        "",
        f"### Global line — all {overall.n_cases} cases blended, **not a gate**",
        "",
        "```text",
        figures,
        "```",
        "",
        "Printed for transparency only, and diluted by construction: it blends 18 "
        "single-answer cases with 6 two-answer cases and 6 cases with no answer at all, so its "
        "recall is computed over fewer cases than its MRR, and its MRR carries six structural "
        "`0.0`s. It is not comparable between datasets and it is never the gate — the `normal` "
        "row above is.",
        "",
    ]


def _abstention(result: EvalResult) -> list[str]:
    scored, none_case_ids = result.abstention
    minimum, median, maximum = result.abstention_band
    n_cases = len(scored) + len(none_case_ids)
    if none_case_ids:
        signal = (
            "- `top_score is None` means BM25 found no chunk sharing one non-stopword term "
            "with the query — a clean abstention signal that needs no threshold."
        )
    else:
        signal = (
            "- Not one case scored `None`: every one of these queries shares at least one "
            "non-stopword term with some chunk, so the retriever always returns *something* "
            "and the raw `top_score` carries the entire abstention decision."
        )
    return [
        "## Abstention analysis",
        "",
        f"### `top_score` distribution — {n_cases} abstention cases",
        "",
        "These cases have no ground truth, so all the retriever can say about them is how "
        "loudly it matched *something*.",
        "",
        *_table(["case", "top_score"], [[case_id, _number(score)] for case_id, score in scored]),
        "",
        f"- min {_number(minimum)} · median {_number(median)} · max {_number(maximum)} · "
        f"cases with `top_score is None`: {len(none_case_ids)}/{n_cases}"
        + (f" (`{', '.join(none_case_ids)}`)" if none_case_ids else ""),
        signal,
        "",
        *_sweep(result),
        *_fragility(result),
    ]


def _sweep(result: EvalResult) -> list[str]:
    rows, facts, chosen = result.sweep_rows, result.thresholds, result.thresholds.chosen
    first, last = rows[0], rows[-1]
    # The sweep rows carry who was lost, not the bucket sizes; those come from the aggregates.
    n_cases = {bucket.case_type: bucket.n_cases for bucket in result.buckets}

    def cells(values: SweepRow) -> list[str]:
        return [
            _number(values[0], 1),
            f"{values[1]}/{n_cases[_ABSTENTION]}",
            f"{values[2]}/{n_cases[_GATED]}",
            f"{values[3]}/{n_cases[_AMBIGUOUS]}",
            _mean(values[4]),
            _mean(values[5]),
            "**chosen** — smallest t in the sweep covering every abstention"
            if values is chosen
            else "",
        ]

    bullets: list[str] = []
    if chosen is not None:
        bullets.append(
            f"- Chosen row: `t = {_number(chosen[0], 1)}` is the smallest threshold in the "
            f"sweep that covers all {n_cases[_ABSTENTION]} abstentions — and it kills {chosen[2]} "
            f"of {n_cases[_GATED]} `normal` cases ({_percentage(chosen[2], n_cases[_GATED])} of "
            f"the questions the system can in fact answer) and {chosen[3]} of "
            f"{n_cases[_AMBIGUOUS]} `ambiguous` ones."
        )
    else:
        bullets.append(
            f"- No threshold in the sweep covers all {n_cases[_ABSTENTION]} abstentions: at "
            f"`t = {_number(last[0], 1)}` only {last[1]} of {n_cases[_ABSTENTION]} are covered, "
            f"and recall@{result.k} over `normal` is unchanged at {_number(last[4].value)}."
        )
        if facts.required_threshold is not None:
            bullets.append(
                f"- Covering all {n_cases[_ABSTENTION]} would need "
                f"`t > {_number(facts.required_threshold)}` (the highest abstention `top_score`, "
                f"case `{facts.required_threshold_case}`), above the top of this sweep — and that "
                f"cut kills {facts.normal_lost_at_required} of {facts.n_normal_scored} `normal` "
                f"cases ({_percentage(facts.normal_lost_at_required, facts.n_normal_scored)}) and "
                f"{facts.ambiguous_lost_at_required} of {n_cases[_AMBIGUOUS]} `ambiguous` ones."
            )
    if chosen is None and not facts.lossless_cover:
        bullets.append(
            "- No `t` in this range saves an abstention without also costing an answerable case: "
            "every threshold that does something does damage."
        )
    return [
        f"### Threshold sweep — t from {_number(first[0], 1)} to {_number(last[0], 1)}, "
        f"step {_number(rows[1][0] - first[0], 1)}",
        "",
        "A threshold `t` makes the system abstain when `top_score < t`; otherwise it truncates "
        "the retrieved list to the chunks scoring `>= t`. The two recall columns are recall@k "
        "recomputed on that truncated list — what the answer can still see after the cut, the "
        "cost side of the same knob.",
        "",
        *_table(
            [
                "t",
                "abstentions covered",
                "normal lost",
                "ambiguous lost",
                f"recall@{result.k} normal (truncated)",
                f"recall@{result.k} ambiguous (truncated)",
                "note",
            ],
            [cells(values) for values in rows],
        ),
        "",
        *bullets,
        "",
    ]


def _fragility(result: EvalResult) -> list[str]:
    """The threshold discussion; every number in it is measured on this run by the evaluator."""
    facts = result.thresholds
    scored, none_case_ids = result.abstention
    abstention_min, _, abstention_max = result.abstention_band
    normal_min, _, normal_max = result.normal_band
    n_abstentions = len(scored) + len(none_case_ids)
    if facts.chosen is not None:
        cost = (
            "The cheapest threshold in the sweep that covers every abstention, "
            f"`t = {_number(facts.chosen[0], 1)}`, already removes the answer from "
            f"{facts.chosen[2]} of {facts.n_normal_scored} normal cases."
        )
    elif facts.required_threshold is None:
        cost = ""
    else:
        cost = (
            "The cheapest threshold that would cover every abstention, "
            f"`t > {_number(facts.required_threshold)}`, removes the answer from "
            f"{facts.normal_lost_at_required} of {facts.n_normal_scored} normal cases "
            f"({_percentage(facts.normal_lost_at_required, facts.n_normal_scored)} of the "
            "questions the system can currently answer) — it trades a wrong answer for "
            f"{facts.normal_lost_at_required} refusals to answer."
        )
    if facts.ranges_overlap:
        overlap = (
            f"The two ranges overlap on [{_number(facts.overlap_low)}, "
            f"{_number(facts.overlap_high)}]: "
            f"{facts.n_abstentions_at_or_above_normal_minimum} of {n_abstentions} "
            "abstention cases score at or above the *weakest* normal case, and "
            f"{facts.n_normals_below_abstention_maximum} of {facts.n_normal_scored} normal "
            "cases score below the *strongest* abstention case. No value of `t` separates "
            'the two groups: the scores that mean "not in the corpus" sit inside the same '
            'interval as the scores that mean "here is the answer".'
        )
    else:
        overlap = (
            f"The ranges do not overlap (a gap from {_number(facts.overlap_high)} to "
            f"{_number(facts.overlap_low)}), which is the only situation in which one "
            "constant cut can separate abstention from answer."
        )
    return [
        " ".join(
            [
                "**Why a raw-score threshold is fragile.** The "
                f"{n_abstentions} abstention cases score {_number(abstention_min)} to "
                f"{_number(abstention_max)}"
                + (
                    f" ({len(none_case_ids)} of them score nothing at all, `None`)"
                    if none_case_ids
                    else ""
                )
                + f", while the {facts.n_normal_scored} answerable `normal` cases score "
                f"{_number(normal_min)} to {_number(normal_max)}.",
                overlap,
                cost,
                "And that cut would not survive contact with the corpus: BM25 here is an unbounded "
                "sum of idf-weighted term scores, `idf = log(1 + (N - df + 0.5) / (df + 0.5))` "
                f"over N = {result.n_chunks} chunks, so a score grows with query length and term "
                "frequency, shrinks as a term appears in more chunks, and moves whenever the "
                "corpus grows, a document is edited or the tokenizer changes. `t = 1.5` is a "
                "property of *this* corpus and *this* tokenizer, not a safety property: the same "
                "constant means something else once the corpus changes, and re-tuning it against "
                "these labels turns the abstention rule into a parameter of the eval itself. A "
                "normalised signal — the margin between the top two scores, or a classifier "
                "judged on labels — is the version of this decision that transfers.",
            ]
        ),
        "",
    ]


def _cases(result: EvalResult) -> list[str]:
    def cells(case: CaseResult) -> list[str]:
        info: list[str] = []
        if case.missing_must_include:
            info.append("missing must_include: " + ", ".join(case.missing_must_include))
        if case.present_must_not_include:
            info.append("found must_not_include: " + ", ".join(case.present_must_not_include))
        return [
            case.id,
            case.case_type,
            {True: "PASS", False: "FAIL", None: "n/a"}[case.case_pass],
            " ".join(case.expected_chunk_ids) or "(none)",
            " ".join(case.retrieved_ids) or "(none)",
            _number(case.top_score),
            _number(case.recall),
            _number(case.precision),
            _number(case.reciprocal_rank),
            _number(case.ndcg),
            "; ".join(info) or "ok",
            case.query,
        ]

    return [
        "## Per-case results",
        "",
        "`must_include` / `must_not_include` are **informational** in this report. They are "
        "matched case-insensitively against the concatenation of the retrieved chunk texts and "
        "printed in the last column only: they constrain the *answer* (the generation eval), "
        "not retrieval, so a retrieved chunk that happens to contain a `must_not_include` "
        "token is not a retrieval failure. They never change `pass` and never move a gate.",
        "",
        *_table(
            [
                "id",
                "type",
                "pass",
                "expected",
                "retrieved_ids",
                "top_score",
                f"recall@{result.k}",
                f"precision@{result.k}",
                "rr",
                f"nDCG@{result.k}",
                "must_include / must_not_include (informational)",
                "query",
            ],
            [cells(case) for case in result.cases],
        ),
        "",
    ]


def _metric_headers(k: int) -> list[str]:
    """Column labels in ``Bucket.means`` order, so the cutoff is visible in every metric name."""
    return [f"recall@{k}", f"precision@{k}", "mrr", f"nDCG@{k}"]


def _mean(mean: Mean) -> str:
    """`mean (n_included/n_total)` — the counts are the point: what was excluded, and how much."""
    return f"{_number(mean.value)} ({mean.n_included}/{mean.n_included + mean.n_excluded})"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    def cells(values: Sequence[str]) -> str:
        return (
            "| "
            + " | ".join(" ".join(value.split()).replace("|", "\\|") for value in values)
            + " |"
        )

    return [
        cells(headers),
        "| " + " | ".join("---" for _ in headers) + " |",
        *(cells(row) for row in rows),
    ]


def _number(value: float | None, decimals: int = 4) -> str:
    """Metrics print at 4 decimals, thresholds at 1; a ``None`` is never rendered as `0`."""
    return "n/a" if value is None else f"{value:.{decimals}f}"


def _threshold_value(value: float) -> str:
    """Two decimals when that is the exact value (0.80), shortest form otherwise (0.875)."""
    pretty = f"{value:.2f}"
    return pretty if float(pretty) == value else f"{value:g}"


def _percentage(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{100.0 * part / whole:.1f}%"
