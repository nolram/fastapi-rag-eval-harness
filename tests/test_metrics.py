"""Hand-computed metric checks for ``eval/metrics.py`` — the arithmetic is in the comments.

The contrast that organises this file: an abstention case has no ground truth, so Recall / Precision
/ nDCG are ``None`` (and excluded from means) while Reciprocal Rank stays ``0.0``.
"""

import json
import math
from collections.abc import Callable, Collection, Sequence
from inspect import signature
from pathlib import Path

import pytest

from eval.metrics import (
    mean_excluding_none,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "eval" / "golden.jsonl"

# The three metrics that take a cut-off; all three must reject k <= 0 as a caller bug.
K_METRICS: tuple[Callable[[Sequence[str], Collection[str], int], float | None], ...] = (
    recall_at_k,
    precision_at_k,
    ndcg_at_k,
)


def test_no_ground_truth_is_none_for_the_three_k_metrics_but_zero_for_reciprocal_rank() -> None:
    # The spec's core contrast, on a non-empty retrieval so it cannot be an empty-list artefact.
    retrieved = ["rate_limits#1", "auth#2"]
    assert recall_at_k(retrieved, [], k=5) is None
    assert recall_at_k(retrieved, set(), k=5) is None
    assert precision_at_k(retrieved, [], k=5) is None
    assert ndcg_at_k(retrieved, [], k=5) is None
    assert reciprocal_rank(retrieved, []) == 0.0
    assert reciprocal_rank([], set()) == 0.0


def test_perfect_retrieval_scores_one_on_every_metric() -> None:
    retrieved = ["a#1", "b#2", "c#3"]
    relevant = {"a#1", "b#2", "c#3"}
    assert recall_at_k(retrieved, relevant, k=3) == 1.0  # 3 / 3
    assert precision_at_k(retrieved, relevant, k=3) == 1.0  # 3 / 3 (k == |relevant|)
    # DCG = 1/log2(2) + 1/log2(3) + 1/log2(4) == IDCG exactly, so nDCG is clamped to 1.0.
    assert ndcg_at_k(retrieved, relevant, k=3) == 1.0
    assert reciprocal_rank(retrieved, relevant) == 1.0  # first hit at rank 1 -> 1 / 1


def test_partial_hit_is_scored_by_hand() -> None:
    retrieved = ["x", "b", "y", "z", "c"]
    relevant = {"a", "b", "c"}
    assert recall_at_k(retrieved, relevant, k=5) == pytest.approx(2 / 3)  # |{b, c}| / |{a, b, c}|
    assert precision_at_k(retrieved, relevant, k=5) == pytest.approx(2 / 5)  # 2 hits / 5 slots
    assert reciprocal_rank(retrieved, relevant) == pytest.approx(1 / 2)  # first hit is rank 2

    # DCG  = 1/log2(2+1) + 1/log2(5+1) = 1/log2(3) + 1/log2(6) = 0.630930 + 0.386853 = 1.017783
    # IDCG = 1/log2(2) + 1/log2(3) + 1/log2(4) = 1 + 0.630930 + 0.5 = 2.130930 (all 3 on top)
    # nDCG = 1.017783 / 2.130930 = 0.477624
    assert ndcg_at_k(retrieved, relevant, k=5) == pytest.approx(0.4776237035032179)


def test_hit_at_rank_six_scores_nothing_in_a_top_five_window_and_positive_in_a_top_ten() -> None:
    retrieved = [f"junk#{n}" for n in range(1, 6)] + ["target#1"]  # target is the 6th (1-based)
    relevant = {"target#1"}

    assert recall_at_k(retrieved, relevant, k=5) == 0.0  # 0 of 1 relevant inside the cut
    assert precision_at_k(retrieved, relevant, k=5) == 0.0  # 0 / 5
    # reciprocal_rank has no k parameter: Stage 6 hands it the same window, so rank 6 is invisible.
    assert reciprocal_rank(retrieved[:5], relevant) == 0.0

    assert recall_at_k(retrieved, relevant, k=10) == 1.0  # 1 / 1, the window now reaches rank 6
    assert precision_at_k(retrieved, relevant, k=10) == pytest.approx(1 / 10)  # 1 / 10
    assert reciprocal_rank(retrieved[:10], relevant) == pytest.approx(1 / 6)  # 1 / 6 = 0.166667
    # DCG = 1/log2(6+1) = 0.356207, IDCG = 1/log2(2) = 1 (only one relevant chunk exists).
    assert ndcg_at_k(retrieved, relevant, k=10) == pytest.approx(1 / math.log2(7))


def test_precision_denominator_is_k_not_len_retrieved() -> None:
    # One chunk came back and it is the right one: recall is perfect, precision is deliberately not.
    assert recall_at_k(["a#1"], {"a#1"}, k=5) == 1.0  # 1 / 1
    assert precision_at_k(["a#1"], {"a#1"}, k=5) == pytest.approx(0.2)  # 1 / 5, not 1 / 1
    # DCG = 1/log2(2) = 1, IDCG = 1/log2(2) = 1 -> perfect ordering despite the short result list.
    assert ndcg_at_k(["a#1"], {"a#1"}, k=5) == pytest.approx(1.0)


def test_ndcg_rewards_correct_ordering_while_recall_stays_identical() -> None:
    relevant = {"a#1"}
    ranked_first = ["a#1", "x#1", "y#1", "z#1", "w#1"]
    ranked_fifth = ["w#1", "x#1", "y#1", "z#1", "a#1"]

    # Recall and Precision@5 are position-blind: both orderings read the same, 1/1 and 1/5.
    assert recall_at_k(ranked_first, relevant, k=5) == recall_at_k(ranked_fifth, relevant, k=5)
    assert precision_at_k(ranked_first, relevant, k=5) == pytest.approx(0.2)  # 1 hit / 5 slots
    assert precision_at_k(ranked_fifth, relevant, k=5) == pytest.approx(0.2)  # 1 hit / 5 slots

    # nDCG and RR are the position-sensitive pair: IDCG = 1/log2(2) = 1 for a single relevant chunk.
    first_ndcg = ndcg_at_k(ranked_first, relevant, k=5)
    fifth_ndcg = ndcg_at_k(ranked_fifth, relevant, k=5)
    assert first_ndcg == pytest.approx(1.0)  # DCG = 1/log2(2) = 1
    assert fifth_ndcg == pytest.approx(0.3868528072345416)  # DCG = 1/log2(6) = 0.386853
    assert first_ndcg is not None and fifth_ndcg is not None  # |relevant| > 0, so not the None path
    assert first_ndcg > fifth_ndcg
    assert reciprocal_rank(ranked_first, relevant) == 1.0  # 1 / 1
    assert reciprocal_rank(ranked_fifth, relevant) == pytest.approx(0.2)  # 1 / 5


def test_duplicate_ids_in_retrieved_do_not_inflate_recall_or_ndcg() -> None:
    retrieved = ["b#1", "b#1", "x#1", "y#1"]
    relevant = {"b#1", "c#1"}
    assert recall_at_k(retrieved, relevant, k=4) == pytest.approx(0.5)  # |{b}| / 2, not 2 / 2
    assert precision_at_k(retrieved, relevant, k=4) == pytest.approx(0.25)  # 1 hit / 4 slots
    assert reciprocal_rank(retrieved, relevant) == 1.0  # first relevant hit is rank 1

    # DCG counts b#1 once at rank 1: 1/log2(2) = 1. IDCG for 2 ideal hits: 1 + 1/log2(3) = 1.630930.
    # nDCG = 1 / 1.630930 = 0.613147. Without the dedup, the second b#1 at rank 2 would add 0.630930
    # and make DCG == IDCG, i.e. a false nDCG of 1.0 from a chunk the corpus only holds once.
    duplicates_ndcg = ndcg_at_k(retrieved, relevant, k=4)
    assert duplicates_ndcg == pytest.approx(0.6131471927654584)
    assert duplicates_ndcg is not None
    assert duplicates_ndcg < 1.0


def test_a_miss_on_a_case_that_has_ground_truth_is_zero_not_none() -> None:
    # The other half of the contrast: 0.0 = the retriever failed, None = nothing was findable.
    assert recall_at_k([], {"a#1"}, k=5) == 0.0
    assert precision_at_k([], {"a#1"}, k=5) == 0.0
    assert ndcg_at_k([], {"a#1"}, k=5) == 0.0
    assert reciprocal_rank([], {"a#1"}) == 0.0


@pytest.mark.parametrize("k", [0, -1, -7])
def test_non_positive_k_raises_on_all_three_k_metrics(k: int) -> None:
    for metric in K_METRICS:
        with pytest.raises(ValueError, match="positive"):
            metric(["a#1"], {"a#1"}, k)  # positional: the Callable type above erases the k= name


@pytest.mark.parametrize("k", [0, -3])
def test_non_positive_k_raises_even_without_ground_truth_because_it_is_a_caller_bug(k: int) -> None:
    for metric in K_METRICS:
        with pytest.raises(ValueError, match="positive"):
            metric(["a#1"], [], k)  # positional, for the same reason as above


def test_reciprocal_rank_has_no_k_parameter_and_never_raises_on_empty_retrieval() -> None:
    assert "k" not in signature(reciprocal_rank).parameters
    assert reciprocal_rank([], {"a#1"}) == 0.0
    assert reciprocal_rank(["x#1"], set()) == 0.0
    assert reciprocal_rank([], set()) == 0.0


def test_relevant_may_be_a_list_because_golden_jsonl_stores_one() -> None:
    relevant = ["auth#4", "rate_limits#1"]
    assert recall_at_k(["rate_limits#1"], relevant, k=5) == pytest.approx(0.5)  # 1 / 2
    assert precision_at_k(["rate_limits#1"], relevant, k=5) == pytest.approx(0.2)  # 1 / 5
    assert reciprocal_rank(["rate_limits#1"], relevant) == 1.0


def test_mean_excluding_none_reports_the_mean_and_both_counts() -> None:
    assert mean_excluding_none([0.5, None, 1.0]) == (0.75, 2, 1)  # (0.5 + 1.0) / 2, one skipped
    assert mean_excluding_none([None, None]) == (None, 0, 2)
    assert mean_excluding_none([]) == (None, 0, 0)
    assert mean_excluding_none([0.4]) == (0.4, 1, 0)


def test_none_is_never_silently_zeroed_when_averaging_recall_over_abstention_cases() -> None:
    # The hazard this module exists to prevent: 6 of the 30 golden cases have no ground truth.
    # Zero-filling them would report (0.4 + 0.0) / 2 = 0.2 in the README as if the retriever failed.
    mean, included, excluded = mean_excluding_none([0.4, None])
    assert mean == pytest.approx(0.4)  # 0.4 / 1, and definitely not 0.2
    assert (included, excluded) == (1, 1)


def test_a_mean_of_all_none_is_none_rather_than_nan_or_zero() -> None:
    mean, included, excluded = mean_excluding_none([None, None, None])
    assert mean is None
    assert (included, excluded) == (0, 3)


def test_metrics_never_mutate_their_arguments() -> None:
    retrieved = ["x#1", "b#1", "y#1"]
    relevant = ["b#1", "c#1"]
    retrieved_before, relevant_before = list(retrieved), list(relevant)

    recall_at_k(retrieved, relevant, k=2)
    precision_at_k(retrieved, relevant, k=2)
    ndcg_at_k(retrieved, relevant, k=2)
    reciprocal_rank(retrieved, relevant)
    mean_excluding_none([0.1, None])

    assert retrieved == retrieved_before
    assert relevant == relevant_before


def test_the_six_abstention_cases_of_golden_jsonl_have_nothing_to_find() -> None:
    # Reading golden.jsonl is a test-side concern only: eval/metrics.py itself does no file I/O.
    cases = [json.loads(line) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]
    abstentions = [case for case in cases if case["type"] == "abstention"]
    assert len(cases) == 30
    assert len(abstentions) == 6

    for case in abstentions:
        assert case["expected_chunk_ids"] == []
        # Scored against any retrieval at all, the at-k metrics refuse to average a lie.
        assert recall_at_k(["webhooks#2", "errors#1"], case["expected_chunk_ids"], k=5) is None
        assert ndcg_at_k(["webhooks#2", "errors#1"], case["expected_chunk_ids"], k=5) is None
        assert reciprocal_rank(["webhooks#2", "errors#1"], case["expected_chunk_ids"]) == 0.0
