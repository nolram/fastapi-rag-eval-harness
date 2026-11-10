"""Tests for the Stage 11 generation eval: measurement semantics, error paths, leaks — no socket.

Every HTTP call in this file lands on an ``httpx.MockTransport``. That is possible because both
provider legs have a documented transport seam: the answerer's is ``lumen.generation._client`` and
the judge's is ``eval.generation_eval._judge_client``. Nothing is monkeypatched below those two
functions, so no test here can reach a network, and the ``block_network`` tripwire (the same one
``tests/test_api.py`` uses) proves it for the paths that claim to touch none.

Three disciplines the module claims, and this file is written to keep:

* ``None`` is not ``0.0``. A case with no ``must_include``, no usable answer or no readable judge
  verdict leaves the denominator, and the ``(included/total)`` count beside every mean is what makes
  that visible in the report.
* Buckets are never blended. Section 4 pins per-bucket means whose blended mean is a *prettier*
  number, so a reader can see the exact figure this harness refuses to print.
* The API key is read only to build one header. It is asserted on the wire, and its absence is
  asserted in every string a test can reach.

Every expected number in this file is computed by hand in a comment next to the assertion, in the
arithmetic ``eval/metrics.py`` performs.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import httpx
import pytest

from eval import generation_eval
from eval.generation_eval import (
    CHECK_ABSTENTION,
    CHECK_MUST_NOT_INCLUDE,
    DEFAULT_REPORT,
    JUDGE_TRANSPORT_FAILURE,
    STATUS_ERROR,
    STATUS_OK,
    CaseOutcome,
    JudgeResponseError,
    JudgeVerdict,
    _answer_and_judge,
    answer_cites_nothing,
    ask_judge,
    build_judge_prompt,
    cited_but_not_retrieved,
    judge_model_for,
    main,
    must_include_coverage,
    parse_judge_reply,
    render_markdown,
    run,
    violated_must_not_include,
    write_report,
)
from eval.metrics import mean_excluding_none
from eval.retrieval_eval import (
    TYPE_ABSTENTION,
    TYPE_AMBIGUOUS,
    TYPE_NORMAL,
    GoldenCase,
    load_golden,
)
from lumen import generation
from lumen.chunking import load_corpus
from lumen.config import Settings
from lumen.prompts import REFUSAL_LINE

API_KEY_SENTINEL = "sk-SENTINEL-NEVER-LEAKS"
TEST_MODEL = "test-model-v1"
TEST_BASE_URL = "http://provider.invalid/v1"
#: Every env name the harness reads, cleared together so a developer's shell cannot pick the mode.
SETTING_ENV_NAMES = (
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LUMEN_TOP_K",
    "LUMEN_ABSTAIN_THRESHOLD",
)
#: A judge verdict carrying this marker in ``reason`` comes back unreadable, to test the hole.
UNPARSABLE_MARKER = "MUST-NOT-BE-JUDGED"
#: A case whose answer comes back 502 records an answer error instead of aborting the run.
UNANSWERABLE_MARKER = "MUST-NOT-BE-ANSWERED"
#: The three bucket rows the report is allowed to have, in the order the module fixes.
BUCKETS = (TYPE_NORMAL, TYPE_AMBIGUOUS, TYPE_ABSTENTION)


def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the network tripwire on both entry points: ``httpx.post`` and ``Client.post``."""

    def blocked(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("network call attempted from a test")

    monkeypatch.setattr(httpx, "post", blocked)
    monkeypatch.setattr(httpx.Client, "post", blocked)


def live_settings(**overrides: float | str | None) -> Settings:
    """Live-mode settings with a sentinel key; every field explicit so no ambient value leaks in."""
    values: dict[str, float | str | None] = {
        "llm_api_key": API_KEY_SENTINEL,
        "llm_model": TEST_MODEL,
        "llm_base_url": TEST_BASE_URL,
        "lumen_top_k": 5,
        "lumen_abstain_threshold": None,
    }
    values.update(overrides)
    return Settings(**values)


# The golden labels the scripted cases quote, copied verbatim out of eval/golden.jsonl so the
# hand-computed fractions below can be checked against the dataset a reader can open.
MUST_INCLUDE_OF: Mapping[str, tuple[str, ...]] = {
    "n-001": ("120 requests per minute",),
    "n-002": ("Five failed attempts", "15 minutes"),
    "n-003": ("35 days", "14 days"),
    "n-004": ("25 problems", "field_errors"),
    "n-005": ("at most 3 retries", "connection timeout"),
    "a-001": (),
}
MUST_NOT_INCLUDE_OF: Mapping[str, tuple[str, ...]] = {
    "n-003": ("30 days",),
    "n-005": ("5-second window",),
    "n-011": ("at most 5 attempts",),
    "a-006": (),
}


# --------------------------------------------------------------------------- 1. must_include
# coverage: case-insensitive hits, a real miss, and None for a case that lists nothing.


def test_must_include_coverage_is_case_insensitive_substring_matching() -> None:
    """One source line, matched as a substring: the label's own casing must not matter."""
    labels = MUST_INCLUDE_OF["n-002"]  # ("Five failed attempts", "15 minutes")
    # 2 hits of 2 listed -> 2/2 = 1.0, the answer spelled in lower case throughout.
    answer = "after five failed attempts the key is locked for 15 minutes."
    assert must_include_coverage(labels, answer) == 1.0


def test_must_include_coverage_counts_a_genuine_miss() -> None:
    """A near miss is a miss: ``15 min`` is not ``15 minutes``, and half a list is a half."""
    labels = MUST_INCLUDE_OF["n-002"]
    # 1 hit ("15 minutes") of 2 listed -> 1/2 = 0.5.
    assert must_include_coverage(labels, "the lockout lasts 15 minutes in total") == 0.5
    # 0 hits of 2 listed -> 0/2 = 0.0, which is a measurement, unlike None below.
    assert must_include_coverage(labels, "the lockout lasts 15 min") == 0.0


def test_must_include_coverage_is_none_when_a_case_lists_nothing() -> None:
    """The load-bearing rule: no expectation is not a zero score, as ``eval/metrics.py`` insists."""
    # An abstention case carries no must_include at all, so it has no coverage to average.
    assert must_include_coverage(MUST_INCLUDE_OF["a-001"], "any answer at all") is None
    # Same when the answer itself is empty: still nothing was expected, so still not 0.0.
    assert must_include_coverage((), "") is None


# --------------------------------------------------------------------------- 2. violated
# must_not_include: which forbidden strings the answer actually contains.


def test_violated_must_not_include_returns_only_the_strings_actually_present() -> None:
    """The violation list names the broken prohibition, not the whole prohibition list.

    Matching is case-insensitive, but the returned string is the one listed in the golden file, so a
    report line always spells the label the way the dataset does. ``n-005``'s label is
    ``"5-second window"`` and the answer contains ``"5-SECOND WINDOW"``.
    """
    assert violated_must_not_include(("5-second window",), "use the 5-SECOND WINDOW only") == (
        "5-second window",
    )
    # Two labels, one breach: the other is not reported as a violation.
    assert violated_must_not_include(
        ("5-second window", "at most 3 retries"), "the window is 5-second window wide"
    ) == ("5-second window",)
    # A case that lists nothing can never be violated, and "" for an answer is no violation.
    assert violated_must_not_include((), "") == ()


# --------------------------------------------------------------------------- 3. the two text
# detectors, and the mean helper they feed.


def test_answer_cites_nothing_only_fires_on_an_answer_that_claimed_to_answer() -> None:
    """The "looks grounded, is not" detector: an abstention citing nothing is correct behaviour."""
    assert answer_cites_nothing(abstained=False, citations=[]) is True
    assert answer_cites_nothing(abstained=False, citations=["rate_limits#1"]) is False
    assert answer_cites_nothing(abstained=True, citations=[]) is False


def test_cited_but_not_retrieved_is_the_invented_citation_tripwire() -> None:
    """A cited id retrieval never offered. Empty today by construction, and reported anyway."""
    assert cited_but_not_retrieved(["auth#4"], ["auth#4", "errors#1"]) == ()
    assert cited_but_not_retrieved(["auth#4", "made_up#9"], ["auth#4"]) == ("made_up#9",)


def test_mean_excluding_none_keeps_none_out_of_both_the_mean_and_the_count() -> None:
    """The helper every bucket mean delegates to: (2+1)/2 over two values, not a mean of three."""
    # (2.0 + 1.0) / 2 = 1.5, with the third value excluded rather than counted as 0.0 (which would
    # have given the prettier-looking-but-false 3.0 / 3 = 1.0).
    assert mean_excluding_none([2.0, None, 1.0]) == (1.5, 2, 1)
    # A mean over nothing is undefined: None, 0 included, everything excluded.
    assert mean_excluding_none([None, None]) == (None, 0, 2)


# --------------------------------------------------------------------------- 4. the judge reply:
# every malformed shape raises here, and is counted rather than raised at `ask_judge` level.

VERDICT_KEYS = '"supported": true, "should_have_refused": false'


def verdict_json(supported: bool, should_have_refused: bool, reason: str) -> str:
    """The exact contract the judge prompt asks for, as one JSON object."""
    return json.dumps(
        {
            "supported": supported,
            "should_have_refused": should_have_refused,
            "reason": reason,
        }
    )


def test_parse_judge_reply_accepts_a_clean_json_object() -> None:
    raw = verdict_json(True, False, "the context names 120 requests per minute")
    assert parse_judge_reply(raw) == (True, False, "the context names 120 requests per minute")


def test_parse_judge_reply_strips_a_code_fence_around_the_payload() -> None:
    """A ``` fence is markup around the payload, never part of it."""
    raw = "```json\n" + verdict_json(False, True, "nothing in the context answers this") + "\n```"
    assert parse_judge_reply(raw) == (False, True, "nothing in the context answers this")


def test_parse_judge_reply_takes_the_object_out_of_surrounding_prose() -> None:
    """A chatty judge costs nothing: the object inside the chatter is the verdict."""
    raw = (
        "I considered both readings. Verdict: "
        + verdict_json(True, True, "the context is about a different product")
        + " — I hope that helps."
    )
    assert parse_judge_reply(raw) == (True, True, "the context is about a different product")


def test_parse_judge_reply_keeps_literal_braces_inside_the_reason_string() -> None:
    """Why ``_first_json_object`` balances braces by hand rather than counting them.

    A naive brace counter closes the object at the ``}`` after ``{policy``, and the JSON then looks
    truncated. Two unbalanced braces live inside the string here, so a hand-written scanner that
    knows about JSON strings is the only thing that parses this.
    """
    raw = '{"supported": true, "should_have_refused": false, "reason": "the {policy} says {so}"}'
    assert parse_judge_reply(raw) == (True, False, "the {policy} says {so}")


def test_parse_judge_reply_survives_an_escaped_quote_then_a_brace() -> None:
    """The scanner must not mistake an escaped quote for the end of the string."""
    raw = (
        '{"supported": false, "should_have_refused": true, '
        '"reason": "he said \\"no {way}\\" and left"}'
    )
    assert parse_judge_reply(raw) == (False, True, 'he said "no {way}" and left')


def test_parse_judge_reply_normalises_whitespace_in_the_reason() -> None:
    """One reason string per row of the report, so embedded newlines are folded to spaces."""
    raw = verdict_json(True, False, "first line\n\n   second   line")
    assert parse_judge_reply(raw) == (True, False, "first line second line")


def test_parse_judge_reply_rejects_any_other_key_set() -> None:
    """A missing or an extra key is a parse failure, not a silent false."""
    for _name, raw in {
        "missing should_have_refused": '{"supported": true, "reason": "r"}',
        "missing reason": '{"supported": true, "should_have_refused": false}',
        "one extra key": "{" + VERDICT_KEYS + ', "reason": "r", "confidence": 0.9}',
        "every key misspelled": '{"a": true, "b": false, "c": "r"}',
    }.items():
        with pytest.raises(JudgeResponseError):
            parse_judge_reply(raw)


def test_parse_judge_reply_rejects_a_non_bool_where_a_bool_was_promised() -> None:
    """``null`` and ``"true"`` are not JSON bools; the module keeps them out of the tally."""
    for raw in (
        '{"supported": null, "should_have_refused": false, "reason": "r"}',
        '{"supported": "true", "should_have_refused": false, "reason": "r"}',
        '{"supported": true, "should_have_refused": 1, "reason": "r"}',
        '{"supported": 0, "should_have_refused": false, "reason": "r"}',
    ):
        with pytest.raises(JudgeResponseError):
            parse_judge_reply(raw)


def test_parse_judge_reply_rejects_an_empty_or_non_string_reason() -> None:
    """A verdict with no stated reason is not evidence a reader could act on."""
    for raw in (
        verdict_json(True, False, ""),
        verdict_json(True, False, "   "),
        '{"supported": true, "should_have_refused": false, "reason": null}',
        '{"supported": true, "should_have_refused": false, "reason": {"text": "r"}}',
    ):
        with pytest.raises(JudgeResponseError):
            parse_judge_reply(raw)


def test_parse_judge_reply_rejects_a_truncated_reply() -> None:
    """No balanced object means no verdict — and the message must not quote the reply back."""
    truncated = '{"supported": true, "should_have_refused": false, "reason": "because the cont'
    with pytest.raises(JudgeResponseError) as raised:
        parse_judge_reply(truncated)
    assert str(raised.value) == "judge reply has an unbalanced JSON object"


def test_parse_judge_reply_rejects_a_reply_with_no_object_at_all() -> None:
    with pytest.raises(JudgeResponseError) as raised:
        parse_judge_reply("The answer looks well supported to me.")
    assert str(raised.value) == "judge reply contains no JSON object"


def test_first_json_object_returns_the_first_balanced_object_not_the_whole_reply() -> None:
    """The documented contract of the scanner: prose around the object is not part of it.

    A JSON *array* wrapped around a verdict therefore yields its first balanced object too — the
    lenience is the algorithm's, not a bug in a key check: the key set is verified afterwards.
    """
    assert generation_eval._first_json_object('[{"a": 1}, {"b": 2}]') == '{"a": 1}'
    assert generation_eval._first_json_object('x {"a": "}"} y') == '{"a": "}"}'
    for raw, message in {
        "": "judge reply contains no JSON object",
        "no braces here": "judge reply contains no JSON object",
        "{": "judge reply has an unbalanced JSON object",
    }.items():
        with pytest.raises(JudgeResponseError) as raised:
            generation_eval._first_json_object(raw)
        assert str(raised.value) == message


def test_build_judge_prompt_carries_question_context_and_answer_and_nothing_else() -> None:
    """The judge's whole world, and no golden field in it.

    A case's label text (``notes`` here, the only free-text label in the dataset) and its
    ``should_abstain`` flag are the two things the calibration section depends on staying private.
    """
    chunks = load_corpus()[:2]
    prompt = build_judge_prompt("What is the rate limit?", chunks, "The limit is 120/min.")
    assert "What is the rate limit?" in prompt
    assert f"[{chunks[0].id}]" in prompt
    assert chunks[0].text in prompt
    assert "The limit is 120/min." in prompt
    assert "single obvious fact" not in prompt  # n-001's notes in eval/golden.jsonl
    assert "should_abstain" not in prompt


def test_build_judge_prompt_is_safe_when_the_answer_itself_contains_braces() -> None:
    """The JSON example in the template is escaped for ``str.format``; substituted values never are.

    An answer holding ``{"supported": false}`` must not be re-interpolated: ``str.format`` would
    read it as a replacement field and raise. This is the line that broke before tests existed.
    """
    chunks = load_corpus()[:1]
    prompt = build_judge_prompt("q", chunks, 'the answer said {"supported": false} about {x}')
    assert '{"supported": false}' in prompt
    assert "{x}" in prompt
    # The example the judge is asked to imitate survives the escaping intact.
    assert '{"supported": true, "should_have_refused": false, "reason": "..."}' in prompt


# --------------------------------------------------------------------------- 5. the judge call:
# every failure shape comes back as a counted error, and nothing here raises.


@dataclass(frozen=True, slots=True)
class Call:
    """One outbound provider request, as the harness actually put it on the wire."""

    url: str
    headers: dict[str, str]
    body: dict[str, object]
    prompt: str

    @property
    def question(self) -> str:
        """The question line both prompt templates carry on a line of its own."""
        start = self.prompt.index("\nQuestion: ") + len("\nQuestion: ")
        end = self.prompt.index("\n", start)
        return self.prompt[start:end]


class Provider:
    """A scripted provider on ``httpx.MockTransport``: records every call, answers by case id.

    ``answers`` maps a case id to the text the answerer should return, or to a whole
    ``httpx.Response`` to send back instead (a 502, a non-JSON body, whatever the test needs).
    Unlisted cases get the plain answer or a clean verdict, so a script names only what it changes.
    """

    def __init__(self, answers: Mapping[str, object] | None = None) -> None:
        self.answers = dict(answers or {})
        self.calls: list[Call] = []

    def install_on_answerer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Point ``lumen.generation._client`` at this transport, so no socket is ever opened."""
        transport = httpx.MockTransport(self._handler)
        monkeypatch.setattr(generation, "_client", lambda: httpx.Client(transport=transport))

    def install_on_judge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Point ``eval.generation_eval._judge_client`` at this transport, for the judge leg."""
        transport = httpx.MockTransport(self._handler)
        monkeypatch.setattr(
            generation_eval, "_judge_client", lambda: httpx.Client(transport=transport)
        )

    @staticmethod
    def completion(content: object) -> httpx.Response:
        """One OpenAI-shaped completion carrying ``content`` verbatim, including a JSON ``null``."""
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    def answer_reply(self, case_id: str) -> httpx.Response:
        """The answerer leg: a scripted ``Response`` verbatim, scripted text as a completion,
        or the plain answer that satisfies no label. A whole ``Response`` passes through so a
        test can put a 502 in front of the answerer, which is what an answer error is."""
        scripted = self.answers.get(case_id, PLAIN_ANSWER)
        if isinstance(scripted, httpx.Response):
            return scripted
        return self.completion(scripted)

    def judge_reply(self, case_id: str) -> httpx.Response:
        """The judge leg: a refusal-flavoured verdict where the script asks for one, else a
        clean one."""
        if case_id == UNPARSABLE_MARKER:
            return self.completion("I could not read it.")
        if case_id == UNANSWERABLE_MARKER:
            return self.completion(None)
        return self.completion(
            verdict_json(
                case_id not in JUDGE_SAYS_UNSUPPORTED,
                case_id in JUDGE_SAYS_REFUSE,
                f"the context settles {case_id}",
            )
        )

    def _handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body["messages"][0]["content"])
        call = Call(
            url=str(request.url),
            headers=dict(request.headers),
            body=body,
            prompt=prompt,
        )
        self.calls.append(call)
        case_id = CASE_ID_BY_QUERY.get(call.question, "")
        if "Decide, using ONLY the context above" in prompt:
            return self.judge_reply(case_id)
        return self.answer_reply(case_id)


