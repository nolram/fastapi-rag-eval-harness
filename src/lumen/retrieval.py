"""Pure-Python BM25 over the chunk corpus, plus the deterministic ``retrieve()`` entry point.

Purity is the contract: no randomness, no clock, no network, no global mutation, so identical input
gives identical ids and scores and the retrieval eval can run on every commit for free.
"""

import functools
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from lumen.chunking import Chunk, load_corpus

# Headings are the strongest lexical signal in this corpus, so the title is indexed twice.
# Repetition is a crude but fully auditable boost, not a learned weight.
TITLE_REPETITION = 2

# A lexical stopword list, not a language model: these exact tokens are dropped from the indexed
# stream and from every query, so every eval number in this repo depends on this set.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "of",
        "to",
        "for",
        "in",
        "on",
        "at",
        "and",
        "or",
        "i",
        "we",
        "you",
        "it",
        "its",
        "my",
        "our",
        "can",
        "how",
        "what",
        "when",
    }
)

# Markdown noise removed before splitting: inline-code backticks, emphasis asterisks and `#` heading
# markers. `_` survives on purpose: identifiers such as `too_many_requests` are content words here.
_MARKDOWN_NOISE = re.compile(r"[`*#]+")
# Any run of characters outside [a-z0-9_] is a separator, so X-RateLimit-Limit -> x ratelimit limit.
_TOKEN_SEPARATOR = re.compile(r"[^a-z0-9_]+")

# term -> postings of (chunk index, term frequency in that chunk), ordered by ascending chunk index.
_Postings = dict[str, list[tuple[int, int]]]


def tokenize(text: str) -> list[str]:
    """Lowercase, strip markdown noise, split on non-word runs, drop STOPWORDS and 1-char tokens."""
    cleaned = _MARKDOWN_NOISE.sub("", text.lower())
    return [
        token
        for token in _TOKEN_SEPARATOR.split(cleaned)
        if len(token) > 1 and token not in STOPWORDS
    ]


@dataclass(frozen=True, slots=True)
class RetrievedResult:
    """Retrieved chunks paired with their scores; ``top_score`` is None when nothing matched."""

    chunks: list[Chunk]
    scores: list[float]
    top_score: float | None


class BM25:
    """Okapi BM25 over a fixed chunk list, served from an inverted index built by ``build()``."""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        postings: _Postings,
        idf: dict[str, float],
        doc_lengths: list[int],
        avg_doc_length: float,
        k1: float,
        b: float,
    ) -> None:
        self._chunks: list[Chunk] = list(chunks)
        self._postings = postings
        self._idf = idf
        self._doc_lengths = doc_lengths
        self._avg_doc_length = avg_doc_length
        self._k1 = k1
        self._b = b

    @classmethod
    def build(cls, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> "BM25":
        """Index ``chunks``: each chunk's token stream is its title repeated, then its body.

        ``k1`` (term saturation) and ``b`` (length normalisation) are the only tuning knobs, on
        purpose: more knobs invite tuning against the eval set until the numbers mean nothing.
        """
        corpus: list[Chunk] = list(chunks)
        postings: _Postings = {}
        doc_lengths: list[int] = []
        for index, chunk in enumerate(corpus):
            tokens = _indexed_tokens(chunk)
            doc_lengths.append(len(tokens))
            # Appending per chunk keeps every postings list sorted by ascending chunk index.
            for term, term_frequency in Counter(tokens).items():
                postings.setdefault(term, []).append((index, term_frequency))
        # idf uses the +1 "scaled cosine" form so that a term in every chunk scores 0 rather
        # than negative. Chunk-free postings cannot exist, so avg_doc_length is only 0 when the
        # corpus itself is empty, in which case search() never divides by it.
        total_chunks = len(corpus)
        idf = {
            term: math.log(
                1.0 + (total_chunks - len(term_postings) + 0.5) / (len(term_postings) + 0.5)
            )
            for term, term_postings in postings.items()
        }
        avg_doc_length = sum(doc_lengths) / total_chunks if total_chunks else 0.0
        return cls(corpus, postings, idf, doc_lengths, avg_doc_length, k1, b)

    def search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        """Top ``k`` chunks by BM25 score, ties broken by chunk id, zero-score chunks dropped.

        Dropping zero-score chunks keeps ``top_score`` a meaningful abstention signal: a chunk that
        shares no token with the query must never occupy one of the ``k`` slots.
        """
        if k <= 0 or not self._chunks:
            return []
        scores = [0.0] * len(self._chunks)
        # dict.fromkeys keeps first-mention order while de-duplicating: each query term contributes
        # exactly once, so a repeated query word cannot inflate a chunk's score.
        for term in dict.fromkeys(tokenize(query)):
            term_postings = self._postings.get(term)
            if term_postings is None:
                continue
            term_weight = self._idf[term]
            for index, term_frequency in term_postings:
                length_norm = (
                    1.0 - self._b + self._b * self._doc_lengths[index] / self._avg_doc_length
                )
                scores[index] += (term_weight * term_frequency * (self._k1 + 1.0)) / (
                    term_frequency + self._k1 * length_norm
                )
        hits = [
            (chunk, score)
            for chunk, score in zip(self._chunks, scores, strict=True)
            if score > 0.0  # zero-score chunks are dropped, never truncated to k.
        ]
        hits.sort(key=lambda hit: (-hit[1], hit[0].id))
        return hits[:k]


def _indexed_tokens(chunk: Chunk) -> list[str]:
    """Title tokens repeated TITLE_REPETITION times, then body tokens (the indexed stream)."""
    title_tokens = tokenize(chunk.title)
    return title_tokens * TITLE_REPETITION + tokenize(chunk.text)


@functools.lru_cache(maxsize=1)
def default_index() -> BM25:
    """BM25 over ``load_corpus()``, built lazily on first use and cached for the process.

    Tests rebuild it by monkeypatching ``lumen.retrieval.load_corpus`` and calling
    ``default_index.cache_clear()`` — Stage 8's corpus-regression tripwire relies on exactly that.
    """
    return BM25.build(load_corpus())


def retrieve(query: str, k: int = 5) -> RetrievedResult:
    """Search the default corpus index. Pure and deterministic: same query, same k, same result."""
    hits = default_index().search(query, k)
    scores = [score for _, score in hits]
    return RetrievedResult(
        chunks=[chunk for chunk, _ in hits],
        scores=scores,
        top_score=scores[0] if scores else None,
    )
