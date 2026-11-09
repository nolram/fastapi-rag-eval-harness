"""Deterministic retrieval eval over the hand-labelled golden set — computation and CLI only.

The pipeline is fixed: read the JSONL golden file and validate it against the real chunker output,
score every case with BM25 from ``lumen.retrieval``, aggregate **per case type**, sweep raw-score
thresholds, decide the gate verdicts. Metric maths lives only in ``eval/metrics.py`` and markdown
rendering only in ``eval/report.py``: this module neither formats a number nor invents a metric.

Deterministic by construction: no clock, no randomness, no network, no LLM — identical inputs give
byte-identical stdout and byte-identical ``eval/report.md``.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import NamedTuple

from eval import report
from eval.metrics import (
    mean_excluding_none,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from lumen.chunking import load_corpus
from lumen.retrieval import retrieve

TYPE_NORMAL = "normal"
TYPE_AMBIGUOUS = "ambiguous"
TYPE_ABSTENTION = "abstention"
# Buckets are computed and printed in this order, always: the gated bucket first, abstention last.
CASE_TYPES: tuple[str, ...] = (TYPE_NORMAL, TYPE_AMBIGUOUS, TYPE_ABSTENTION)
# The label for the transparency line that blends all three buckets (never a gate).
GLOBAL_BUCKET = "global"
# The committed composition of eval/golden.jsonl, hard-coded on purpose: the counts are part of the
# dataset contract, so re-typing a case as `abstention` to dodge a gate fails validation instead of
# being silently scored.
EXPECTED_COUNTS: Mapping[str, int] = {TYPE_NORMAL: 18, TYPE_AMBIGUOUS: 6, TYPE_ABSTENTION: 6}
# Raw-score threshold sweep bounds (inclusive), step 0.5 — the fixed range the report discusses.
SWEEP_MIN, SWEEP_MAX, SWEEP_STEP = 0.5, 4.0, 0.5
DEFAULT_GOLDEN, DEFAULT_K, DEFAULT_MIN_RECALL, DEFAULT_MIN_MRR = "eval/golden.jsonl", 5, 0.80, 0.70


class GoldenFileError(Exception):
    """The golden file is unreadable, malformed, or inconsistent with the chunker output.

    Carries every problem found in one pass (not just the first), because a partial report sends
    someone editing the dataset to fix one line, re-run, and hit the next error.
    """


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One labelled case. ``case_type`` is the JSON ``type`` field, renamed to avoid shadowing."""

    id: str
    case_type: str
    query: str
    expected_chunk_ids: tuple[str, ...]
    expected_doc_ids: tuple[str, ...]
    must_include: tuple[str, ...]
    must_not_include: tuple[str, ...]
    k: int
    should_abstain: bool
    clarify: bool
    notes: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One scored case: what came back, the four metrics, and the verdict.

    ``case_pass`` is ``None`` for a type with no retrieval verdict, which is not the same as
    ``False``. ``missing_must_include`` / ``present_must_not_include`` are informational only.
    """

    id: str
    case_type: str
    query: str
    expected_chunk_ids: tuple[str, ...]
    retrieved_ids: tuple[str, ...]
    retrieved_scores: tuple[float, ...]
    top_score: float | None
    recall: float | None
    precision: float | None
    reciprocal_rank: float
    ndcg: float | None
    case_pass: bool | None
    missing_must_include: tuple[str, ...]
    present_must_not_include: tuple[str, ...]


class Mean(NamedTuple):
    """A mean plus the counts that produced it: ``(value, n_included, n_excluded)``.

    The counts are the point — ``mean_excluding_none`` refuses to turn ``None`` into ``0.0``, so a
    reader can always see how much of a bucket a number actually covers.
    """

    value: float | None
    n_included: int
    n_excluded: int


@dataclass(frozen=True, slots=True)
class Bucket:
    """One case type's verdict tally and its four means, always recall, precision, mrr, ndcg."""

    case_type: str
    n_cases: int
    means: tuple[Mean, ...]
    n_pass: int
    n_fail: int