def judge_over(
    monkeypatch: pytest.MonkeyPatch, reply: Callable[[httpx.Request], httpx.Response]
) -> JudgeVerdict:
    """One judge call against a handler written by hand: the error-path tests are all this shape."""
    transport = httpx.MockTransport(reply)
    monkeypatch.setattr(generation_eval, "_judge_client", lambda: httpx.Client(transport=transport))
    return ask_judge("What is the rate limit?", load_corpus()[:2], "120/min.", live_settings())


def test_ask_judge_posts_the_documented_request_and_carries_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the key is used for, asserted on the wire rather than asserted about."""
    provider = Provider()
    provider.install_on_judge(monkeypatch)

    verdict = ask_judge(
        "What is the rate limit?", load_corpus()[:2], "120 requests per minute.", live_settings()
    )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call.url == f"{TEST_BASE_URL}/chat/completions"
    assert call.headers["authorization"] == f"Bearer {API_KEY_SENTINEL}"
    assert call.body["model"] == TEST_MODEL
    assert call.body["temperature"] == 0
    # No max_tokens on purpose: capping it truncates a reasoning model into an empty content, which
    # would then be miscounted as a parse failure rather than as a provider shape.
    assert "max_tokens" not in call.body
    assert verdict.status == STATUS_OK
    assert (verdict.supported, verdict.should_have_refused) == (True, False)


def test_ask_judge_transport_error_is_counted_and_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    verdict = judge_over(monkeypatch, refuse)
    assert verdict.status == STATUS_ERROR
    assert (verdict.supported, verdict.should_have_refused, verdict.reason) == (
        None,
        None,
        JUDGE_TRANSPORT_FAILURE,
    )
    assert API_KEY_SENTINEL not in str(verdict)


@pytest.mark.parametrize("status_code", [400, 429, 500, 503])
def test_ask_judge_reports_a_bad_status_by_number_only(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    """A bad status is reported by number: an error page can quote the request it rejected."""
    verdict = judge_over(
        monkeypatch,
        lambda request: httpx.Response(status_code, text=f"<html>{API_KEY_SENTINEL}</html>"),
    )
    assert verdict.status == STATUS_ERROR
    assert verdict.reason == f"judge provider returned HTTP {status_code}"
    assert API_KEY_SENTINEL not in verdict.reason


def test_ask_judge_rejects_a_body_that_is_not_json_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = judge_over(
        monkeypatch, lambda request: httpx.Response(200, text="data: not json at all")
    )
    assert verdict.status == STATUS_ERROR
    assert verdict.reason == "judge body is not JSON"


def test_ask_judge_treats_a_null_content_as_an_error_not_a_false_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a truncated reasoning model returns. ``content: null`` is not ``supported: false``."""
    for body in (
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": []},
        {"choices": ["not an object"]},
        {"choices": [{"message": {}}]},
        [],
    ):
        verdict = judge_over(monkeypatch, lambda request, body=body: httpx.Response(200, json=body))
        assert verdict.status == STATUS_ERROR, body
        assert (verdict.supported, verdict.should_have_refused) == (None, None)


