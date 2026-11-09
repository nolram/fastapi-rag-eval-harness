"""Behaviour tests for BM25 and ``retrieve()``: determinism, ordering, abstention, index rebuild.

Unit-level only — no quality gates and no numeric thresholds here. The comment at the bottom of this
file marks where Stage 8 appends the Recall@5 / MRR gates and the corpus-regression tripwire.
"""

import dataclasses
import json
from inspect import signature
from pathlib import Path

import pytest

from eval import report
from eval.retrieval_eval import (
    DEFAULT_K,
    DEFAULT_MIN_MRR,
    DEFAULT_MIN_RECALL,
    EXPECTED_COUNTS,
    TYPE_ABSTENTION,
    TYPE_NORMAL,
    EvalResult,
    main,
    run,
)
from lumen import retrieval
from lumen.chunking import Chunk, load_corpus
from lumen.retrieval import BM25, RetrievedResult, retrieve, tokenize

RATE_LIMIT_QUERY = "What is the default rate limit for the API?"
GIBBERISH_QUERY = "zzyzx quuxford"
SAMPLE_QUERIES = (
    RATE_LIMIT_QUERY,
    "how long is an access token valid",
    "how do I verify a webhook signature",
    "how many days of cancellation notice do I need to give",
    "what is the refund processing time",
    "cursor expires",
)


def _ids(result: RetrievedResult) -> list[str]:
    return [chunk.id for chunk in result.chunks]


def _synthetic_corpus() -> list[Chunk]:
    """Three chunks of identical token count, so the only variable is how often `widget` appears."""
    return [
        Chunk(
            id="alpha#1",
            doc_id="alpha.md",
            title="Alpha",
            text="widget gadget sprocket cog panel bolt valve gasket flange",
        ),
        Chunk(
            id="beta#1",
            doc_id="beta.md",
            title="Beta",
            text="widget widget widget widget widget gadget sprocket cog panel",
        ),
        Chunk(
            id="gamma#1",
            doc_id="gamma.md",
            title="Gamma",
            text="gadget sprocket cog panel bolt valve gasket flange bracket",
        ),
    ]


def test_retrieved_result_exposes_exactly_the_contracted_frozen_slots_fields() -> None:
    result = retrieve(RATE_LIMIT_QUERY, k=2)
    assert [field.name for field in dataclasses.fields(RetrievedResult)] == [
        "chunks",
        "scores",
        "top_score",
    ]
    assert RetrievedResult.__dataclass_params__.frozen
    assert not hasattr(result, "__dict__")


def test_public_signatures_keep_their_contractual_defaults() -> None:
    build = signature(BM25.build)
    assert build.parameters["k1"].default == 1.5
    assert build.parameters["b"].default == 0.75
    assert signature(retrieve).parameters["k"].default == 5


def test_tokenize_lowercases_splits_on_punctuation_and_drops_one_character_tokens() -> None:
    assert tokenize("X-RateLimit-Limit") == ["ratelimit", "limit"]
    assert tokenize("Retry-After in whole seconds") == ["retry", "after", "whole", "seconds"]


def test_tokenize_strips_markdown_backticks_asterisks_and_heading_markers() -> None:
    assert tokenize("`429 too_many_requests` **bulk** `POST /v1/policies`") == [
        "429",
        "too_many_requests",
        "bulk",
        "post",
        "v1",
        "policies",
    ]
    assert tokenize("# Handling 429 ## ") == ["handling", "429"]


def test_tokenize_drops_every_stopword_so_a_stopword_query_has_no_terms() -> None:
    assert tokenize("the of and what is when can you it") == []


def test_retrieve_is_deterministic_for_identical_input() -> None:
    # Cross-process determinism holds too (no randomness, clock, network or global state in
    # retrieval.py, and PYTHONHASHSEED cannot reach the ranking because hits are sorted by
    # (-score, id), never by set or dict order), but a second in-process call is the cheap gate.
    first = retrieve(RATE_LIMIT_QUERY, k=5)
    second = retrieve(RATE_LIMIT_QUERY, k=5)
    assert _ids(first) == _ids(second)
    assert first.scores == second.scores
    assert first.top_score == second.top_score
    assert retrieve(RATE_LIMIT_QUERY, k=5) == first


def test_retrieve_uses_an_index_equivalent_to_one_built_from_the_corpus() -> None:
    hits = BM25.build(load_corpus()).search(RATE_LIMIT_QUERY, 5)
    result = retrieve(RATE_LIMIT_QUERY, k=5)
    assert [chunk.id for chunk, _ in hits] == _ids(result)
    assert [score for _, score in hits] == result.scores


