"""Answer generation: one entry point, ``answer()``, whose ``mode`` says how the answer arrived.

Stub mode is the default everywhere. When ``llm_api_key`` is empty the answerer does not guess, does
not paraphrase the retrieved context and does not return empty text — it returns an explicit
abstention naming the reason (no provider configured). Nothing in this repo can mistake that for an
answer, and no test, gate or demo can spend money.

Live mode is one plain ``httpx`` POST to an OpenAI-shaped ``/chat/completions`` endpoint, with
**no** retries, no streaming and no provider SDK: retries would multiply cost and change what
"the answer" is, and a new dependency needs human approval. Failures raise GenerationUnavailable.

Credential discipline: the API key is read only to build the ``Authorization`` header of that one
request. This module logs nothing, puts no request material in an exception message, and stores no
settings on ``GenerationResult``, so the key cannot surface in any ``repr()``.
"""

from dataclasses import dataclass

import httpx

from lumen import prompts
from lumen.chunking import Chunk
from lumen.config import Settings
from lumen.schemas import GenerationMode

# A generous ceiling for one non-streaming completion; a hang must not outlive the request.
REQUEST_TIMEOUT_SECONDS = 20.0

# Shown verbatim when stub mode abstains, so the response explains its own state.
STUB_ANSWER = (
    "No answer generated: the answerer is disabled because LLM_API_KEY is empty (mode='stub'). "
    "Retrieval ran normally and the retrieved chunk ids are listed in this response, but stub mode "
    "does not produce answer text. Configure LLM_API_KEY to enable live generation."
)

# HTTP failures are reported by status code only. The provider body is never included: an upstream
# error page can echo request material, and the status code is the part known to be safe.
_TRANSPORT_FAILURE = "generation provider could not be reached"


class GenerationUnavailable(RuntimeError):
    """Live generation failed. Messages name the failure shape, never the request contents."""


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """What ``answer()`` produced. ``mode`` is the honesty field, ``abstained`` the usable one.

    Every field is required: an answerer that could silently default to ``mode="stub"`` or to
    ``abstained=False`` is the failure mode this stage exists to prevent.
    """

    text: str
    citations: list[str]
    abstained: bool
    mode: GenerationMode
    prompt_version: str


def generation_mode(settings: Settings) -> GenerationMode:
    """The single definition of stub vs live, shared by ``/health`` and ``answer()``."""
    return "live" if settings.llm_api_key else "stub"


def answer(
    question: str,
    retrieved: list[Chunk],
    scores: list[float],
    settings: Settings,
) -> GenerationResult:
    """Answer ``question`` from ``retrieved``; ``scores`` feed only the configured abstain gate.

    Decision order: stub mode short-circuits before any network access. Then the score threshold
    abstains only if one is configured — ``Settings.lumen_abstain_threshold`` defaults to ``None``,
    meaning never abstain on score, because this repo measured that no raw score cutoff separates
    abstention cases from answerable ones. Only then can a live request leave the process.
    """
    mode = generation_mode(settings)
    if mode == "stub":
        return GenerationResult(
            text=STUB_ANSWER,
            citations=[],
            abstained=True,
            mode="stub",
            prompt_version=prompts.PROMPT_VERSION,
        )

    threshold = settings.lumen_abstain_threshold
    top_score = max(scores) if scores else None
    if threshold is not None and (top_score is None or top_score < threshold):
        reason = "nothing was retrieved" if top_score is None else f"top score {top_score:.4f}"
        return GenerationResult(
            text=(
                f"Abstaining before generation: {reason} is below the configured "
                f"LUMEN_ABSTAIN_THRESHOLD of {threshold:.4f}."
            ),
            citations=[],
            abstained=True,
            mode=mode,
            prompt_version=prompts.PROMPT_VERSION,
        )

    text = _complete(question, retrieved, settings)
    # A refusal that followed the template abstains. Otherwise a chunk counts as cited only when its
    # id appears verbatim in the answer text. Both rules are crude and deterministic on purpose.
    if prompts.REFUSAL_LINE.lower() in text.lower():
        return GenerationResult(
            text=text,
            citations=[],
            abstained=True,
            mode=mode,
            prompt_version=prompts.PROMPT_VERSION,
        )
    citations = [chunk.id for chunk in retrieved if chunk.id in text]
    return GenerationResult(
        text=text,
        citations=citations,
        abstained=False,
        mode=mode,
        prompt_version=prompts.PROMPT_VERSION,
    )


def _complete(question: str, chunks: list[Chunk], settings: Settings) -> str:
    """One non-streaming completion request; any failure raises ``GenerationUnavailable``."""
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    prompt = prompts.build_answer_prompt(question, chunks)
    payload: dict[str, object] = {
        "model": settings.llm_model,
        # Pinned at 0: same question and same context should be as repeatable as a provider allows.
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    try:
        with _client() as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.RequestError as exc:
        raise GenerationUnavailable(_TRANSPORT_FAILURE) from exc
    if response.status_code >= 400:
        raise GenerationUnavailable(f"generation provider returned HTTP {response.status_code}")
    return _extract_text(response)


def _extract_text(response: httpx.Response) -> str:
    """Read ``choices[0].message.content`` from an OpenAI-shaped body, narrowing every step."""
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise GenerationUnavailable("generation provider returned a body that is not JSON") from exc
    if not isinstance(payload, dict):
        raise GenerationUnavailable("generation provider body is not a JSON object")
    choices: object = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise GenerationUnavailable("generation provider body has no completion choices")
    choice: object = choices[0]
    if not isinstance(choice, dict):
        raise GenerationUnavailable("generation provider choice is not a JSON object")
    message: object = choice.get("message")
    if not isinstance(message, dict):
        raise GenerationUnavailable("generation provider choice has no message")
    content: object = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise GenerationUnavailable("generation provider message has no answer text")
    return content.strip()


def _client() -> httpx.Client:
    """Transport seam: tests patch this to hand back a client on an ``httpx.MockTransport``."""
    return httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