def test_ask_judge_counts_an_unreadable_verdict_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad verdict costs a denominator, never a run."""
    verdict = judge_over(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "I think it is fine."}}]}
        ),
    )
    assert verdict.status == STATUS_ERROR
    assert verdict.reason == "judge reply contains no JSON object"


# --------------------------------------------------------------------------- 6. a whole run,
# scripted case by case. One fixture, one table, every number in it worked out by hand.

#: The golden file this suite measures against, found without depending on the current directory.
GOLDEN_FILE = Path(__file__).resolve().parent.parent / "eval" / "golden.jsonl"

PLAIN_ANSWER = "The documentation states the policy for this question."
#: The verdicts the judge returns for the abstention cases that should earn agreement.
JUDGE_SAYS_REFUSE = frozenset({"a-001", "a-002", "a-003", "a-004"})
#: The one case the judge calls unsupported, which is where it agrees with a human violation.
JUDGE_SAYS_UNSUPPORTED = frozenset({"n-003"})

# The script below is the whole measurement surface of a run, written so that every figure is
# computable from the labels in eval/golden.jsonl plus this table. "coverage" is hits/labelled,
# case-insensitively; "abst." is what the answerer did, against `should_abstain` in the dataset.
#
#   case      must_include (dataset)             must_not_include    answer contains
#   n-001     ("120 requests per minute",)       ()                  "120 REQUESTS PER MINUTE"
#   n-002     ("Five failed attempts","15 min…") ()                  "15 minutes" only
#   n-003     ("35 days","14 days")              ("30 days",)        "30 days": a violation
#   n-004     ("25 problems","field_errors")     ()                  both, in mixed case
#   n-005     ("at most 3 retries","connect…")   ("5-second window",)"5-SECOND WINDOW": a breach
#   n-006..18 two strings each                   -                   PLAIN_ANSWER: neither
#   m-001..6  ()                                 ()                  PLAIN_ANSWER, so it answers
#   a-001..5  ()                                 ()                  the refusal line
#   a-006     ()                                 ()                  PLAIN_ANSWER: no refusal
#
#   case      coverage  abst.  should_abstain  match  judge supported  judge should_have_refused
#   n-001     1/1=1.0   no     no              yes    true             false
#   n-002     1/2=0.5   no     no              yes    true             false
#   n-003     0/2=0.0   no     no              yes    FALSE            false
#   n-004     2/2=1.0   no     no              yes    true             false
#   n-005     0/2=0.0   no     no              yes    true             false
#   n-006..18 0.0       no     no              yes    true             false
#   m-*       None      no     no              yes    true             false
#   a-001..4  None      yes    yes             yes    true             TRUE  (judge agrees)
#   a-005     None      yes    yes             yes    true             false (judge disagrees)
#   a-006     None      no     yes             NO     true             false (judge disagrees)
#
# Hand-computed consequences, asserted one per test below:
#   normal coverage mean   (1.0 + 0.5 + 0.0 + 1.0 + 0.0 + 13×0.0) / 18       = 2.5/18  = 0.1389
#   ambiguous, abstention  every case lists no must_include -> mean None, 0 included, 6 excluded
#   abstention match       normal 18/18 = 1.0000   ambiguous 6/6 = 1.0000   abstention 5/6 = 0.8333
#   answer cites nothing   normal 18 · ambiguous 6 · abstention 1 (a-006 answered and named no id)
#   calibration, refusal   agreed 4/6 = 0.6667  disagreements a-005, a-006
#   calibration, breach    agreed 1/2 = 0.5000  disagreement n-005 (the human label says wrong)
ANSWER_SCRIPT: Mapping[str, str] = {
    "n-001": "The rate limit is 120 REQUESTS PER MINUTE for this caller.",
    "n-002": "After a lockout the key stays unusable for 15 minutes.",
    "n-003": "Backups cover 30 days, which is also the rollback window.",
    "n-004": "Up to 25 Problems are reported, listed under field_errors.",
    "n-005": "Reuse the key inside the 5-SECOND WINDOW and move on.",
}

# Loaded at import so the provider handler can map a prompt back to a case id without a fixture.
GOLDEN_CASES: list[GoldenCase] = load_golden(
    GOLDEN_FILE, {chunk.id: chunk.doc_id for chunk in load_corpus()}
)
CASE_ID_BY_QUERY: Mapping[str, str] = {case.query: case.id for case in GOLDEN_CASES}
CASE_BY_ID: Mapping[str, GoldenCase] = {case.id: case for case in GOLDEN_CASES}


def golden_refusing() -> dict[str, str]:
    """The abstention cases that behave: they say the refusal line the prompt asks for."""
    return {case_id: REFUSAL_LINE for case_id in ("a-001", "a-002", "a-003", "a-004", "a-005")}


def scripted_provider() -> Provider:
    """The answerer leg of the canonical script: refusals where the label demands one."""
    return Provider({**golden_refusing(), **ANSWER_SCRIPT})


@pytest.fixture()
def golden_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The real golden file copied into a temp directory that becomes the working directory.

    The copy is what lets the report be asked for a relative path: a report written under
    ``/tmp/...`` that never mentions ``/tmp/...`` is the evidence the determinism test wants.
    """
    destination = tmp_path / GOLDEN_FILE.name
    destination.write_bytes(GOLDEN_FILE.read_bytes())
    monkeypatch.chdir(tmp_path)
    return destination


