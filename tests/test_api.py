"""HTTP tests for the Stage 7 service: ``/health``, ``/search``, ``/ask`` — with the network shut.

Two mechanisms carry the honesty of this file:

* Configuration is overridden by monkeypatching ``lumen.config.get_settings``, the override point
  documented in that module. Routes call it per request, so a patch takes effect immediately.
* Nothing leaves the machine. Stub-mode tests install the tripwire below, which raises
  ``AssertionError("network call attempted from a test")`` from ``httpx.post`` and
  ``httpx.Client.post``. Live-mode tests point ``lumen.generation._client`` at an
  ``httpx.MockTransport``, which answers the provider call in process — the tripwire is not used
  there, because the mocked provider is itself an ``httpx.Client`` calling ``Client.post``.

``TestClient`` also inherits ``httpx.Client.post``, so while the tripwire is installed these tests
issue their own requests through ``post_json``, which calls ``Client.request``.
"""

import json
from itertools import pairwise
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from fastapi.testclient import TestClient

import lumen
from lumen import config, generation, prompts, retrieval
from lumen.chunking import Chunk, load_corpus
from lumen.config import Settings
from lumen.main import app
from lumen.schemas import SNIPPET_MAX_CHARS

RATE_LIMIT_QUERY = "What is the default rate limit for the API?"
OFF_DOMAIN_QUERY = "How do I recalibrate the flux capacitor on a 1998 sedan?"
API_KEY_SENTINEL = "sk-SENTINEL-NEVER-LEAKS"
TEST_MODEL = "test-model-v1"
TEST_BASE_URL = "http://provider.invalid/v1"
#: Every env name this service reads, cleared together so a developer's shell cannot set the mode.
SETTING_ENV_NAMES = (
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LUMEN_TOP_K",
    "LUMEN_ABSTAIN_THRESHOLD",
)
#: The exact ``/ask`` JSON contract. Stage 11's judge parses this, so the key set is asserted.
ASK_RESPONSE_KEYS = {
    "query",
    "k",
    "answer",
    "citations",
    "context_chunk_ids",
    "abstained",
    "mode",
    "prompt_version",
}


def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the network tripwire on both entry points: ``httpx.post`` and ``Client.post``."""

    def blocked(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("network call attempted from a test")

    monkeypatch.setattr(httpx, "post", blocked)
    monkeypatch.setattr(httpx.Client, "post", blocked)


def post_json(client: TestClient, path: str, payload: dict[str, object]) -> httpx.Response:
    """POST a JSON body via ``Client.request``; ``Client.post`` is the tripwired method."""
    return client.request("POST", path, json=payload)


def sample_chunks() -> list[Chunk]:
    """Two real corpus chunks, so mocked citations are ids that actually exist in the corpus."""
    return load_corpus()[:2]


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


class ProviderRecorder:
    """A ``MockTransport`` provider: records the one outbound request, answers with ``reply``."""

    def __init__(self, reply: httpx.Response) -> None:
        self.reply = reply
        self.url = ""
        self.headers: dict[str, str] = {}
        self.body: dict[str, object] = {}
        self.calls = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Point ``lumen.generation._client`` at this transport, so no socket is ever opened."""
        transport = httpx.MockTransport(self._handler)
        monkeypatch.setattr(generation, "_client", lambda: httpx.Client(transport=transport))

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.url = str(request.url)
        self.headers = dict(request.headers)
        self.body = _json_object(request)
        return self.reply


def _json_object(request: httpx.Request) -> dict[str, object]:
    """The outgoing request body as a dict, so tests assert on the payload itself, not on intent."""
    payload: object = json.loads(request.content.decode("utf-8"))
    assert isinstance(payload, dict)
    return {str(key): value for key, value in payload.items()}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """Clear the setting env vars and move CWD off the repo, so defaults come from the code."""
    for name in SETTING_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # a local, gitignored `.env` must not decide the mode under test
    return monkeypatch


@pytest.fixture()
def stub_mode(clean_env: pytest.MonkeyPatch) -> None:
    """Force stub mode through the documented override point, bypassing the process-wide cache."""
    assert Settings().llm_api_key == ""  # the environment really is empty before we assert anything
    clean_env.setattr(config, "get_settings", Settings)


def test_settings_defaults_are_the_non_paying_defaults(clean_env: pytest.MonkeyPatch) -> None:
    """No key, no model, no score threshold: a fresh checkout spends nothing and guesses nothing."""
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.llm_api_key == ""
    assert settings.llm_model == ""
    assert settings.lumen_top_k == 5
    assert settings.lumen_abstain_threshold is None


def test_health_reports_stub_mode_with_no_key_in_the_environment(
    client: TestClient, clean_env: pytest.MonkeyPatch
) -> None:
    clean_env.delenv("LLM_API_KEY", raising=False)
    config.get_settings.cache_clear()
    body = client.get("/health").json()
    assert body["mode"] == "stub"
    assert body["status"] == "ok"
    assert body["version"] == lumen.__version__
    assert body["chunk_count"] == len(load_corpus())


