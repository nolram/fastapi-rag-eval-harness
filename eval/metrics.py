"""Retrieval metrics over (retrieved, relevant) chunk ids, with abstention-safe ``None`` semantics.

Pure stdlib functions: no file I/O, no clock, no ranker coupling, so any retriever can be scored.
"""

import math
from collections.abc import Collection, Sequence


def recall_at_k(retrieved: Sequence[str], relevant: Collection[str], k: int) -> float | None:
    """Share of the ground truth found in the top ``k``. ``None`` when there was nothing to find."""
    _require_positive_k(k)
    if not relevant:
        # Abstention case: no ground truth, so 0.0 would be a lie and would poison the mean.
        return None
    # Set intersection: a duplicate id in `retrieved` can only be counted once, by construction.
    hits = len(set(retrieved[:k]) & set(relevant))
    return hits / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: Collection[str], k: int) -> float | None:
    """Share of the top ``k`` slots that are relevant. ``None`` when ``relevant`` is empty."""
    _require_positive_k(k)
    if not relevant:
        return None
    # Denominator is k, never len(retrieved): returning fewer chunks must not flatter precision.
    hits = len(set(retrieved[:k]) & set(relevant))
    return hits / k


def reciprocal_rank(retrieved: Sequence[str], relevant: Collection[str]) -> float:
    """1 / 1-based rank of the first relevant hit, else ``0.0``. Never ``None``, never raises."""
    # Deliberate asymmetry with the other three metrics: RR stays numeric for abstention cases, so
    # MRR means include them and are knowingly diluted by them. Stage 6 reports the counts.
    relevant_ids = set(relevant)
    for rank, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Collection[str], k: int) -> float | None:
    """nDCG@k with binary relevance, credit decaying as 1/log2(pos+1). ``None`` if no truth."""
    _require_positive_k(k)
    if not relevant:
        return None
    relevant_ids = set(relevant)
    seen: set[str] = set()
    dcg = 0.0
    for position, chunk_id in enumerate(retrieved[:k], start=1):
        # `seen` is why a repeated id cannot earn credit a second time at a deeper position.
        if chunk_id in relevant_ids and chunk_id not in seen:
            dcg += 1.0 / math.log2(position + 1)
        seen.add(chunk_id)
    # IDCG assumes the best possible ordering: min(|relevant|, k) relevant chunks in the top slots.
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_hits + 1))
    # Clamp for float safety: DCG <= IDCG mathematically, but summing reciprocals of logs can
    # overshoot in the last bits and hand the caller a 1.0000000000000002.
    return min(dcg / idcg, 1.0)


def mean_excluding_none(values: Sequence[float | None]) -> tuple[float | None, int, int]:
    """``(mean, n_included, n_excluded)`` over non-``None`` values; never maps ``None`` to 0.0."""
    included = [value for value in values if value is not None]
    n_excluded = len(values) - len(included)
    if not included:
        # A mean over nothing is undefined, not 0.0: reporting 0.0 here would invent a score.
        return None, 0, n_excluded
    return sum(included) / len(included), len(included), n_excluded


def _require_positive_k(k: int) -> None:
    """``k <= 0`` is a caller bug, not a data case: raise instead of returning a neutral value."""
    if k <= 0:
        raise ValueError(f"k must be a positive int, got {k}")
