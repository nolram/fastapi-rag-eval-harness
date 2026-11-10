"""Pure-Python BM25 over the chunk corpus, plus the deterministic ``retrieve()`` entry point.

Purity is the contract: no randomness, no clock, no network, no global mutation, so identical input
gives identical ids and scores and the retrieval eval can run on every commit for free.

``tokenize()`` folds regular inflectional suffixes (``locked``/``locking``/``locks`` -> ``lock``),
so
BM25 has morphology without having a lexicon. The rules are a pure function of one token: they never
read the corpus, the query or the golden set, which is what keeps the folding from becoming a lookup
table for the eval data. See ``_SUFFIX_RULES`` for the generalisation each rule encodes.

Known risks of over-folding, i.e. the word pairs this tokenizer deliberately merges into one term:
- ``-s`` after a sibilant makes ``clause``/``clauses`` and ``because``/``becaus`` the same term, and
  the ``-s`` rule cannot see the difference: ``cause`` + ``s`` and ``laus``e + ``s`` are spelled
  alike.
- ``-er`` and ``-est`` are NOT folded, because ``-er`` is also a noun suffix (``broker``,
  ``filter``,
  ``counter``): comparative folding needs to know a word's part of speech, which is a lexicon.
- ``-ing`` and ``-ed`` are folded without restoring a silent ``e``, so ``using``/``uses`` collapse
  to
  ``us``/``use`` while ``using`` stays ``using``, and ``make``/``making``/``made`` stay three terms.
  That splits word families instead of merging unrelated words: the safe direction.
- ``-ing`` on a noun-like stem folds to a fragment (``during`` -> ``dur``, ``during`` never meets
  ``dure``), and ``-s`` on a Latin-shaped singular does too (``bias`` -> ``bia``, ``canvas`` ->
  ``canv``). Such a fragment only ever merges with itself; it does not collide with a real word
  here.
- ``was``/``is``, ``this``/``his``, ``business``/``busi``, ``class``/``clas`` and
  ``status``/``statu``
  are the collisions a naive stemmer produces, and the guards in ``_SUFFIX_RULES`` exist to keep
  them
  apart; ``tests/test_retrieval.py`` pins them.
"""

import functools
import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
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

_VOWELS = "aeiou"
# The consonants English writes twice before a vowel-initial suffix (`stop` + `ing`), i.e. the ones
# the undoubling below may undo. `s` and `z` are excluded because they are doubled by spelling
# convention, not by stress: `passed` must fold to `pass`, never to `pas`. `l` is excluded because
# it
# needs the measure test in ``_undoubled`` — `fill` and `call` simply end in a doubled `l` instead
# of
# having one added by a suffix.
_DOUBLABLE_CONSONANTS = "bcdfghijkmnpqrtvwxy"
# A stem shorter than this is a fragment, not a word. The floor is what stops `using` -> `us`,
# `being` -> `be`, `his` -> `hi`, `was` -> `wa` and `ids` -> `id`: none of those leftovers is long
# enough to be an English word, so the fold that would produce it is refused.
_MIN_STEM = 3


def _is_consonant(text: str) -> bool:
    """True for a single letter that is not a vowel. Digits and `_` are not consonants either."""
    return len(text) == 1 and text.isalpha() and text not in _VOWELS


def _ends_with_sibilant(stem: str) -> bool:
    """True when ``stem`` ends in a sibilant spelled so that `-es` is unambiguously the suffix.

    `ss`, `x`, `ch`, `sh` (pass/passes, box/boxes, match/matches, crash/crashes). A single `s` at
    the
    cut is refused, because the spelling cannot tell `bus` + `es` from `case` + `s`: folding it
    would
    turn `cases` into `cas` and `uses` into `us`. Losing `buses` -> `bus` is the price of that.
    """
    return stem.endswith(("ss", "x", "ch", "sh"))


def _measure(stem: str) -> int:
    """Count the vowel-consonant groups in ``stem`` (Porter's measure): `fill` = 1, `cancell` =
    2."""
    return len(re.findall(r"[aeiou]+[^aeiou]+", stem))


def _dropped(stem: str) -> str:
    """The fold for a suffix that is simply removed: the stem it left behind is already the
    answer."""
    return stem


def _undoubled(stem: str) -> str:
    """Undo a consonant doubling the suffix caused: `stop` + `ing` -> `stopp` -> `stop`.

    English doubles the consonant after a short stressed vowel when a vowel-initial suffix follows
    (``stop``/``stopping``, ``occur``/``occurred``, ``refer``/``referring``,
    ``commit``/``committing``),
    so the doubling has to come off with the suffix. ``l`` is the awkward case: ``cancel`` +
    ``ling``
    and ``travel`` + ``led`` double it at the suffix boundary, while ``fill``, ``call`` and
    ``telling``
    just end in one — identical at the cut, so only the measure test separates them (``_measure``).
    """
    last = stem[-1]
    if len(stem) < 2 or stem[-2] != last:
        return stem
    if last == "l":
        return stem[:-1] if _measure(stem) > 1 else stem
    return stem[:-1] if last in _DOUBLABLE_CONSONANTS else stem