@dataclass(frozen=True, slots=True)
class Gate:
    """One gate line: the bucket it is measured over, the observed value and the verdict."""

    metric: str
    bucket: str
    n_cases: int
    observed: float | None
    threshold: float
    passed: bool


# One sweep row: (t, abstentions covered, normal lost, ambiguous lost, recall@k of the truncated
# normal list, same for the truncated ambiguous list).
SweepRow = tuple[float, int, int, int, Mean, Mean]
# A ``top_score`` band: (minimum, median, maximum), all ``None`` when nothing in the group was
# scored at all.
Band = tuple[float | None, float | None, float | None]


@dataclass(frozen=True, slots=True)
class ThresholdFacts:
    """The numbers the threshold discussion cites — computed once, so the prose cannot drift."""

    n_abstentions: int
    n_normal_scored: int
    n_abstentions_at_or_above_normal_minimum: int
    n_normals_below_abstention_maximum: int
    ranges_overlap: bool
    overlap_low: float | None
    overlap_high: float | None
    chosen: SweepRow | None
    required_threshold: float | None
    required_threshold_case: str
    normal_lost_at_required: int
    ambiguous_lost_at_required: int
    lossless_cover: bool


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Everything the renderer needs; produced by :func:`run`, never mutated afterwards.

    ``abstention`` is ``(sorted (case id, top_score) pairs, case ids that scored nothing at all)``,
    beside the ``top_score`` bands of the abstention and the answerable ``normal`` groups.
    """

    golden_path: str
    k: int
    min_recall: float
    min_mrr: float
    file_k_values: tuple[int, ...]
    n_documents: int
    n_chunks: int
    cases: tuple[CaseResult, ...]
    buckets: tuple[Bucket, ...]
    overall: Bucket
    gates: tuple[Gate, ...]
    abstention: tuple[tuple[tuple[str, float], ...], tuple[str, ...]]
    abstention_band: Band
    normal_band: Band
    sweep_rows: tuple[SweepRow, ...]
    thresholds: ThresholdFacts


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. 0 = every gate cleared, 1 = a gate failed, 2 = golden file is unusable."""
    arguments = _build_parser().parse_args(argv)
    try:
        result = run(arguments.golden, arguments.k, arguments.min_recall, arguments.min_mrr)
    except GoldenFileError as error:
        sys.stderr.write(f"{error}\n")
        return 2
    sys.stdout.write(f"{report.render_markdown(result)}\n")
    if arguments.report is not None:
        report.write_report(arguments.report, result)
    return 1 if any(not gate.passed for gate in result.gates) else 0