def test_default_rate_limit_question_ranks_rate_limits_first() -> None:
    result = retrieve(RATE_LIMIT_QUERY, k=5)
    assert result.chunks[0].id == "rate_limits#1"
    assert result.top_score == result.scores[0]


def test_scores_are_positive_and_non_increasing_and_aligned_with_chunks() -> None:
    for query in SAMPLE_QUERIES:
        result = retrieve(query, k=5)
        assert len(result.chunks) == len(result.scores), query
        assert all(score > 0.0 for score in result.scores), query
        # strict=False on purpose: the second sequence is deliberately one element shorter.
        pairs = list(zip(result.scores, result.scores[1:], strict=False))
        assert all(earlier >= later for earlier, later in pairs), query
        assert result.top_score == (result.scores[0] if result.scores else None), query


def test_ties_are_broken_by_chunk_id_never_by_insertion_order() -> None:
    corpus = [
        Chunk(id="zeta#1", doc_id="zeta.md", title="Zeta", text="shared unique token here"),
        Chunk(id="alpha#1", doc_id="alpha.md", title="Alpha", text="shared unique token here"),
    ]
    hits = BM25.build(corpus).search("shared", k=5)
    assert [chunk.id for chunk, _ in hits] == ["alpha#1", "zeta#1"]
    assert len({score for _, score in hits}) == 1


def test_gibberish_query_returns_an_empty_result_with_no_top_score() -> None:
    result = retrieve(GIBBERISH_QUERY, k=5)
    assert result.chunks == []
    assert result.scores == []
    assert result.top_score is None


def test_stopword_only_query_returns_no_chunks() -> None:
    result = retrieve("the of and", k=5)
    assert result.chunks == []
    assert result.scores == []
    assert result.top_score is None


def test_k_bounds_the_number_of_results_and_zero_k_returns_nothing() -> None:
    for k in (1, 3, 5, 100):
        result = retrieve(RATE_LIMIT_QUERY, k=k)
        assert len(result.chunks) <= k, k
        assert len(result.scores) <= k, k
    for k in (0, -1):
        result = retrieve(RATE_LIMIT_QUERY, k=k)
        assert result.chunks == []
        assert result.scores == []
        assert result.top_score is None


def test_results_keep_the_markdown_doc_id_and_never_repeat_a_chunk_id() -> None:
    for query in SAMPLE_QUERIES:
        result = retrieve(query, k=100)
        assert result.chunks, query
        assert all(chunk.doc_id.endswith(".md") for chunk in result.chunks), query
        ids = _ids(result)
        assert len(set(ids)) == len(ids), query


def test_repeated_query_terms_do_not_change_the_ranking() -> None:
    once = retrieve("rate limit", k=5)
    twice = retrieve("rate limit rate limit", k=5)
    assert _ids(once) == _ids(twice)
    assert once.scores == twice.scores


def test_title_repetition_outranks_a_chunk_that_only_holds_the_term_in_its_body() -> None:
    corpus = [
        Chunk(id="body#1", doc_id="body.md", title="Widgets", text="flux capacitor widget"),
        Chunk(id="head#1", doc_id="head.md", title="Widget repair", text="flux capacitor widget"),
    ]
    hits = BM25.build(corpus).search("widget", k=5)
    assert [chunk.id for chunk, _ in hits] == ["head#1", "body#1"]


def test_synthetic_bm25_ranks_the_obviously_matching_chunk_first() -> None:
    hits = BM25.build(_synthetic_corpus()).search("widget", k=5)
    assert [chunk.id for chunk, _ in hits] == ["beta#1", "alpha#1"]
    # gamma#1 shares no token with the query, so it is dropped instead of filling a slot.
    assert all(chunk.id != "gamma#1" for chunk, _ in hits)


def test_synthetic_bm25_saturates_instead_of_scaling_with_term_frequency() -> None:
    # beta#1 repeats `widget` 5x as often as alpha#1; a 5x score would mean no saturation at all.
    # Both chunks have the same token count, so length normalisation cannot rescue the comparison.
    hits = BM25.build(_synthetic_corpus()).search("widget", k=5)
    scores = {chunk.id: score for chunk, score in hits}
    ratio = scores["beta#1"] / scores["alpha#1"]
    assert 1.0 < ratio < 3.0


def test_search_on_an_empty_index_returns_nothing() -> None:
    assert BM25.build([]).search("widget", k=5) == []


