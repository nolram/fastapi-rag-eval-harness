"""Generation eval over the hand-labelled golden set: measurement, and deliberately no gate.

The pipeline is fixed: read ``eval/golden.jsonl``, retrieve with BM25 at each case's own ``k`` (or
one uniform ``--k``), call ``lumen.generation.answer()`` — live only when ``LLM_API_KEY`` is set,
otherwise the run skips — compute the deterministic answer metrics, then ask a judge model about
that answer. Nothing here decides pass or fail: no threshold, no ``--min-*`` flag, no exit code for
an ugly number, because picking a threshold before seeing the distribution is the anti-pattern
``AGENT.md`` records as I5. The only non-zero exit is a genuine harness error.

Two facts shape every judge number in the report. The answerer and the judge are the **same model**
behind the same endpoint, so self-preference bias is a live configuration of this harness rather
than a footnote; and the judge **never sees a golden label** — ``eval/golden.jsonl`` is
human-authored and is the only independent signal in the repo, so judge and golden stay two
separate measurements that the calibration section can put side by side.

Credential discipline mirrors ``lumen.generation``: the key is read only to build the
``Authorization`` header of the one judge request. Nothing here logs it, puts it in an exception
message, or writes it to the report; a provider failure is reported by shape, never by body.

Deterministic apart from the provider itself: no clock, no randomness, tables iterate by case id,
and every rate prints at 4 decimals. Skipped mode touches no socket and writes no file.
"""

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from eval.metrics import mean_excluding_none
from eval.retrieval_eval import CASE_TYPES, TYPE_NORMAL, GoldenCase, GoldenFileError, load_golden
from lumen import config, generation
from lumen.chunking import Chunk, load_corpus
from lumen.config import Settings
from lumen.prompts import PROMPT_VERSION
from lumen.retrieval import retrieve

DEFAULT_GOLDEN = "eval/golden.jsonl"
DEFAULT_REPORT = "eval/generation_report.md"
#: The line CI sees when no provider is configured; the tests match this substring verbatim.
SKIPPED_LINE = (
    "skipped: no LLM_API_KEY — the generation eval needs a live provider, so nothing ran and no "
    "report was written. Retrieval is still measured by `make eval-retrieval`."
)
# A judge reply is either parsed or an error. An error is counted and excluded, never raised.
STATUS_OK, STATUS_ERROR = "ok", "error"
# Reported by shape only, like `lumen.generation._TRANSPORT_FAILURE`: an httpx exception message can
# carry the request URL, and nothing in a failure message is worth that risk.
JUDGE_TRANSPORT_FAILURE = "judge provider could not be reached"
JUDGE_PROMPT_VERSION: str = "v1"
#: The exact key set the judge must return. A missing or extra key is a parse failure, not a false.
JUDGE_KEYS = frozenset({"supported", "should_have_refused", "reason"})
#: The two comparisons this dataset can support, named once so report prose cannot drift.
CHECK_ABSTENTION = "human says refuse vs judge should_have_refused"
CHECK_MUST_NOT_INCLUDE = "human says must_not_include violated vs judge supported=false"
#: A fence line (```json and its closer) is markup around the payload, never part of the payload.
_FENCE_LINE = re.compile(r"^[ \t]*(?:```|~~~)[ \t]*\w*[ \t]*$", re.MULTILINE)


class JudgeResponseError(Exception):
    """The judge reply could not be turned into the contracted verdict."""


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """One judge reply: its two booleans and reason, or ``status="error"`` and the failure shape.

    A failure keeps the booleans ``None``: "the judge could not be read" is a different claim from
    "the judge said false", and collapsing them would move the agreement rate silently.
    """

    status: str
    supported: bool | None
    should_have_refused: bool | None
    reason: str


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """One case end to end: what retrieval offered, what the answer said, what the judge said.

    The deterministic metrics are stored beside their inputs, so the renderer never recomputes one.
    ``answer_error`` is "" when an answer exists; otherwise every field that would need answer text
    holds the empty/``None`` value and every mean excludes the case.
    """

    id: str
    case_type: str
    query: str
    k: int
    #: The human label, kept for the calibration comparison and the per-case table only.
    should_abstain: bool
    retrieved_ids: tuple[str, ...]
    answer_mode: str
    answer_error: str
    text: str
    abstained: bool
    citations: tuple[str, ...]
    must_include_coverage: float | None
    must_include_missing: tuple[str, ...]
    must_not_include_present: tuple[str, ...]
    abstention_match: bool | None
    cites_nothing: bool
    cited_but_not_retrieved: tuple[str, ...]
    judge: JudgeVerdict

    @property
    def measured(self) -> bool:
        """False when the answerer failed: there is no answer text to measure or to judge."""
        return not self.answer_error

    @property
    def judged(self) -> bool:
        """True only when the judge returned exactly the contracted key set."""
        return self.judge.status == STATUS_OK