def run(golden_path: Path, k: int, min_recall: float, min_mrr: float) -> EvalResult:
    """Read, validate, score, aggregate and gate. Raises ``GoldenFileError`` on a bad golden file.

    ``k`` overrides each case's own ``k`` uniformly — a recorded decision, so that a single flag can
    answer "what would this dataset look like at k=1 / k=10" without touching the dataset.
    """
    corpus_docs = {chunk.id: chunk.doc_id for chunk in load_corpus()}
    cases = load_golden(golden_path, corpus_docs)
    scored = tuple(_score_case(case, k) for case in cases)
    by_type = {
        case_type: [case for case in scored if case.case_type == case_type]
        for case_type in CASE_TYPES
    }
    buckets = {case_type: _bucket(case_type, group) for case_type, group in by_type.items()}
    buckets[GLOBAL_BUCKET] = _bucket(GLOBAL_BUCKET, scored)
    abstention_ids = sorted(
        (
            (case.id, case.top_score)
            for case in by_type[TYPE_ABSTENTION]
            if case.top_score is not None
        ),
        key=lambda pair: (pair[1], pair[0]),
    )
    abstention_scores = [score for _, score in abstention_ids]
    normal_scores = sorted(
        score for score in (case.top_score for case in by_type[TYPE_NORMAL]) if score is not None
    )
    sweep = _sweep(by_type, k)
    return EvalResult(
        golden_path=str(golden_path),
        k=k,
        min_recall=min_recall,
        min_mrr=min_mrr,
        file_k_values=tuple(sorted({case.k for case in cases})),
        n_documents=len({doc_id for doc_id in corpus_docs.values()}),
        n_chunks=len(corpus_docs),
        cases=scored,
        buckets=tuple(buckets[case_type] for case_type in CASE_TYPES),
        overall=buckets[GLOBAL_BUCKET],
        gates=_gates(buckets[TYPE_NORMAL], k, min_recall, min_mrr),
        abstention=(
            tuple(abstention_ids),
            tuple(case.id for case in by_type[TYPE_ABSTENTION] if case.top_score is None),
        ),
        abstention_band=_band(abstention_scores),
        normal_band=_band(normal_scores),
        sweep_rows=sweep,
        thresholds=_facts(
            len(by_type[TYPE_ABSTENTION]),
            abstention_ids,
            normal_scores,
            by_type[TYPE_AMBIGUOUS],
            sweep,
        ),
    )