def test_default_index_can_be_rebuilt_from_a_monkeypatched_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This monkeypatch + cache_clear() pair is the exact mechanism Stage 8 uses to prove the
    # harness notices a dropped corpus document.
    only = Chunk(id="solo#1", doc_id="solo.md", title="Solo widget", text="A single chunk.")
    monkeypatch.setattr(retrieval, "load_corpus", lambda: [only])
    retrieval.default_index.cache_clear()

    result = retrieve("widget", k=5)
    assert _ids(result) == ["solo#1"]
    assert result.top_score == result.scores[0]

    monkeypatch.undo()
    retrieval.default_index.cache_clear()
    # "policy claim refund" spans several corpus documents, so one distinct doc_id would mean the
    # 1-chunk substitute index was somehow still cached.
    restored = retrieve("policy claim refund", k=5)
    assert len({chunk.doc_id for chunk in restored.chunks}) > 1


GOLDEN_PATH = Path(__file__).resolve().parent.parent / "eval" / "golden.jsonl"
# The id substituted into the tmp copy of the golden file by the rejection test below: a chunk the
# real corpus does not contain, so `load_golden` must refuse the whole file.
MISSING_CHUNK_ID = "no-such-chunk#1"


def _run_golden(k: int = DEFAULT_K) -> EvalResult:
    """Score the committed golden file through the harness at one cut-off."""
    return run(GOLDEN_PATH, k, DEFAULT_MIN_RECALL, DEFAULT_MIN_MRR)


def _normal_pass_count(k: int) -> int:
    """How many ``normal`` cases the harness passes at a single cut-off."""
    return sum(
        1
        for case in _run_golden(k).cases
        if case.case_type == TYPE_NORMAL and case.case_pass is True
    )


def _corrupt_golden_copy(tmp_path: Path) -> Path:
    """The golden file rewritten under ``tmp_path`` with one expected chunk id the corpus lacks."""
    lines = GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
    first: dict[str, object] = json.loads(lines[0])
    first["expected_chunk_ids"] = [MISSING_CHUNK_ID]
    lines[0] = json.dumps(first)
    corrupt = tmp_path / "golden-with-unknown-chunk.jsonl"
    corrupt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return corrupt


def test_the_shipped_golden_file_is_valid_because_its_exit_code_is_never_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 0 means every gate cleared and 1 means a gate failed; only 2 says load_golden refused the
    # file. The committed dataset must never land there, whatever the retriever is scoring.
    exit_code = main(["--golden", str(GOLDEN_PATH), "--k", str(DEFAULT_K)])
    capsys.readouterr()
    assert exit_code in (0, 1)


def test_abstention_cases_reach_the_report_as_recall_none_and_reciprocal_rank_zero() -> None:
    abstentions = [case for case in _run_golden().cases if case.case_type == TYPE_ABSTENTION]
    assert len(abstentions) == EXPECTED_COUNTS[TYPE_ABSTENTION]
    # The repo's central claim, asserted on the harness' own CaseResult objects rather than by
    # calling eval.metrics directly: absence of ground truth must not become a zero.
    assert [case.recall for case in abstentions] == [None] * len(abstentions)
    # 0.0 exactly, not pytest.approx: reciprocal_rank returns the literal 0.0 for no ground truth.
    assert [case.reciprocal_rank for case in abstentions] == [0.0] * len(abstentions)


def test_scoring_twice_replays_the_same_cases_and_a_byte_identical_report() -> None:
    first, second = _run_golden(), _run_golden()
    assert first.cases == second.cases
    # The report is the artifact a human reads, so compare the bytes it would be written as.
    first_bytes = report.render_markdown(first).encode("utf-8")
    second_bytes = report.render_markdown(second).encode("utf-8")
    assert first_bytes == second_bytes


def test_the_cli_k_override_reaches_scoring_with_11_14_and_15_normal_passes() -> None:
    # Measured on the committed dataset, not guessed: widening the cut-off from 1 to 5 recovers
    # three normal cases and 5 -> 10 one more. run() is what main() calls with the parsed --k, so a
    # flag that never reached the cut-off would give three identical counts here.
    assert {k: _normal_pass_count(k) for k in (1, 5, 10)} == {1: 11, 5: 14, 10: 15}


def test_a_golden_file_naming_an_unknown_expected_chunk_is_rejected_with_exit_code_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corrupt_path = _corrupt_golden_copy(tmp_path)
    exit_code = main(["--golden", str(corrupt_path), "--k", str(DEFAULT_K)])
    stderr = capsys.readouterr().err
    # Exit 2 is validation, not a failed gate: the file is refused before any metric is computed,
    # and the message names the invented id so whoever edited the dataset knows which line to fix.
    assert exit_code == 2
    assert MISSING_CHUNK_ID in stderr


# Stage 8 appends below this line: `Recall@5 >= 0.80` and `MRR >= 0.70` quality gates (each with the
# comment "if this fails, fix the retriever, not the assertion") plus the corpus-regression tripwire
# built on the monkeypatch + default_index.cache_clear() mechanism shown above.