@dataclass(frozen=True, slots=True)
class BucketMean:
    """One metric over one bucket: the mean and both counts behind it. There is no blended row."""

    case_type: str
    value: float | None
    n_included: int
    n_excluded: int


@dataclass(frozen=True, slots=True)
class BucketStats:
    """One case type's deterministic numbers and judge counts.

    Never blended with another bucket: the abstention cases carry no ``must_include`` at all, so
    their coverage is ``None`` rather than ``0.0``, and the ambiguous cases are the only ones whose
    answer is supposed to name two markets. A blended mean moves when a case is added, not when the
    answerer improves — the failure this repo documents and CI has caught before.
    """

    case_type: str
    n_cases: int
    must_include_coverage: BucketMean
    abstention_match: BucketMean
    context_offered: BucketMean
    context_cited: BucketMean
    n_cites_nothing: int
    cites_nothing_ids: tuple[str, ...]
    n_cited_but_not_retrieved: int
    cited_but_not_retrieved_ids: tuple[str, ...]
    n_answer_errors: int
    answer_error_ids: tuple[str, ...]
    n_judged: int
    n_judge_errors: int
    judge_error_ids: tuple[str, ...]
    n_supported: int
    n_unsupported: int
    n_should_have_refused: int
    n_answer_should_stand: int


@dataclass(frozen=True, slots=True)
class Agreement:
    """Judge-vs-golden agreement on one named check, kept beside both of its denominators."""

    name: str
    value: float | None
    n_agreed: int
    n_compared: int
    n_excluded: int


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One case where the human label and the judge verdict point opposite ways. Printed whole."""

    id: str
    case_type: str
    check: str
    human_label: str
    judge_verdict: str
    reason: str


@dataclass(frozen=True, slots=True)
class Calibration:
    """The judge-vs-golden comparison, and the counts that bound what it could compare."""

    agreements: tuple[Agreement, ...]
    disagreements: tuple[Disagreement, ...]
    n_should_abstain_labelled: int
    n_normal_with_violated_must_not_include: int


@dataclass(frozen=True, slots=True)
class EvalRun:
    """Everything the renderer prints, produced by :func:`run` and never mutated afterwards."""

    golden_path: str
    report_path: str
    k_override: int | None
    file_k_values: tuple[int, ...]
    limit: int | None
    n_cases_in_file: int
    model: str
    judge_model: str
    base_url: str
    answer_prompt_version: str
    answer_mode: str
    cases: tuple[CaseOutcome, ...]
    stats: tuple[BucketStats, ...]
    calibration: Calibration


# ----------------------------------------------------------- deterministic metrics: pure functions
# of (the golden labels, the answer text). No judge, no network, no clock.


def _present(labels: Sequence[str], haystack: str) -> tuple[str, ...]:
    """The listed strings found in ``haystack``, case-insensitively, in the listed order.

    Substring matching, because a golden string sits on one source line and the answerer may
    surround it with its own sentence. Not smarter than that on purpose: an answer that split the
    string across a line break counts as missing, which is the honest reading of the label.
    """
    lowered = haystack.lower()
    return tuple(label for label in labels if label.lower() in lowered)


def must_include_coverage(must_include: Sequence[str], answer_text: str) -> float | None:
    """Share of ``must_include`` present in the answer. ``None`` when the case lists none.

    Abstention-safe by the same rule as ``eval/metrics.py``: a case with no expectation has no
    coverage, and calling that ``0.0`` would drag a bucket mean for a fact about the dataset.
    """
    if not must_include:
        return None
    return len(_present(must_include, answer_text)) / len(must_include)


def missing_must_include(must_include: Sequence[str], answer_text: str) -> tuple[str, ...]:
    """The ``must_include`` strings the answer does not contain — the readable half of the above."""
    present = set(_present(must_include, answer_text))
    return tuple(label for label in must_include if label not in present)


def violated_must_not_include(must_not_include: Sequence[str], answer_text: str) -> tuple[str, ...]:
    """The forbidden strings the answer *does* contain. A non-empty result is a violation.

    Returned as a list of strings, and the case ids are reported by :func:`run`, because "1 of 18
    normal cases violated something" hides exactly the case a reader would need to look at.
    """
    return _present(must_not_include, answer_text)


def answer_cites_nothing(abstained: bool, citations: Sequence[str]) -> bool:
    """True for a non-abstaining answer with no citations: the "looks grounded, is not" detector.

    An abstention citing nothing is correct behaviour, so this is only ever true of an answer that
    claimed to answer and named no source.
    """
    return not abstained and not citations


def cited_but_not_retrieved(
    citations: Sequence[str], retrieved_ids: Sequence[str]
) -> tuple[str, ...]:
    """Cited ids retrieval never offered.

    Always empty today: ``lumen.generation.answer`` extracts citations from the retrieved list only,
    so it cannot invent one. Computed and reported anyway — it is the tripwire for the day citation
    extraction gets smarter than that.
    """
    offered = set(retrieved_ids)
    return tuple(chunk_id for chunk_id in citations if chunk_id not in offered)


def abstention_matches(abstained: bool, should_abstain: bool) -> bool:
    """Whether the answerer's behaviour matched the human label for this case."""
    return abstained == should_abstain