def test_health_mode_agrees_with_the_answerer_when_a_key_is_present(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/health`` is not decoration: with a key it reports live, not stub."""
    monkeypatch.setattr(config, "get_settings", lambda: live_settings())
    assert client.get("/health").json()["mode"] == "live"


def test_search_returns_real_chunk_ids_in_non_increasing_score_order(client: TestClient) -> None:
    corpus_ids = {chunk.id for chunk in load_corpus()}
    response = post_json(client, "/search", {"query": RATE_LIMIT_QUERY, "k": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["k"] == 3
    assert len(body["hits"]) == 3
    ids = [hit["chunk_id"] for hit in body["hits"]]
    assert set(ids) <= corpus_ids
    scores = [hit["score"] for hit in body["hits"]]
    assert all(first >= second for first, second in pairwise(scores))
    assert body["top_score"] == body["hits"][0]["score"]
    for hit in body["hits"]:
        assert len(hit["snippet"]) <= SNIPPET_MAX_CHARS
        assert hit["doc_id"]
        assert hit["title"]


def test_search_is_deterministic_for_the_same_query(client: TestClient) -> None:
    first = post_json(client, "/search", {"query": RATE_LIMIT_QUERY, "k": 5}).json()
    second = post_json(client, "/search", {"query": RATE_LIMIT_QUERY, "k": 5}).json()
    assert first == second
    assert [hit["chunk_id"] for hit in first["hits"]] == [hit["chunk_id"] for hit in second["hits"]]


def test_search_and_ask_reject_out_of_range_k_and_blank_queries(client: TestClient) -> None:
    for payload in (
        {"query": RATE_LIMIT_QUERY, "k": 0},
        {"query": RATE_LIMIT_QUERY, "k": 99},
        {"query": "", "k": 5},
        {"query": "   ", "k": 5},
    ):
        assert post_json(client, "/search", payload).status_code == 422, payload
    for payload in ({"query": RATE_LIMIT_QUERY, "k": 0}, {"query": "   "}):
        assert post_json(client, "/ask", payload).status_code == 422, payload


def test_ask_in_stub_mode_makes_no_network_call(
    client: TestClient, stub_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tripwire is the proof, not an assumption: any outbound httpx call raises
    ``AssertionError("network call attempted from a test")``, so this 200 is evidence."""
    block_network(monkeypatch)
    response = post_json(client, "/ask", {"query": RATE_LIMIT_QUERY})
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is True
    assert body["mode"] == "stub"
    assert body["prompt_version"] == prompts.PROMPT_VERSION
    assert body["citations"] == []
    assert body["context_chunk_ids"]  # retrieval ran normally in stub mode
    assert "LLM_API_KEY" in body["answer"]  # the abstention names its own reason


def test_ask_stub_response_has_exactly_the_documented_shape(
    client: TestClient, stub_mode: None
) -> None:
    body = post_json(client, "/ask", {"query": RATE_LIMIT_QUERY}).json()
    assert set(body) == ASK_RESPONSE_KEYS
    assert isinstance(body["answer"], str) and body["answer"]
    assert isinstance(body["citations"], list)
    assert isinstance(body["context_chunk_ids"], list)
    assert set(body["citations"]) <= set(body["context_chunk_ids"])
    assert isinstance(body["abstained"], bool)
    assert body["mode"] in {"stub", "live"}
    assert isinstance(body["prompt_version"], str)


def test_ask_off_domain_query_still_returns_a_well_formed_response(
    client: TestClient, stub_mode: None
) -> None:
    """Off-domain input must still be well-formed. Nothing here claims the answer is correct: the
    generation eval does not exist yet, so no test in this repo may assert generation quality."""
    response = post_json(client, "/ask", {"query": OFF_DOMAIN_QUERY})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == ASK_RESPONSE_KEYS
    assert body["mode"] == "stub"
    assert body["abstained"] is True
    assert isinstance(body["context_chunk_ids"], list)


def test_live_request_carries_temperature_model_and_hides_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live leg, answered by ``MockTransport``: every assertion is about the real request."""
    chunks = sample_chunks()
    recorder = ProviderRecorder(
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": f"60 requests/minute. Cited: {chunks[0].id}"}}]
            },
        )
    )
    recorder.install(monkeypatch)

    result = generation.answer(RATE_LIMIT_QUERY, chunks, [9.5, 4.0], live_settings())

    assert recorder.calls == 1
    assert recorder.url == f"{TEST_BASE_URL}/chat/completions"
    assert recorder.body["temperature"] == 0
    assert recorder.body["model"] == TEST_MODEL
    messages = recorder.body["messages"]
    assert isinstance(messages, list) and isinstance(messages[0], dict)
    prompt = messages[0]["content"]
    assert chunks[0].id in prompt
    assert RATE_LIMIT_QUERY in prompt
    assert prompts.REFUSAL_LINE in prompt
    assert recorder.headers["authorization"] == f"Bearer {API_KEY_SENTINEL}"
    assert result.mode == "live"
    assert result.abstained is False
    assert result.citations == [chunks[0].id]
    assert API_KEY_SENTINEL not in repr(result)


def test_live_refusal_abstains_without_inventing_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = ProviderRecorder(
        httpx.Response(200, json={"choices": [{"message": {"content": prompts.REFUSAL_LINE}}]})
    )
    recorder.install(monkeypatch)
    result = generation.answer(OFF_DOMAIN_QUERY, sample_chunks(), [3.0], live_settings())
    assert recorder.calls == 1
    assert result.mode == "live"
    assert result.abstained is True
    assert result.citations == []
    assert result.prompt_version == prompts.PROMPT_VERSION


def test_live_bad_status_raises_without_leaking_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = ProviderRecorder(
        httpx.Response(502, json={"error": {"message": "upstream exploded"}})
    )
    recorder.install(monkeypatch)
    with pytest.raises(generation.GenerationUnavailable) as raised:
        generation.answer(RATE_LIMIT_QUERY, sample_chunks(), [9.0], live_settings())
    assert recorder.calls == 1
    assert API_KEY_SENTINEL not in repr(raised.value)
    assert API_KEY_SENTINEL not in repr(raised.value.__cause__)
    assert "502" in str(raised.value)


def test_live_transport_error_raises_without_leaking_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    monkeypatch.setattr(generation, "_client", lambda: httpx.Client(transport=transport))
    with pytest.raises(generation.GenerationUnavailable) as raised:
        generation.answer(RATE_LIMIT_QUERY, sample_chunks(), [9.0], live_settings())
    assert API_KEY_SENTINEL not in repr(raised.value)
    assert API_KEY_SENTINEL not in repr(raised.value.__cause__)


def test_ask_live_path_answers_through_mock_transport(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end live path. MockTransport (not the tripwire) keeps it on the machine: the mocked
    provider is itself an ``httpx.Client``, whose ``post`` the tripwire would have to block."""
    monkeypatch.setattr(config, "get_settings", lambda: live_settings())
    retrieved = retrieval.retrieve(RATE_LIMIT_QUERY, 5)
    cited = retrieved.chunks[0].id
    recorder = ProviderRecorder(
        httpx.Response(
            200, json={"choices": [{"message": {"content": f"60/min. Cited: {cited}"}}]}
        ),
    )
    recorder.install(monkeypatch)

    response = post_json(client, "/ask", {"query": RATE_LIMIT_QUERY})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == ASK_RESPONSE_KEYS
    assert body["mode"] == "live"
    assert body["abstained"] is False
    assert body["context_chunk_ids"] == [chunk.id for chunk in retrieved.chunks]
    assert body["citations"] == [cited]
    assert body["prompt_version"] == prompts.PROMPT_VERSION
    assert API_KEY_SENTINEL not in response.text


def test_ask_live_provider_failure_is_503_without_the_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "get_settings", lambda: live_settings())
    ProviderRecorder(httpx.Response(500, json={"error": {"message": "no"}})).install(monkeypatch)
    response = post_json(client, "/ask", {"query": RATE_LIMIT_QUERY})
    assert response.status_code == 503
    assert "500" in response.text
    assert API_KEY_SENTINEL not in response.text