# The thresholds are literals here on purpose, not DEFAULT_MIN_RECALL / DEFAULT_MIN_MRR: a
# threshold lowered in eval/retrieval_eval.py must turn these two tests redder, not carry them
# quietly to green. The measured values, by contrast, are never written down — they come from
# `run`, so `eval/metrics.py` stays the only place a metric is computed.
GATE_MIN_RECALL = 0.80
GATE_MIN_MRR = 0.70
# The document the tripwire drops, chosen because two normal cases name a chunk from it. Dropping
# a document the golden file never asks for would leave the pass set untouched and prove nothing.
CRITICAL_DOC_ID = "webhooks.md"


def _normal_gate_readings() -> dict[str, tuple[float | None, int]]:
    """The ``normal`` bucket's gate readings at the gate cut-off, keyed by the harness' own names.

    Each reading is ``(observed, n_cases)`` taken straight off ``EvalResult.gates`` — the same two
    numbers printed under ``## Gates`` in ``eval/report.md``. Nothing here recomputes a metric, so
    a metric bug can only live where it belongs, and neither threshold line can drift from the
    report a human reads.
    """
    return {
        gate.metric: (gate.observed, gate.n_cases)
        for gate in _run_golden(DEFAULT_K).gates
        if gate.bucket == TYPE_NORMAL
    }


def _normal_pass_ids(result: EvalResult) -> set[str]:
    """The ids of the ``normal`` cases the harness passes — the pass set the tripwire compares."""
    return {
        case.id for case in result.cases if case.case_type == TYPE_NORMAL and case.case_pass is True
    }


def test_recall_at_5_over_the_normal_bucket_only_meets_the_gate() -> None:
    # If this fails, fix the retriever. Not this assertion, not the 0.80, not golden.jsonl: all
    # three edits make the number mean nothing while making CI look fine.
    observed, n_cases = _normal_gate_readings()[f"recall@{DEFAULT_K}"]
    assert observed is not None, "the normal bucket has no recall at all, which is not a pass"
    assert observed >= GATE_MIN_RECALL, (
        f"recall@{DEFAULT_K} over the {n_cases} normal cases is {observed:.4f}, below the "
        f"{GATE_MIN_RECALL:.2f} gate. Fix the retriever, not this gate."
    )


def test_mrr_over_the_normal_bucket_only_meets_the_gate() -> None:
    # Same discipline as the recall gate: this assertion and this threshold are not the fix.
    # It is ``normal``-only because the abstention cases carry reciprocal_rank 0.0 by construction;
    # averaging them in would flatter BM25 with numbers that mean "no ground truth", not "missed".
    observed, n_cases = _normal_gate_readings()["mrr"]
    assert observed is not None, "the normal bucket has no reciprocal rank, which is not a pass"
    assert observed >= GATE_MIN_MRR, (
        f"mrr over the {n_cases} normal cases is {observed:.4f}, below the "
        f"{GATE_MIN_MRR:.2f} gate. Fix the retriever, not this gate."
    )


def test_the_harness_notices_when_a_critical_corpus_document_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim about the harness' sensitivity, not about what the corpus happens to contain.

    ``webhooks.md`` is only the lever. If the ``normal`` pass set survived losing a document two
    of its cases name chunks from, the harness would be scoring a corpus it never indexed and
    every figure in ``eval/report.md`` would be decoration.
    """
    before = _normal_pass_ids(_run_golden(DEFAULT_K))
    # The exact monkeypatch + cache_clear() pair of the rebuild test above. Only the index sees the
    # shrunken corpus: `run` still validates golden.jsonl against the real one, so this measures a
    # weaker retriever over a still-valid dataset rather than a golden file that stopped loading.
    corpus = [chunk for chunk in load_corpus() if chunk.doc_id != CRITICAL_DOC_ID]
    monkeypatch.setattr(retrieval, "load_corpus", lambda: corpus)
    retrieval.default_index.cache_clear()
    after = _normal_pass_ids(_run_golden(DEFAULT_K))
    # Restored before any assert, so a red assertion below cannot leave the shrunken index cached
    # for whatever test runs next, in whatever order pytest reaches it.
    monkeypatch.undo()
    retrieval.default_index.cache_clear()

    lost = sorted(before - after)
    assert lost, (
        f"dropping {CRITICAL_DOC_ID} cost the normal bucket no cases: the harness passed "
        f"{len(before)} of them with and without a document it should have missed."
    )
    assert _normal_pass_ids(_run_golden(DEFAULT_K)) == before, (
        "the monkeypatch leaked: the default index no longer scores the real corpus"
    )