def _fraction(flag: bool | None) -> float | None:
    """A bool is a 1.0/0.0 measurement; ``None`` stays ``None``. A missing verdict is not a "no"."""
    return None if flag is None else float(flag)


def _mean_over(case_type: str, values: Sequence[float | None]) -> BucketMean:
    """Delegate the arithmetic to ``mean_excluding_none`` and keep the counts beside the mean."""
    value, n_included, n_excluded = mean_excluding_none(values)
    return BucketMean(case_type, value, n_included, n_excluded)


def _stats(case_type: str, cases: Sequence[CaseOutcome]) -> BucketStats:
    """Aggregate one bucket. Every mean names its denominator; nothing here builds a blended row."""
    measured = [case for case in cases if case.measured]
    judged = [case for case in cases if case.judged]
    hollow = tuple(case.id for case in cases if case.cites_nothing)
    invented = tuple(case.id for case in cases if case.cited_but_not_retrieved)
    return BucketStats(
        case_type=case_type,
        n_cases=len(cases),
        must_include_coverage=_mean_over(case_type, [case.must_include_coverage for case in cases]),
        abstention_match=_mean_over(
            case_type, [_fraction(case.abstention_match) for case in cases]
        ),
        context_offered=_mean_over(
            case_type, [float(len(case.retrieved_ids)) for case in measured]
        ),
        context_cited=_mean_over(case_type, [float(len(case.citations)) for case in measured]),
        n_cites_nothing=len(hollow),
        cites_nothing_ids=hollow,
        n_cited_but_not_retrieved=len(invented),
        cited_but_not_retrieved_ids=invented,
        n_answer_errors=len(cases) - len(measured),
        answer_error_ids=tuple(case.id for case in cases if not case.measured),
        n_judged=len(judged),
        n_judge_errors=len(cases) - len(judged),
        judge_error_ids=tuple(case.id for case in cases if not case.judged),
        n_supported=sum(1 for case in judged if case.judge.supported is True),
        n_unsupported=sum(1 for case in judged if case.judge.supported is False),
        n_should_have_refused=sum(1 for case in judged if case.judge.should_have_refused is True),
        n_answer_should_stand=sum(1 for case in judged if case.judge.should_have_refused is False),
    )


# ------------------------------------------------- the judge: one POST, strict JSON in, a parsed
# verdict or a counted error out. It never sees a golden label.

JUDGE_TEMPLATE = """\
You are grading one answer to one question. You are not given the correct answer, and you have no
knowledge of this domain outside the context below.

Question: {question}

Context:
{context}

Candidate answer:
{answer}

Decide, using ONLY the context above:
- "supported": does the context support every claim the candidate answer makes? An answer that
  refuses is supported when the context does not answer the question, and unsupported when the
  context plainly does.
- "should_have_refused": should a careful answerer have refused to answer this question from the
  context above, rather than answering it?
- "reason": one or two sentences naming the decisive evidence.

Reply with strict JSON only, no prose and no code fences, exactly these keys and no others:
{{"supported": true, "should_have_refused": false, "reason": "..."}}
"""


def build_judge_prompt(question: str, retrieved: Sequence[Chunk], answer_text: str) -> str:
    """The judge's whole world: question, retrieved context (id + text), candidate answer.

    The JSON example in the template is escaped as ``{{...}}`` because ``str.format`` would
    otherwise read ``{"supported": ...}`` as a replacement field and raise ``KeyError:
    '"supported"'`` — which it did, on the first real call, before any test existed. Values
    substituted INTO the template are
    never re-interpolated, so an answer containing braces is safe.

    No golden field is ever interpolated here. If the judge could read the labels, judge and golden
    would stop being two measurements and the calibration section would be a model compared with
    itself.
    """
    if retrieved:
        context = "\n\n".join(f"[{chunk.id}]\n{chunk.text}" for chunk in retrieved)
    else:
        context = "(nothing retrieved)"
    return JUDGE_TEMPLATE.format(question=question, context=context, answer=answer_text)