@pytest.fixture()
def full_run(
    monkeypatch: pytest.MonkeyPatch, golden_copy: Path
) -> tuple[generation_eval.EvalRun, Provider, Provider]:
    """A complete 30-case run over the real golden file, both provider legs scripted."""
    answers, judges = scripted_provider(), Provider()
    answers.install_on_answerer(monkeypatch)
    judges.install_on_judge(monkeypatch)
    result = run(golden_copy, live_settings(), report_path="generation_report.md")
    return result, answers, calls_after_run(judges)


def calls_after_run(judges: Provider) -> Provider:
    """Sort the judge records by case id, because ``run`` iterates the golden file's own order."""
    order = {case.id: position for position, case in enumerate(GOLDEN_CASES)}
    judges.calls.sort(key=lambda call: order[CASE_ID_BY_QUERY[call.question]])
    return judges


def bucket(run_: generation_eval.EvalRun, case_type: str) -> generation_eval.BucketStats:
    """One bucket row, by name rather than by index, so a reordered report cannot hide a mistake."""
    for stats in run_.stats:
        if stats.case_type == case_type:
            return stats
    raise AssertionError(f"no bucket {case_type!r} in the run")


def outcome(run_: generation_eval.EvalRun, case_id: str) -> CaseOutcome:
    """One case's outcome by id."""
    for case in run_.cases:
        if case.id == case_id:
            return case
    raise AssertionError(f"no case {case_id!r} in the run")


