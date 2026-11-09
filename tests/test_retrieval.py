"""Behaviour tests for BM25 and ``retrieve()``: determinism, ordering, abstention, index rebuild.

Unit-level only — no quality gates and no numeric thresholds here. The comment at the bottom of this
file marks where Stage 8 appends the Recall@5 / MRR gates and the corpus-regression tripwire.
"""

import dataclasses
from inspect import signature

import pytest

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


# Stage 8 appends below this line: `Recall@5 >= 0.80` and `MRR >= 0.70` quality gates (each with the
# comment "if this fails, fix the retriever, not the assertion") plus the corpus-regression tripwire
# built on the monkeypatch + default_index.cache_clear() mechanism shown above.
