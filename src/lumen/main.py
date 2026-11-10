"""The HTTP surface of the harness: ``GET /health``, ``POST /search``, ``POST /ask``. Nothing else.

Deliberately absent: CORS, auth, rate limiting, docs customisation and middleware. None of them is
Stage 7, and each would be an unmeasured surface to defend later.

Configuration is read per request through ``config.get_settings()`` — not captured at import time —
so tests override it by monkeypatching that one function. ``GET /health`` reports the answerer's
``mode`` so a caller can see whether generation is real without probing it, and ``POST /ask``
echoes ``mode``, ``prompt_version`` and the retrieved context ids on every response.

The retrieval/answer layering stays intact: this module owns HTTP shape and nothing else.
"""

from fastapi import FastAPI, HTTPException

import lumen
from lumen import config, generation, retrieval, schemas
from lumen.chunking import load_corpus
from lumen.config import Settings

app = FastAPI()


@app.get("/health", response_model=schemas.HealthResponse)
def health() -> schemas.HealthResponse:
    """Service state, including whether generation is live or a stub. Makes no network call."""
    return schemas.HealthResponse(
        status="ok",
        version=lumen.__version__,
        chunk_count=len(load_corpus()),
        mode=generation.generation_mode(config.get_settings()),
    )


@app.post("/search", response_model=schemas.SearchResponse)
def search(request: schemas.SearchRequest) -> schemas.SearchResponse:
    """Ranked BM25 hits for ``query``. Deterministic: the same query and k give the same ids."""
    settings = config.get_settings()
    k = _effective_k(request.k, settings)
    found = retrieval.retrieve(request.query, k)
    hits = [
        schemas.SearchHit(
            chunk_id=chunk.id,
            doc_id=chunk.doc_id,
            title=chunk.title,
            snippet=_snippet(chunk.text),
            score=score,
        )
        for chunk, score in zip(found.chunks, found.scores, strict=True)
    ]
    return schemas.SearchResponse(
        query=request.query,
        k=k,
        hits=hits,
        top_score=found.top_score,
    )


@app.post("/ask", response_model=schemas.AskResponse)
def ask(request: schemas.AskRequest) -> schemas.AskResponse:
    """Retrieve, then answer. The answer is abstained unless the answerer actually produced a claim.

    A live provider failure is a 503 with the retrieval context dropped: it is the only honest thing
    to report, and the exception text is built to carry no request material.
    """
    settings = config.get_settings()
    k = _effective_k(request.k, settings)
    found = retrieval.retrieve(request.query, k)
    try:
        result = generation.answer(request.query, found.chunks, found.scores, settings)
    except generation.GenerationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return schemas.AskResponse(
        query=request.query,
        k=k,
        answer=result.text,
        citations=result.citations,
        context_chunk_ids=[chunk.id for chunk in found.chunks],
        abstained=result.abstained,
        mode=result.mode,
        prompt_version=result.prompt_version,
    )


def _effective_k(request_k: int | None, settings: Settings) -> int:
    """Caller's ``k`` wins; the configured default is clamped to the schema's own bounds."""
    if request_k is not None:
        return request_k
    return min(max(settings.lumen_top_k, schemas.K_MIN), schemas.K_MAX)


def _snippet(text: str) -> str:
    """First ``schemas.SNIPPET_MAX_CHARS`` characters of the chunk, so ``/search`` stays small."""
    if len(text) <= schemas.SNIPPET_MAX_CHARS:
        return text
    return text[: schemas.SNIPPET_MAX_CHARS - 1] + "…"