# ------------------------------------------------------------------ 6. skipped mode: the only mode
# a machine without an endpoint ever sees, and the only one that must never open a socket.


def test_main_skips_without_a_key_exits_zero_writes_nothing_and_reaches_no_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CI contract: no key -> a named skip, exit ``0``, no report file, no socket.

    Exit ``0`` rather than ``1`` is a decision and not a default. The absence of an endpoint is not
    a failed measurement, and a red check on a machine that was never given a key trains people to
    ignore red — which is the one thing every other file in this repo is trying to buy back.
    """
    block_network(monkeypatch)
    for name in SETTING_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    assert main([]) == 0

    captured = capsys.readouterr()
    assert "skipped: no LLM_API_KEY" in captured.out
    assert captured.err == ""
    # Nothing on disk: a skip that still wrote a report would leave a stale one looking current.
    assert not (tmp_path / DEFAULT_REPORT).exists()
    assert list(tmp_path.rglob("*.md")) == [], "skipped mode wrote a file it promised not to write"


def test_skipped_mode_clears_every_env_that_could_pick_the_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One cleared variable is not enough: a stray ``LLM_MODEL`` must not half-arm the run.

    The harness chooses a mode from the key alone, so the skip is still a skip with a model and a
    base URL present — and the line printed names the key as the reason, not the empty settings.
    """
    block_network(monkeypatch)
    for name in SETTING_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "a-model-without-a-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://provider.invalid/v1")
    monkeypatch.chdir(tmp_path)

    assert main([]) == 0

    captured = capsys.readouterr()
    assert "skipped: no LLM_API_KEY" in captured.out
    assert "a-model-without-a-key" not in captured.out