# One inflection fold: the suffix to strip, the stem transform that follows it, and the guard that
# licenses the rule. A guard sees only the stem — the token with the suffix removed — so the fold
# stays a pure function of one token: it never sees the corpus, the query or the golden set.
@dataclass(frozen=True, slots=True)
class _SuffixRule:
    """One fold: strip ``suffix`` from a token, then hand the remaining stem to ``fold``."""

    suffix: str
    fold: Callable[[str], str]
    # Every floor here is measured on the stem, not on the token, because the stem is the only thing
    # a rule can produce a fragment out of.
    applies: Callable[[str], bool]


# Ordered by descending suffix length, then by precedence within a length. The first rule a token
# matches is the only rule applied to it, so a token is never rewritten twice in one pass. Each
# comment states the English generalisation that licenses the line — that is the bar for a line
# existing here, and no line was added, kept or retired because of an eval number.
_SUFFIX_RULES: tuple[_SuffixRule, ...] = (
    _SuffixRule(
        # A consonant + `y` noun pluralises to `-ies` (policy/policies, territory/territories) and
        # such a verb past-tenses to `-ied` (try/tried, carry/carried): both fold back onto the `y`.
        # The floor is what keeps `dies` and `ties` from becoming `dy` and `ty`; `movies` -> `movey`
        # is the harmless fragment no spelling test can catch.
        "ies",
        lambda stem: stem + "y",
        lambda stem: _is_consonant(stem[-1:]) and len(stem) + 1 >= _MIN_STEM,
    ),
    _SuffixRule(
        # `-ied` is the past tense of exactly the consonant + `y` verbs above (try/tried,
        # carry/carried,
        # apply/applied), so it folds to `y` by the same generalisation and under the same floor.
        "ied",
        lambda stem: stem + "y",
        lambda stem: _is_consonant(stem[-1:]) and len(stem) + 1 >= _MIN_STEM,
    ),
    _SuffixRule(
        # `-eed` carries only a `d` of suffix, because `ee` is the stem itself (agree/agreed,
        # free/freed). The floor keeps one-letter stems out: `seed` -> `see` would be a collision,
        # not a fold.
        "eed",
        lambda stem: stem + "ee",
        lambda stem: len(stem) >= 2,
    ),
    _SuffixRule(
        # A stem ending in a sibilant takes `-es` rather than `-s` (pass/passes, box/boxes,
        # match/matches, crash/crashes), so the whole `-es` is suffix and the `ss` of `passes` folds
        # back to `pass` intact. See ``_ends_with_sibilant`` for the spellings this refuses.
        "es",
        _dropped,
        _ends_with_sibilant,
    ),
    _SuffixRule(
        # `-s` marks the plural and the 3rd-person singular (attempt/attempts, limit/limits,
        # key/keys). The three guards are for stems left ending in a letter that belongs to
        # uninflected words: `s` means an `ss` word (class, business, missing), `u` an `us` word
        # (status, corpus, bonus) and `i` an `is` word (this, is, axis). Folding those would destroy
        # the word rather than its suffix — the one failure mode this rule must not have.
        "s",
        _dropped,
        lambda stem: len(stem) >= _MIN_STEM and not stem.endswith(("s", "u", "i")),
    ),
    _SuffixRule(
        # `-ing` is the progressive and the gerund (lock/locking, retry/retrying, refer/referring).
        # `using`, `being` and `doing` are stopped by the stem floor, which is stated as a length
        # and
        # never as a word list — that is what keeps the rule general instead of fitted.
        "ing",
        _undoubled,
        lambda stem: len(stem) >= _MIN_STEM,
    ),
    _SuffixRule(
        # `-ed` is the past tense (lock/locked, fail/failed, hash/hashed). British spelling doubles
        # a
        # final `l` before it (cancel/cancelled, travel/travelled) and `stop` doubles its `p`
        # (stopped): ``_undoubled`` undoes both, and `passed` keeps its `ss`.
        "ed",
        _undoubled,
        lambda stem: len(stem) >= _MIN_STEM,
    ),
)


def _fold_once(token: str) -> str:
    """Apply the first suffix rule the token matches, or return the token untouched."""
    for rule in _SUFFIX_RULES:
        if not token.endswith(rule.suffix):
            continue
        stem = token[: -len(rule.suffix)]
        if rule.applies(stem):
            return rule.fold(stem)
    return token


def _fold_suffix(token: str) -> str:
    """Fold one token's inflectional suffix: `locked` -> `lock`, `attempts` -> `attempt`, `class` ->
    `class`.

    Pure and idempotent: ``_fold_suffix(_fold_suffix(t)) == _fold_suffix(t)`` for every input,
    because
    a fold whose result is itself matched by another rule is refused. That check is what stops
    `closed` -> `clos` from going on to `clo`: a second fold on a fragment is not a fold of the
    original word, so the fragment is rejected and the word is indexed as written.
    """
    folded = _fold_once(token)
    if folded == token:
        return token
    return folded if _fold_once(folded) == folded else token


def tokenize(text: str) -> list[str]:
    """Lowercase, strip markdown noise, split on non-word runs, drop STOPWORDS and 1-char tokens."""
    cleaned = _MARKDOWN_NOISE.sub("", text.lower())
    return [
        folded
        for folded in (
            _fold_suffix(token) for token in _TOKEN_SEPARATOR.split(cleaned) if len(token) > 1
        )
        if folded not in STOPWORDS
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