def load_golden(path: Path, corpus_docs: Mapping[str, str]) -> list[GoldenCase]:
    """Parse and validate the JSONL golden file in one pass, collecting every problem.

    This is the permanent version of the ad-hoc checks used while the dataset was written, and it
    is what makes "someone edited golden.jsonl to make CI green" visible: re-labelled types,
    invented chunk ids and emptied expectations all fail here with exit 2, before a single metric
    is computed. The committed case counts fail too, so a case cannot be moved between buckets.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise GoldenFileError(f"golden file {path} is unreadable: {error}") from error
    cases: list[GoldenCase] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record: object = json.loads(line)
        except json.JSONDecodeError as error:
            problems.append(f"line {number}: not valid JSON ({error.msg})")
            continue
        case = _parse_record(record, number, problems)
        if case is None:
            continue
        if case.id in seen:
            problems.append(f"duplicate case id '{case.id}'")
        seen.add(case.id)
        _check_case(case, corpus_docs, problems)
        cases.append(case)
    for case_type, expected in EXPECTED_COUNTS.items():
        found = sum(1 for case in cases if case.case_type == case_type)
        if found != expected:
            problems.append(f"expected {expected} '{case_type}' cases, found {found}")
    if problems:
        raise GoldenFileError(_describe(path, problems))
    return cases


# Every JSON key a golden record must carry; `type` is the only one that is not a field name.
GOLDEN_KEYS = (
    "id type query expected_chunk_ids expected_doc_ids must_include must_not_include k "
    "should_abstain clarify notes"
)


def _parse_record(record: object, number: int, problems: list[str]) -> GoldenCase | None:
    """One JSONL record -> ``GoldenCase``, appending every schema problem to ``problems``.

    The nested readers are the whole JSON schema: a wrong type is reported and replaced by a
    neutral value, so one malformed field does not hide the problems in the rest of the record.
    """
    where = f"line {number}"
    if not isinstance(record, dict):
        problems.append(f"{where}: expected a JSON object")
        return None
    missing = [key for key in GOLDEN_KEYS.split() if key not in record]
    if missing:
        problems.append(f"{where}: missing field(s) {', '.join(missing)}")
        return None

    def bad(key: str, expected: str) -> None:
        problems.append(f"{where}: field '{key}' must be {expected}")

    def text(key: str, *, allow_empty: bool = False) -> str:
        value = record[key]
        if not isinstance(value, str) or (not value.strip() and not allow_empty):
            bad(key, "a non-empty string")
            return ""
        return value

    def texts(key: str) -> tuple[str, ...]:
        value = record[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            bad(key, "a list of strings")
            return ()
        return tuple(value)

    def whole(key: str) -> int:
        value = record[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            bad(key, "an int >= 1")
            return 0
        return value

    def flag(key: str) -> bool:
        value = record[key]
        if not isinstance(value, bool):
            bad(key, "a boolean")
            return False
        return value

    return GoldenCase(
        id=text("id"),
        case_type=text("type"),
        query=text("query"),
        expected_chunk_ids=texts("expected_chunk_ids"),
        expected_doc_ids=texts("expected_doc_ids"),
        must_include=texts("must_include"),
        must_not_include=texts("must_not_include"),
        k=whole("k"),
        should_abstain=flag("should_abstain"),
        clarify=flag("clarify"),
        notes=text("notes", allow_empty=True),
    )


def _check_case(case: GoldenCase, corpus_docs: Mapping[str, str], problems: list[str]) -> None:
    """The per-type invariants one case must satisfy against the real chunker output."""
    unknown = [chunk_id for chunk_id in case.expected_chunk_ids if chunk_id not in corpus_docs]
    if unknown:
        problems.append(f"{case.id}: expected_chunk_ids not in the corpus: {', '.join(unknown)}")
        return  # the expected_doc_ids check below would only restate these ids
    docs = sorted({corpus_docs[chunk_id] for chunk_id in case.expected_chunk_ids})
    if sorted(set(case.expected_doc_ids)) != docs:
        problems.append(
            f"{case.id}: expected_doc_ids must be the documents of expected_chunk_ids {docs}, not "
            f"{sorted(set(case.expected_doc_ids))}"
        )
    if case.case_type == TYPE_ABSTENTION:
        if case.expected_chunk_ids or case.expected_doc_ids:
            problems.append(f"{case.id}: abstention cases must expect no chunk and no document")
        if not case.should_abstain:
            problems.append(f"{case.id}: abstention cases must have should_abstain: true")
    elif case.case_type == TYPE_NORMAL:
        # Exactly one expected chunk: that is what "pass = its single expected chunk is in the top
        # k" means. Zero would make `pass` vacuously true, which is an editing hole.
        if len(case.expected_chunk_ids) != 1:
            problems.append(
                f"{case.id}: normal cases must reference exactly one chunk, "
                f"got {len(case.expected_chunk_ids)}"
            )
        if case.should_abstain or case.clarify:
            problems.append(
                f"{case.id}: normal cases must have should_abstain: false and clarify: false"
            )
    elif case.case_type == TYPE_AMBIGUOUS:
        if len(set(case.expected_chunk_ids)) < 2 or len(docs) < 2:
            problems.append(
                f"{case.id}: ambiguous cases must reference at least 2 chunks across 2 documents"
            )
        if not case.clarify:
            problems.append(f"{case.id}: ambiguous cases must have clarify: true")
    else:
        problems.append(f"{case.id}: unknown type '{case.case_type}'")


def _describe(path: Path, problems: Sequence[str]) -> str:
    return f"golden file {path} is invalid ({len(problems)} problem(s)):\n" + "\n".join(
        f"  - {problem}" for problem in problems
    )


def _score_case(case: GoldenCase, k: int) -> CaseResult:
    """Retrieve, then score one case with ``eval/metrics.py`` and nothing else.

    ``must_include`` / ``must_not_include`` are ANSWER constraints (the generation eval's job);
    matching them against retrieved text here is a reading aid that never moves ``case_pass``.
    """
    result = retrieve(case.query, k)
    retrieved_ids = tuple(chunk.id for chunk in result.chunks)
    expected = case.expected_chunk_ids
    haystack = "\n".join(chunk.text for chunk in result.chunks).lower()
    # ``None`` is a real verdict state: an abstention case has no retrieval verdict, because whether
    # it *should* abstain depends on a raw-score threshold that is not fixed. It is judged in the
    # sweep only, never in a gate. A normal case needs its one chunk in the top k; an ambiguous case
    # needs **both** market chunks, because the markets are deliberate near-ties.
    case_pass: bool | None = (
        None if case.case_type == TYPE_ABSTENTION else set(expected) <= set(retrieved_ids)
    )
    return CaseResult(
        id=case.id,
        case_type=case.case_type,
        query=case.query,
        expected_chunk_ids=expected,
        retrieved_ids=retrieved_ids,
        retrieved_scores=tuple(result.scores),
        top_score=result.top_score,
        recall=recall_at_k(retrieved_ids, expected, k),
        precision=precision_at_k(retrieved_ids, expected, k),
        reciprocal_rank=reciprocal_rank(retrieved_ids, expected),
        ndcg=ndcg_at_k(retrieved_ids, expected, k),
        case_pass=case_pass,
        missing_must_include=tuple(t for t in case.must_include if t.lower() not in haystack),
        present_must_not_include=tuple(t for t in case.must_not_include if t.lower() in haystack),
    )


def _mean(values: Sequence[float | None]) -> Mean:
    """Delegate the maths to ``mean_excluding_none`` and keep the counts beside the mean."""
    return Mean(*mean_excluding_none(values))


def _band(values: Sequence[float]) -> Band:
    """(min, median, max) of a group's scores; all ``None`` when the group scored nothing at all."""
    return (values[0], median(values), values[-1]) if values else (None, None, None)


def _bucket(case_type: str, cases: Sequence[CaseResult]) -> Bucket:
    return Bucket(
        case_type=case_type,
        n_cases=len(cases),
        means=(
            _mean([case.recall for case in cases]),
            _mean([case.precision for case in cases]),
            _mean([case.reciprocal_rank for case in cases]),
            _mean([case.ndcg for case in cases]),
        ),
        n_pass=sum(1 for case in cases if case.case_pass is True),
        n_fail=sum(1 for case in cases if case.case_pass is False),
    )


def _sweep(by_type: Mapping[str, Sequence[CaseResult]], k: int) -> tuple[SweepRow, ...]:
    """Sweep raw-score thresholds, reporting the abstention win **and** the answer loss together.

    A threshold abstains when ``top_score < t``; otherwise the retrieved list is truncated to the
    chunks scoring ``>= t`` and recall is recomputed on that list — one knob, both its sides.
    """

    def kept(case: CaseResult, threshold: float) -> tuple[str, ...]:
        # Truncating to nothing is the same decision as abstaining: the two halves must not
        # disagree, so both are decided by `top_score < threshold` on the case and `>=` per chunk.
        return tuple(
            chunk_id
            for chunk_id, score in zip(case.retrieved_ids, case.retrieved_scores, strict=True)
            if score >= threshold
        )

    def abstained(case: CaseResult, threshold: float) -> bool:
        return case.top_score is None or case.top_score < threshold

    steps = int(round((SWEEP_MAX - SWEEP_MIN) / SWEEP_STEP)) + 1
    rows: list[SweepRow] = []
    for step in range(steps):
        threshold = SWEEP_MIN + step * SWEEP_STEP
        rows.append(
            (
                threshold,
                sum(1 for case in by_type[TYPE_ABSTENTION] if abstained(case, threshold)),
                sum(1 for case in by_type[TYPE_NORMAL] if abstained(case, threshold)),
                sum(1 for case in by_type[TYPE_AMBIGUOUS] if abstained(case, threshold)),
                _mean(
                    [
                        recall_at_k(kept(case, threshold), case.expected_chunk_ids, k)
                        for case in by_type[TYPE_NORMAL]
                    ]
                ),
                _mean(
                    [
                        recall_at_k(kept(case, threshold), case.expected_chunk_ids, k)
                        for case in by_type[TYPE_AMBIGUOUS]
                    ]
                ),
            )
        )
    return tuple(rows)


def _facts(
    n_abstentions: int,
    abstention_scores: Sequence[tuple[str, float]],
    normal_scores: Sequence[float],
    ambiguous: Sequence[CaseResult],
    sweep: Sequence[SweepRow],
) -> ThresholdFacts:
    """Every claim the fragility paragraph makes, measured on this run rather than asserted."""
    scores = [score for _, score in abstention_scores]
    scored_both = bool(scores) and bool(normal_scores)
    # Covering every abstention needs a threshold strictly above the highest abstention top_score,
    # because a case abstains only when `top_score < threshold`.
    required = scores[-1] if scores else None
    return ThresholdFacts(
        n_abstentions=n_abstentions,
        n_normal_scored=len(normal_scores),
        n_abstentions_at_or_above_normal_minimum=sum(
            1 for score in scores if scored_both and score >= normal_scores[0]
        ),
        n_normals_below_abstention_maximum=sum(
            1 for score in normal_scores if scored_both and score < scores[-1]
        ),
        ranges_overlap=scored_both
        and max(scores[0], normal_scores[0]) <= min(scores[-1], normal_scores[-1]),
        overlap_low=max(scores[0], normal_scores[0]) if scored_both else None,
        overlap_high=min(scores[-1], normal_scores[-1]) if scored_both else None,
        chosen=next((row for row in sweep if row[1] == n_abstentions), None),
        required_threshold=required,
        required_threshold_case=abstention_scores[-1][0] if abstention_scores else "",
        normal_lost_at_required=sum(
            1 for score in normal_scores if required is not None and score <= required
        ),
        ambiguous_lost_at_required=sum(
            1
            for case in ambiguous
            if required is not None and case.top_score is not None and case.top_score <= required
        ),
        lossless_cover=any(row[1] > 0 and row[2] == 0 and row[3] == 0 for row in sweep),
    )


def _gates(normal: Bucket, k: int, min_recall: float, min_mrr: float) -> tuple[Gate, ...]:
    """The two gates, measured over the ``normal`` bucket only.

    Recorded decision, taken before any number was computed: blending buckets is what this repo
    forbids, and the abstention cases carry ``recall = None`` and ``rr = 0.0`` by construction. The
    blended global figures are printed next to these lines, labelled as not a gate. And a bucket
    with nothing to measure cannot clear a gate either: "no data" is not a pass.
    """
    measured = (
        (f"recall@{k}", normal.means[0].value, min_recall),
        ("mrr", normal.means[2].value, min_mrr),
    )
    return tuple(
        Gate(
            metric, TYPE_NORMAL, normal.n_cases, value, limit, value is not None and value >= limit
        )
        for metric, value, limit in measured
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.retrieval_eval",
        description="Score eval/golden.jsonl against BM25 retrieval and print the report. Exit "
        "codes: 0 all gates met, 1 a gate failed, 2 the golden file is invalid or unreadable.",
    )
    parser.add_argument(
        "--golden", type=Path, default=Path(DEFAULT_GOLDEN), help="JSONL golden dataset"
    )
    parser.add_argument(
        "--k",
        type=_positive_int,
        default=DEFAULT_K,
        help="cut-off applied to every case, overriding the per-case k (default: %(default)s)",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=DEFAULT_MIN_RECALL,
        help=f"gate on recall@k over the {TYPE_NORMAL} bucket (default: %(default)s)",
    )
    parser.add_argument(
        "--min-mrr",
        type=float,
        default=DEFAULT_MIN_MRR,
        help=f"gate on MRR over the {TYPE_NORMAL} bucket (default: %(default)s)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        metavar="PATH",
        help="also write the rendered markdown report to PATH",
    )
    return parser


def _positive_int(raw: str) -> int:
    """``--k`` must be a positive int; argparse turns a bad value into exit code 2 (bad input)."""
    if not raw.isdigit() or int(raw) < 1:
        raise argparse.ArgumentTypeError(f"--k must be a positive integer, got {raw!r}")
    return int(raw)


if __name__ == "__main__":
    raise SystemExit(main())