def test_no_score_abstention_when_the_threshold_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Threshold unset means "never abstain on score": a weak score still reaches the provider."""
    recorder = ProviderRecorder(
        httpx.Response(200, json={"choices": [{"message": {"content": "answer text"}}]})
    )
    recorder.install(monkeypatch)
    result = generation.answer(RATE_LIMIT_QUERY, sample_chunks(), [0.1], live_settings())
    assert recorder.calls == 1
    assert result.abstained is False
    assert result.mode == "live"


def test_configured_threshold_abstains_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a threshold (cited from eval/report.md, never guessed) a lower top score never reaches
    the wire — the tripwire would raise, and ``calls == 0`` records that it did not."""
    block_network(monkeypatch)
    recorder = ProviderRecorder(
        httpx.Response(200, json={"choices": [{"message": {"content": "must not be called"}}]})
    )
    recorder.install(monkeypatch)
    result = generation.answer(
        RATE_LIMIT_QUERY,
        sample_chunks(),
        [4.4],
        live_settings(lumen_abstain_threshold=4.4589),
    )
    assert recorder.calls == 0
    assert result.abstained is True
    assert result.mode == "live"
    assert "LUMEN_ABSTAIN_THRESHOLD" in result.text


def test_stub_mode_never_reaches_the_provider_even_with_a_weak_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub mode short-circuits first: not even a threshold it would fail can cause a request."""
    block_network(monkeypatch)
    recorder = ProviderRecorder(httpx.Response(200, json={}))
    recorder.install(monkeypatch)
    settings = Settings(llm_api_key="", llm_model="", lumen_abstain_threshold=9.0)
    result = generation.answer(RATE_LIMIT_QUERY, sample_chunks(), [0.0], settings)
    assert recorder.calls == 0
    assert (result.mode, result.abstained) == ("stub", True)