class UnreadableJudgeFor(Provider):
    """The canonical judge script, with one named case's reply made impossible to read."""

    def __init__(self, case_id: str) -> None:
        super().__init__()
        self.unreadable_case_id = case_id

    def judge_reply(self, case_id: str) -> httpx.Response:
        if case_id == self.unreadable_case_id:
            return self.completion("The answer looks broadly correct to me, for what it is worth.")
        return super().judge_reply(case_id)


# ------------------------------------------------------------------ 7. per-bucket aggregation, and
# the blended number this harness refuses to compute.


def test_bucket_means_are_never_blended_and_the_blend_is_the_prettier_number(
    full_run: tuple[generation_eval.EvalRun, Provider, Provider],
) -> None:
    """The refused figure is named, because "we do not blend" is only testable as a number.

    From the script table: ``normal`` matches ``should_abstain`` 18/18, ``ambiguous`` 6/6,
    ``abstention`` 5/6 — ``a-006`` was answered when it should have refused.

        one mean over all 30 cases = (18 + 6 + 5) / 30 = 29/30 = 0.9667

    which beats every honest row and describes none of them: it moves when the dataset's
    composition moves, and it is the number a reader would most want to see. It is not computed.
    """
    measured, _, _ = full_run
    normal, ambiguous, abstention = (bucket(measured, t) for t in BUCKETS)

    assert normal.abstention_match.value == 18 / 18
    assert normal.abstention_match.n_included == 18
    assert normal.abstention_match.n_excluded == 0
    assert ambiguous.abstention_match.value == 6 / 6
    assert abstention.abstention_match.value == 5 / 6  # a-006 is the miss

    blended = (18 + 6 + 5) / 30  # 0.9666... the number the report must never print
    per_bucket = {
        "normal": normal.abstention_match.value,
        "ambiguous": ambiguous.abstention_match.value,
        "abstention": abstention.abstention_match.value,
    }
    # The blend is prettier than the only informative row (0.9667 against abstention's 0.8333) and
    # is not any of the three rows either, so it describes no bucket while looking like one.
    assert blended > per_bucket["abstention"]
    assert blended not in per_bucket.values()
    assert blended < per_bucket["normal"]
    assert [s.case_type for s in measured.stats] == list(BUCKETS)
    assert len(measured.stats) == 3, "a fourth row would be a blend wearing a bucket name"


def test_coverage_means_exclude_the_cases_that_list_nothing_from_both_sides(
    full_run: tuple[generation_eval.EvalRun, Provider, Provider],
) -> None:
    """``None`` is not ``0.0``: an unlabelled case leaves the denominator and is counted leaving.

    Script: ``n-001`` 1/1, ``n-002`` 1/2, ``n-003`` 0/2, ``n-004`` 2/2, ``n-005`` 0/2, and
    ``n-006..n-018`` all answered with ``PLAIN_ANSWER``, which satisfies neither of their two
    labels, so 13 x 0/2.

        normal mean = (1.0 + 0.5 + 0.0 + 1.0 + 0.0 + 13 * 0.0) / 18 = 2.5/18 = 0.1389

    All 18 ``normal`` cases carry at least one ``must_include``, so none is excluded. The other two
    buckets list none at all: every one of their six cases is excluded, and the mean is ``None``.
    """
    measured, _, _ = full_run
    normal, ambiguous, abstention = (bucket(measured, t) for t in BUCKETS)

    assert normal.must_include_coverage.n_included == 18
    assert normal.must_include_coverage.n_excluded == 0
    assert normal.must_include_coverage.value == pytest.approx(2.5 / 18, abs=1e-9)

    for stats in (ambiguous, abstention):
        assert stats.must_include_coverage.value is None
        assert stats.must_include_coverage.n_included == 0
        assert stats.must_include_coverage.n_excluded == 6