def parse_judge_reply(raw: str) -> tuple[bool, bool, str]:
    """Strict-JSON parse of a judge reply: fences stripped, first balanced object, keys verified.

    Defensive about the *shape* only. A reply with any other key set raises
    :class:`JudgeResponseError`, and the caller counts that as a judge error, so a chatty or
    truncated judge costs a denominator, never a run.
    """
    candidate = _first_json_object(_FENCE_LINE.sub("", raw))
    try:
        record: object = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise JudgeResponseError(f"judge verdict is not JSON ({error.msg})") from error
    if not isinstance(record, dict):
        raise JudgeResponseError("judge verdict is not a JSON object")
    if set(record) != JUDGE_KEYS:
        raise JudgeResponseError(
            f"judge verdict keys must be exactly {sorted(JUDGE_KEYS)}, got {sorted(record)}"
        )
    supported, should_have_refused = record["supported"], record["should_have_refused"]
    if not isinstance(supported, bool) or not isinstance(should_have_refused, bool):
        raise JudgeResponseError("judge verdict booleans must be JSON true or false")
    reason = record["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise JudgeResponseError("judge verdict reason must be a non-empty string")
    return supported, should_have_refused, " ".join(reason.split())


def _first_json_object(text: str) -> str:
    """The first balanced ``{...}`` substring, string- and escape-aware.

    Balance is tracked by hand rather than by counting braces because a reason like
    ``"the {policy} says so"`` holds braces inside a JSON string that must not close the object. A
    truncated reply has no balanced object, so it fails here instead of letting ``json.loads`` quote
    the reply back in its error message.
    """
    start = text.find("{")
    if start < 0:
        raise JudgeResponseError("judge reply contains no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise JudgeResponseError("judge reply has an unbalanced JSON object")


def _judge_client() -> httpx.Client:
    """Transport seam: tests patch this to hand back a client on an ``httpx.MockTransport``."""
    return httpx.Client(timeout=generation.REQUEST_TIMEOUT_SECONDS)


def _reply_text(response: httpx.Response) -> str:
    """``choices[0].message.content`` of an OpenAI-shaped body, described by shape only.

    Never echoes a body fragment into an error message: an upstream error page can quote the request
    it rejected, and that request contains the retrieved context.
    """
    try:
        payload: object = response.json()
    except ValueError as error:
        raise JudgeResponseError("judge body is not JSON") from error
    if not isinstance(payload, dict):
        raise JudgeResponseError("judge body is not a JSON object")
    choices: object = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise JudgeResponseError("judge body has no completion choices")
    choice: object = choices[0]
    if not isinstance(choice, dict):
        raise JudgeResponseError("judge completion is not a JSON object")
    message: object = choice.get("message")
    if not isinstance(message, dict):
        raise JudgeResponseError("judge completion has no message")
    content: object = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise JudgeResponseError("judge message has no text")
    return content


def judge_model_for(settings: Settings) -> str:
    """Which model grades: ``LLM_JUDGE_MODEL`` when set, otherwise the answering model itself.

    The default is deliberately the same model, because that is the configuration most teams run by
    accident, and the report names self-preference bias when it is present. Setting a second model
    is the one lever that turns that confession from a description into an experiment.
    """
    return settings.llm_judge_model or settings.llm_model


def ask_judge(
    question: str, retrieved: Sequence[Chunk], answer_text: str, settings: Settings
) -> JudgeVerdict:
    """One judge call. Every failure comes back as ``status="error"``; nothing here raises.

    A run with judge errors is a measurement with a visible wound: the case is counted, named in the
    report, excluded from the judge denominators, and the run finishes. No ``max_tokens`` is sent:
    the endpoint's default is adequate, and capping it truncates a reasoning model into an empty
    ``content`` — which would then be miscounted as a parse failure.
    """
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload: dict[str, object] = {
        "model": judge_model_for(settings),
        "temperature": 0,
        "messages": [
            {"role": "user", "content": build_judge_prompt(question, retrieved, answer_text)}
        ],
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    try:
        with _judge_client() as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.RequestError:
        return JudgeVerdict(STATUS_ERROR, None, None, JUDGE_TRANSPORT_FAILURE)
    if response.status_code >= 400:
        return JudgeVerdict(
            STATUS_ERROR, None, None, f"judge provider returned HTTP {response.status_code}"
        )
    try:
        supported, should_have_refused, reason = parse_judge_reply(_reply_text(response))
    except JudgeResponseError as error:
        return JudgeVerdict(STATUS_ERROR, None, None, str(error))
    return JudgeVerdict(STATUS_OK, supported, should_have_refused, reason)


# ------------------------------------------------------------------- the run: retrieve, answer,
# measure, judge. No thresholds anywhere in this section.


def run(
    golden_path: Path,
    settings: Settings,
    k_override: int | None = None,
    limit: int | None = None,
    report_path: str = DEFAULT_REPORT,
) -> EvalRun:
    """Read and validate the golden file, then answer, measure and judge every case in it.

    ``k_override`` replaces each case's own ``k`` uniformly, exactly as the retrieval CLI's ``--k``
    does; ``None`` means "use the file's per-case k". ``limit`` keeps the first N cases in file
    order, before sorting, so a smoke run is a prefix of the dataset rather than a sample of it.
    Validation covers the whole file even under a limit, so a truncated run cannot hide a corrupted
    dataset. Raises ``GoldenFileError``, which the caller maps to the harness exit code.
    """
    corpus_docs = {chunk.id: chunk.doc_id for chunk in load_corpus()}
    cases = load_golden(golden_path, corpus_docs)
    n_cases_in_file = len(cases)
    if limit is not None:
        cases = cases[:limit]
    outcomes = tuple(
        sorted(
            (_answer_and_judge(case, k_override, settings) for case in cases),
            key=lambda outcome: outcome.id,
        )
    )
    return EvalRun(
        golden_path=str(golden_path),
        report_path=report_path,
        k_override=k_override,
        file_k_values=tuple(sorted({case.k for case in cases})),
        limit=limit,
        n_cases_in_file=n_cases_in_file,
        model=settings.llm_model,
        judge_model=judge_model_for(settings),
        base_url=settings.llm_base_url,
        answer_prompt_version=PROMPT_VERSION,
        answer_mode=generation.generation_mode(settings),
        cases=outcomes,
        stats=tuple(
            _stats(case_type, [case for case in outcomes if case.case_type == case_type])
            for case_type in CASE_TYPES
        ),
        calibration=calibrate(outcomes),
    )


def _answer_and_judge(case: GoldenCase, k_override: int | None, settings: Settings) -> CaseOutcome:
    """One case: retrieve, ask the answerer, compute the deterministic metrics, then ask the judge.

    A provider failure in the *answerer* is recorded on the case instead of aborting the run: only a
    harness error is a non-zero exit, and a 5xx from a provider is not one. The case then holds no
    answer text, every mean that needs text excludes it, and the report names it by id.
    """
    k = case.k if k_override is None else k_override
    found = retrieve(case.query, k)
    retrieved_ids = tuple(chunk.id for chunk in found.chunks)
    try:
        generated = generation.answer(case.query, found.chunks, found.scores, settings)
    except generation.GenerationUnavailable as error:
        # GenerationUnavailable messages name a status code or a failure shape, never request
        # material, so storing the message cannot leak the key. "" means an answer exists.
        answer_error, text = str(error), ""
    else:
        answer_error, text = "", generated.text
    measured = not answer_error
    return CaseOutcome(
        id=case.id,
        case_type=case.case_type,
        query=case.query,
        k=k,
        should_abstain=case.should_abstain,
        retrieved_ids=retrieved_ids,
        answer_mode=generation.generation_mode(settings),
        answer_error=answer_error,
        text=text,
        abstained=generated.abstained if measured else False,
        citations=tuple(generated.citations) if measured else (),
        must_include_coverage=(
            must_include_coverage(case.must_include, text) if measured else None
        ),
        must_include_missing=(missing_must_include(case.must_include, text) if measured else ()),
        must_not_include_present=(
            violated_must_not_include(case.must_not_include, text) if measured else ()
        ),
        abstention_match=(
            abstention_matches(generated.abstained, case.should_abstain) if measured else None
        ),
        cites_nothing=(
            answer_cites_nothing(generated.abstained, generated.citations) if measured else False
        ),
        cited_but_not_retrieved=(
            cited_but_not_retrieved(generated.citations, retrieved_ids) if measured else ()
        ),
        judge=(
            ask_judge(case.query, found.chunks, text, settings)
            if measured
            else JudgeVerdict(
                STATUS_ERROR, None, None, "the answerer failed, so there is no answer"
            )
        ),
    )


def calibrate(cases: Sequence[CaseOutcome]) -> Calibration:
    """Compare the judge's verdicts with the human labels, and list every disagreement.

    Only two comparisons exist, because only those two are labelled per case: ``should_abstain``,
    and a ``must_not_include`` string the answer actually contains — a prohibition the human called
    and the answer broke. A clean answer to a ``must_not_include`` case is not evidence either way,
    so it joins neither denominator. A case whose judge reply could not be parsed is excluded and
    counted, so an unreadable judge can neither help nor hurt an agreement rate.
    """
    abstentions = [case for case in cases if case.should_abstain]
    violations = [
        case for case in cases if case.case_type == TYPE_NORMAL and case.must_not_include_present
    ]
    agreement_abstention, rows_abstention = _check(
        CHECK_ABSTENTION, abstentions, lambda case: "should_abstain=true"
    )
    agreement_violations, rows_violations = _check(
        # The human label is the string this answer was not allowed to contain.
        CHECK_MUST_NOT_INCLUDE,
        violations,
        lambda case: "violates must_not_include: " + ", ".join(case.must_not_include_present),
    )
    return Calibration(
        agreements=(agreement_abstention, agreement_violations),
        disagreements=tuple(
            sorted([*rows_abstention, *rows_violations], key=lambda row: (row.id, row.check))
        ),
        n_should_abstain_labelled=len(abstentions),
        n_normal_with_violated_must_not_include=len(violations),
    )


def _check(
    name: str, cases: Sequence[CaseOutcome], human_label: Callable[[CaseOutcome], str]
) -> tuple[Agreement, tuple[Disagreement, ...]]:
    """One human-vs-judge comparison: its agreement rate, and the cases that disagreed.

    ``n_excluded`` is how many labelled cases had no usable answer or no readable judge verdict.
    They sit outside both the numerator and the denominator, and are never counted as a
    disagreement — an unreadable judge is a hole in the measurement, not a verdict.
    """
    comparable = [case for case in cases if case.measured and case.judged]
    agreed = [case for case in comparable if _agrees(name, case)]
    agreement = Agreement(
        name=name,
        value=None if not comparable else len(agreed) / len(comparable),
        n_agreed=len(agreed),
        n_compared=len(comparable),
        n_excluded=len(cases) - len(comparable),
    )
    disagreements = tuple(
        Disagreement(
            id=case.id,
            case_type=case.case_type,
            check=name,
            human_label=human_label(case),
            judge_verdict=(
                f"should_have_refused={case.judge.should_have_refused}"
                if name == CHECK_ABSTENTION
                else f"supported={case.judge.supported}"
            ),
            reason=case.judge.reason,
        )
        for case in sorted(comparable, key=lambda case: case.id)
        if not _agrees(name, case)
    )
    return agreement, disagreements


def _agrees(name: str, case: CaseOutcome) -> bool:
    """The single definition of "judge and human label point the same way", for both checks."""
    if name == CHECK_ABSTENTION:
        return case.judge.should_have_refused is True
    return case.judge.supported is False


# -------------------------------------------------------------------------- rendering. The order of
# the document is fixed, and every figure in it was computed by run(). Formatting only.


def render_markdown(result: EvalRun) -> str:
    """The whole report as one string. Nothing here measures anything."""
    return "\n".join(
        [
            *_header(result),
            *_deterministic(result),
            *_judge(result),
            *_calibration(result),
            *_cases(result),
        ]
    )


def write_report(path: Path, result: EvalRun) -> None:
    """Write ``render_markdown`` to ``path``, byte-identical to what the CLI prints."""
    path.write_text(f"{render_markdown(result)}\n", encoding="utf-8")


def _header(result: EvalRun) -> list[str]:
    composition = ", ".join(f"{stats.n_cases} `{stats.case_type}`" for stats in result.stats)
    total = sum(stats.n_cases for stats in result.stats)
    command = f"python -m eval.generation_eval --golden {result.golden_path}"
    if result.k_override is not None:
        command += f" --k {result.k_override}"
    if result.limit is not None:
        command += f" --limit {result.limit}"
    command += f" --report {result.report_path}"
    file_k = ", ".join(str(value) for value in result.file_k_values)
    scoring_k = (
        f"each case's own `k` from the golden file (values present: {file_k})"
        if result.k_override is None
        else f"{result.k_override}, applied uniformly to every case (file's own: {file_k})"
    )
    limit_note = (
        []
        if result.limit is None
        else [
            f"- Limited to the first {result.limit} of {result.n_cases_in_file} cases "
            "in file order "
            "(`--limit`): a smoke run, so every denominator below names the reduced count."
        ]
    )
    return [
        "# Lumen generation eval",
        "",
        f"- Model that answered: `{result.model}` — mode `{result.answer_mode}`, "
        f"`lumen.generation.answer()`, prompt_version `{result.answer_prompt_version}`.",
        f"- Model that judged: `{result.judge_model}` — "
        f"`eval.generation_eval.ask_judge`, JUDGE_PROMPT_VERSION `{JUDGE_PROMPT_VERSION}`.",
        *(
            [
                f"- **The same model answered and judged** (`{result.model}`, one endpoint). Every "
                "judge number below is a model grading its own output, so self-preference bias is "
                "a live configuration of this harness and not a footnote: read `supported` as that "
                "model's opinion of its own answer, never as correctness. Set `LLM_JUDGE_MODEL` to "
                "break the tie."
            ]
            if result.judge_model == result.model
            else [
                f"- Answerer and judge are different models (`{result.model}` answered, "
                "`{result.judge_model}` judged), so self-preference bias is not the confound here. "
                "That does not make the judge right: the golden set is 30 hand-labelled cases, so "
                "agreement is still an agreement between two fallible readers."
            ]
        ),
        "- **The judge never sees a golden label.** Its prompt carries the question, the retrieved "
        "context and the candidate answer, and nothing else. `eval/golden.jsonl` is human-authored "
        "and is the only independent signal here, so judge and golden stay two measurements, put "
        "side by side only in the calibration section.",
        "- **Nothing in this file is a gate.** No threshold, no `--min-*` flag, no PASS/FAIL "
        "and no "
        "exit code for a bad number: Stage 11 ships the measurement, and a threshold picked before "
        "this distribution was seen is the anti-pattern `AGENT.md` records as I5.",
        "- No `LLM_API_KEY` value appears in this file, in stdout, or in any error message.",
        f"- Golden file: `{result.golden_path}` — {total} cases ({composition}).",
        f"- Scoring k: {scoring_k}.",
        f"- Command: `{command}`",
        *limit_note,
        "",
    ]


def _deterministic(result: EvalRun) -> list[str]:
    """Per-bucket deterministic metrics, and the two detectors that need a paragraph, not a cell."""

    def cells(stats: BucketStats) -> list[str]:
        return [
            stats.case_type,
            str(stats.n_cases),
            _mean_cell(stats.must_include_coverage),
            _mean_cell(stats.abstention_match),
            _mean_cell(stats.context_offered),
            _mean_cell(stats.context_cited),
        ]

    hollow = " · ".join(
        f"{stats.case_type} {_counts(stats.n_cites_nothing, stats.cites_nothing_ids)}"
        for stats in result.stats
    )
    invented = " · ".join(
        f"{stats.case_type} "
        f"{_counts(stats.n_cited_but_not_retrieved, stats.cited_but_not_retrieved_ids)}"
        for stats in result.stats
    )
    excluded = "; ".join(
        f"`{stats.case_type}`: answer errors "
        f"{_counts(stats.n_answer_errors, stats.answer_error_ids)}, judge errors "
        f"{_counts(stats.n_judge_errors, stats.judge_error_ids)}"
        for stats in result.stats
    )
    return [
        "## Deterministic metrics per case type",
        "",
        "Computed from the answer text and the labels alone, with no judge involved.",
        "",
        *_table(
            [
                "bucket",
                "cases",
                "must_include coverage",
                "abstention match",
                "context offered",
                "context cited",
            ],
            [cells(stats) for stats in result.stats],
        ),
        "",
        "- **`must_include coverage`** is the share of a case's `must_include` strings present in "
        "the answer, matched as case-insensitive substrings. A case that lists none — all six "
        "`abstention` and all six `ambiguous` cases do — contributes `None`, not `0.0`, which is "
        "why the `(included/total)` count travels with every mean.",
        "- **`abstention match`** is `answer.abstained == should_abstain`, per bucket, never "
        "blended: the cases labelled `should_abstain` and the answerable ones ask different "
        "questions, and a mean over both moves when the dataset's composition changes rather than "
        "when the answerer does.",
        '- **`answer_cites_nothing` — the "looks grounded, is not" detector.** A non-abstaining '
        f"answer that names no chunk id. Per bucket: {hollow}.",
        "- **`cited_but_not_retrieved`** — a cited chunk id retrieval never offered. Per bucket: "
        f"{invented}. This is `0` today by construction, because `lumen.generation` extracts "
        "citations from the retrieved list only; it is reported anyway, as the tripwire for "
        "the day "
        "citation extraction gets smarter than that.",
        "- **`context offered` vs `context cited`** — the mean number of chunk ids retrieval "
        "put in "
        "front of the answerer against the mean number the answer actually named; the gap is how "
        "much offered context went unused.",
        f"- Cases outside a mean: {excluded}. A provider failure on one case is a hole in the "
        "measurement, not a crash: the case is named here and leaves every denominator that needs "
        "answer text.",
        "",
    ]


def _judge(result: EvalRun) -> list[str]:
    """How the judge voted, per bucket. Counts, not a score: nothing reads these rows as a gate."""

    def cells(stats: BucketStats) -> list[str]:
        return [
            stats.case_type,
            str(stats.n_cases),
            str(stats.n_judged),
            str(stats.n_judge_errors),
            str(stats.n_supported),
            str(stats.n_unsupported),
            str(stats.n_should_have_refused),
            str(stats.n_answer_should_stand),
        ]

    return [
        "## Judge verdicts per case type",
        "",
        "One `httpx` POST per case, `temperature: 0`, no `max_tokens`, expecting "
        '`{"supported": bool, "should_have_refused": bool, "reason": str}`.',
        "",
        *_table(
            [
                "bucket",
                "cases",
                "judged",
                "judge errors",
                "supported true",
                "supported false",
                "should_have_refused true",
                "should_have_refused false",
            ],
            [cells(stats) for stats in result.stats],
        ),
        "",
        "- `judged + judge errors = cases`. A reply that did not parse is a judge error: the case "
        "is counted, named above, and excluded from every judge denominator. It is never recounted "
        'as `supported: false`, because "the judge could not be read" is not a verdict.',
        "- A case the answerer failed to answer occupies a judge slot too, so the two id lists "
        "above overlap by construction: one upstream failure is one failure, counted once as an "
        "answer error and once as an ungradeable answer. Only the judge errors that do not appear "
        "among the answer errors are faults of the judge itself.",
        "- `supported` and `should_have_refused` are two independent opinions — one about this "
        "answer, one about the question. Neither column is a score.",
        "",
    ]


def _calibration(result: EvalRun) -> list[str]:
    """Judge against the human labels. The disagreement list is the point; the rates label it."""
    calibration = result.calibration
    return [
        "## Calibration: the judge measured against the golden labels",
        "",
        "- **This is not accuracy.** The judge shares a model with the answerer, so an agreement "
        "here can be two systems agreeing with each other rather than with the world, and the "
        "golden set is 30 hand-labelled items, so each denominator below is a number that one "
        "edited case would move. The table describes this run; the disagreements underneath it are "
        "the part worth reading.",
        f"- {calibration.n_should_abstain_labelled} cases are labelled `should_abstain`, which is "
        "the entire denominator of the first check.",
        f"- {calibration.n_normal_with_violated_must_not_include} `normal` cases had an "
        "answer that "
        "contained one of their `must_not_include` strings — the only cases where the human label "
        'says "this answer is wrong" and the judge has something to agree or disagree with. A '
        "clean answer to a `must_not_include` case is not evidence either way and is not counted.",
        "",
        *_table(
            ["check", "agreement", "agreed / compared", "excluded (no usable judge verdict)"],
            [
                [
                    agreement.name,
                    _number(agreement.value),
                    f"{agreement.n_agreed}/{agreement.n_compared}",
                    str(agreement.n_excluded),
                ]
                for agreement in calibration.agreements
            ],
        ),
        "",
        f"### Disagreements ({len(calibration.disagreements)}) — read them, do not average them",
        "",
        *(
            [
                *_table(
                    ["id", "type", "check", "human label", "judge verdict", "judge reason"],
                    [
                        [
                            row.id,
                            row.case_type,
                            row.check,
                            row.human_label,
                            row.judge_verdict,
                            row.reason,
                        ]
                        for row in calibration.disagreements
                    ],
                )
            ]
            if calibration.disagreements
            else [
                "No case disagreed in this run. Over a handful of comparable cases that is a small "
                "sample, not an agreement: it does not make the judge independent of the answerer."
            ]
        ),
        "",
    ]


def _cases(result: EvalRun) -> list[str]:
    def cells(case: CaseOutcome) -> list[str]:
        return [
            case.id,
            case.case_type,
            "n/a" if not case.measured else _yes_no(case.abstained),
            _yes_no(case.should_abstain),
            _number(case.must_include_coverage),
            " ".join(case.citations) or "(none)",
            "n/a" if not case.judged else _yes_no(case.judge.supported),
        ]

    return [
        "## Per-case results",
        "",
        "Sorted by case id, so two runs of the same dataset put the same row in the same place.",
        "",
        *_table(
            [
                "id",
                "type",
                "abstained",
                "should_abstain",
                "must_include coverage",
                "cited ids",
                "judge supported",
            ],
            [cells(case) for case in result.cases],
        ),
        "",
    ]


def _yes_no(value: bool | None) -> str:
    """Booleans render as words: `True`/`False` in a table reads like Python, not like a verdict."""
    return "n/a" if value is None else ("yes" if value else "no")


def _counts(n: int, case_ids: Sequence[str]) -> str:
    """A count with the case ids behind it, because \"2\" is not a finding and \"2 (`n-003`, ...)\"
    is."""
    return f"{n}" + (f" (`{', '.join(case_ids)}`)" if case_ids else "")


def _mean_cell(mean: BucketMean) -> str:
    """`mean (included/total)` — the counts are the point: which cases did this mean cover?"""
    return f"{_number(mean.value)} ({mean.n_included}/{mean.n_included + mean.n_excluded})"


def _number(value: float | None) -> str:
    """Rates print at 4 decimals, the fixed precision of the retrieval report. `None` is `n/a`."""
    return "n/a" if value is None else f"{value:.4f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Markdown table. The helper in `eval/report.py` does this, but it is module-private there on
    purpose; a leading underscore is a boundary, and this document is not that module's client."""

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


# --------------------------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. 0 = skipped (no key) or completed, 1 = harness error. No gate, so no exit
    code for a measurement nobody likes — that decision belongs to whoever has seen a distribution.
    """
    arguments = _build_parser().parse_args(argv)
    settings = config.get_settings()
    if not settings.llm_api_key:
        sys.stdout.write(f"{SKIPPED_LINE}\n")
        return 0
    try:
        result = run(
            arguments.golden,
            settings,
            arguments.k,
            arguments.limit,
            str(arguments.report),
        )
    except GoldenFileError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    try:
        write_report(arguments.report, result)
    except OSError as error:
        sys.stderr.write(f"report file {arguments.report} is unwritable: {error}\n")
        return 1
    sys.stdout.write(f"{render_markdown(result)}\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.generation_eval",
        description="Answer every case in the golden file with lumen.generation, measure the "
        "answers deterministically and with an LLM judge, and write the report. This stage ships "
        "measurement, not gates: no flag here can fail the run. Exit codes: 0 skipped (no "
        "LLM_API_KEY) or completed, 1 harness error (unreadable golden file, unwritable report).",
    )
    parser.add_argument(
        "--golden", type=Path, default=Path(DEFAULT_GOLDEN), help="JSONL golden dataset"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(DEFAULT_REPORT),
        metavar="PATH",
        help="where to write the markdown report (default: %(default)s)",
    )
    parser.add_argument(
        "--k",
        type=_positive_int,
        default=None,
        help="cut-off applied to every case, overriding the per-case k (default: the file's own)",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help="answer only the first N cases in file order, for a smoke run",
    )
    return parser


def _positive_int(raw: str) -> int:
    """``--k`` and ``--limit`` must be positive ints; a bad value becomes argparse exit code 2."""
    if not raw.isdigit() or int(raw) < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {raw!r}")
    return int(raw)


if __name__ == "__main__":
    raise SystemExit(main())