def test_an_answer_that_cites_nothing_is_counted_only_where_answering_was_right(
    full_run: tuple[generation_eval.EvalRun, Provider, Provider],
) -> None:
    """The looks-grounded-is-not detector, per bucket — an honest refusal is not an offence.

    Script: every ``normal`` and ``ambiguous`` answer is plain prose naming no id, so 18 and 6.
    In ``abstention`` five cases recite the refusal line, which cites nothing and should; only
    ``a-006`` answers without an id, so the bucket count is 1 and it is named.
    """
    measured, _, _ = full_run
    normal, ambiguous, abstention = (bucket(measured, t) for t in BUCKETS)

    assert (normal.n_cites_nothing, ambiguous.n_cites_nothing) == (18, 6)
    assert abstention.n_cites_nothing == 1
    assert abstention.cites_nothing_ids == ("a-006",)


# ------------------------------------------------------------------ 8. calibration: the point of
# this stage. Both directions of disagreement are asserted as id sets, not as a rate.


def test_calibration_agreement_is_computed_over_comparable_cases_only(
    full_run: tuple[generation_eval.EvalRun, Provider, Provider],
) -> None:
    """Two checks, two denominators, and neither denominator is the number of cases.

    Refusal check, over the 6 cases labelled ``should_abstain``: the judge votes ``should_have_
    refused`` for ``a-001..a-004``, so 4 agree and ``a-005``/``a-006`` do not. 4/6 = 0.6667.

    Breach check, over the ``normal`` cases whose answer actually contains one of their
    ``must_not_include`` strings: ``n-003`` ("30 days") and ``n-005`` ("5-SECOND WINDOW"), so the
    denominator is 2 and not 18. The judge calls ``n-003`` unsupported (agrees) and ``n-005``
    supported (disagrees): 1/2 = 0.5000.
    """
    measured, _, _ = full_run
    by_name = {a.name: a for a in measured.calibration.agreements}
    assert set(by_name) == {CHECK_ABSTENTION, CHECK_MUST_NOT_INCLUDE}

    refusal = by_name[CHECK_ABSTENTION]
    assert (refusal.n_agreed, refusal.n_compared, refusal.n_excluded) == (4, 6, 0)
    assert refusal.value == pytest.approx(4 / 6, abs=1e-9)

    breach = by_name[CHECK_MUST_NOT_INCLUDE]
    assert (breach.n_agreed, breach.n_compared, breach.n_excluded) == (1, 2, 0)
    assert breach.value == pytest.approx(1 / 2, abs=1e-9)

    assert measured.calibration.n_should_abstain_labelled == 6
    assert measured.calibration.n_normal_with_violated_must_not_include == 2


def test_both_directions_of_disagreement_are_named_by_id(
    full_run: tuple[generation_eval.EvalRun, Provider, Provider],
) -> None:
    """A rate that hides which case moved is the failure mode of the number next to it.

    ``a-005`` refused and the judge did not call for a refusal; ``a-006`` answered when the human
    label says it should not have, and the judge did not call for a refusal either — that is the
    direction where judge and human are wrong or right together. ``n-005`` broke a stated
    prohibition and the judge still supported the answer.
    """
    measured, _, _ = full_run
    pairs = {(d.id, d.check) for d in measured.calibration.disagreements}
    assert pairs == {
        ("a-005", CHECK_ABSTENTION),
        ("a-006", CHECK_ABSTENTION),
        ("n-005", CHECK_MUST_NOT_INCLUDE),
    }


def test_a_case_with_no_readable_verdict_leaves_both_sides_and_is_never_a_disagreement(
    monkeypatch: pytest.MonkeyPatch, golden_copy: Path
) -> None:
    """The denominator rule, tested where it can actually bite.

    Same script as the canonical run except ``a-005``, whose judge reply cannot be read at all.
    The refusal denominator drops from 6 to 5 and the agreement stays at 4, giving 4/5 = 0.8000 —
    a better number bought by knowing less, which is exactly why ``(agreed/compared)`` travels with
    every figure in the report. ``a-005`` leaves the disagreement list too: an unreadable reply is
    not a vote against the human label, and counting it as one would invent a disagreement out of a
    transport failure.
    """
    answers, judges = scripted_provider(), UnreadableJudgeFor("a-005")
    answers.install_on_answerer(monkeypatch)
    judges.install_on_judge(monkeypatch)
    measured = run(golden_copy, live_settings(), report_path="r.md")

    by_name = {a.name: a for a in measured.calibration.agreements}
    refusal = by_name[CHECK_ABSTENTION]
    assert (refusal.n_agreed, refusal.n_compared, refusal.n_excluded) == (4, 5, 1)
    assert refusal.value == pytest.approx(4 / 5, abs=1e-9)
    assert {d.id for d in measured.calibration.disagreements if d.check == CHECK_ABSTENTION} == {
        "a-006"
    }
    # The hole is counted where it happened, and it is not reported as a verdict.
    abstention = bucket(measured, TYPE_ABSTENTION)
    assert abstention.n_judged == 5
    assert abstention.n_judge_errors == 1
    assert abstention.judge_error_ids == ("a-005",)
    assert abstention.n_supported + abstention.n_unsupported == 5


# ------------------------------------------------------------------ 9. the report as an artefact:
# deterministic, path-free, and honest about what it is.


def test_the_report_renders_byte_identically_and_carries_no_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    golden_copy: Path,
    tmp_path: Path,
) -> None:
    """Same run, same bytes; and the committed artefact records no machine.

    Invoked the way the Makefile invokes it — both paths relative to the working directory — the
    document must contain no absolute path at all, because an artefact that records where it was
    built turns every future diff into a fight with the filesystem. The module echoes the paths it
    is handed, so this is a claim about the artefact we commit, not about path rewriting.
    """
    answers, judges = scripted_provider(), Provider()
    answers.install_on_answerer(monkeypatch)
    judges.install_on_judge(monkeypatch)
    measured = run(Path(golden_copy.name), live_settings(), report_path="generation_report.md")

    first = render_markdown(measured)
    assert first == render_markdown(measured), "two renders of one run differ"

    for needle in ("/home/", "/tmp/", str(tmp_path), str(golden_copy.parent)):
        assert needle not in first, f"{needle} leaked into the report"
    # The key is the one secret the run holds, and it travels through every request header.
    assert API_KEY_SENTINEL not in first

    write_report(Path("written.md"), measured)
    assert Path("written.md").read_text(encoding="utf-8") == f"{first}\n"


def test_the_report_says_in_words_that_none_of_it_is_a_gate(
    full_run: tuple[generation_eval.EvalRun, Provider, Provider],
) -> None:
    """The sentence a reader skimming the tables must not be able to miss."""
    text = render_markdown(full_run[0])
    assert "**Nothing in this file is a gate.**" in text
    assert "No threshold, no `--min-*` flag, no PASS/FAIL" in text
    assert "Stage 11 ships the measurement" in text


def test_the_confession_about_self_judging_is_present_only_when_it_is_true(
    full_run: tuple[generation_eval.EvalRun, Provider, Provider],
    monkeypatch: pytest.MonkeyPatch,
    golden_copy: Path,
) -> None:
    """The stage's own bias, printed or not printed according to the configuration that ran.

    ``full_run`` answers and judges with ``test-model-v1``, so the confession is on the page. With
    ``judge_model`` pointed somewhere else, the same sentence must be absent: a warning that fires
    when it does not apply trains a reader to ignore the one that does.
    """
    text = render_markdown(full_run[0])
    assert "**The same model answered and judged**" in text

    other = live_settings(llm_judge_model="a-different-model")
    answers, judges = scripted_provider(), Provider()
    answers.install_on_answerer(monkeypatch)
    judges.install_on_judge(monkeypatch)
    separate = run(golden_copy, other, limit=1, report_path="r.md")
    assert "**The same model answered and judged**" not in render_markdown(separate)


# ------------------------------------------------------------------ 10. the answer-error path: one
# provider failure is a hole in the measurement, not a crash.


def test_a_case_the_provider_refused_to_answer_is_recorded_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct at ``_answer_and_judge``, where the unbound-variable risk actually sits.

    The module reads ``generated.abstained`` only when the answer leg succeeded. Had it read the
    name unconditionally, this call would raise ``UnboundLocalError`` and the failure would look
    like a harness bug instead of an upstream 502. It must return a case that measured nothing.
    """
    answers = Provider({"n-005": httpx.Response(502, json={"error": "upstream gave up"})})
    answers.install_on_answerer(monkeypatch)
    Provider().install_on_judge(monkeypatch)
    # No block_network here: it patches ``httpx.Client.post``, which is the very method a
    # MockTransport client dispatches through, so installing both would block the mock instead of
    # the internet. The two seams above are what make a socket impossible.

    result = _answer_and_judge(CASE_BY_ID["n-005"], None, live_settings())

    assert result.measured is False
    assert "502" in result.answer_error
    # Every text-dependent measurement is None, not 0.0 and not False: there is nothing to measure.
    assert result.must_include_coverage is None
    assert result.abstention_match is None
    assert result.text == ""
    assert result.abstained is False, "an unanswered case must not be recorded as a clean refusal"
    # The judge slot is consumed too, by a verdict that says so in words rather than by a call:
    # there is no answer to grade. The report must not let a reader count that as a second
    # provider fault, which is why the judge-error bullet names the reason.
    assert result.judge is not None
    assert result.judge.status == STATUS_ERROR
    assert "no answer" in result.judge.reason


def test_an_answer_error_is_counted_in_its_bucket_and_named_in_the_stats(
    monkeypatch: pytest.MonkeyPatch, golden_copy: Path
) -> None:
    """The hole is a named hole: the bucket row counts it and the report lists the id.

    ``limit=5`` runs ``n-001..n-005``. Four answer, ``n-005`` gets a 502, so the ``normal`` row
    covers 5 cases and measures 4: coverage is 1/1 + 1/2 + 0/2 + 2/2 over 4 cases = 2.5/4 = 0.6250,
    and ``n-005`` appears in the answer-error ids, not in any mean.
    """
    answers = Provider({**ANSWER_SCRIPT, "n-005": httpx.Response(502, json={"error": "boom"})})
    answers.install_on_answerer(monkeypatch)
    Provider().install_on_judge(monkeypatch)
    measured = run(golden_copy, live_settings(), limit=5, report_path="r.md")

    normal = bucket(measured, TYPE_NORMAL)
    assert normal.n_cases == 5
    assert normal.n_answer_errors == 1
    assert normal.answer_error_ids == ("n-005",)
    assert normal.must_include_coverage.n_included == 4
    assert normal.must_include_coverage.n_excluded == 1
    assert normal.must_include_coverage.value == pytest.approx(2.5 / 4, abs=1e-9)


def test_the_default_judge_is_the_answering_model_and_a_second_one_breaks_the_tie() -> None:
    """The default is the accidental configuration, and the lever is one environment variable.

    An empty ``llm_judge_model`` means "grade yourself", because that is what teams run by accident,
    and the report says so in those words. Naming a second model is what makes the report's
    confession conditional — and a warning that can never be untrue is not a warning, it is
    decoration.
    """
    assert judge_model_for(live_settings()) == TEST_MODEL
    assert judge_model_for(live_settings(llm_judge_model="other-model")) == "other-model"
