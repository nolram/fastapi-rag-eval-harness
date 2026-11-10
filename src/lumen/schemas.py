"""Pydantic v2 request and response models for the HTTP surface: the contract is in one place.

Two rules this file exists to enforce:

* Input is bounded at the boundary. ``query`` is stripped and must be non-empty after stripping,
  and ``k`` is optional but bounded ``1..20`` when supplied, so no request asks for a huge dump.
* Output is an explicit model. Routes return these classes, never bare dicts, so the JSON shape
  the generation eval (Stage 11) consumes is declared here and visible in OpenAPI.

``GenerationMode`` lives here rather than in ``lumen.generation`` so the response models can name it
without importing the module that talks to a provider.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

#: How the answerer produced its answer: ``stub`` never contacted a provider, ``live`` did.
GenerationMode = Literal["stub", "live"]

#: A non-empty query. ``strip_whitespace`` runs before ``min_length``, so "   " is rejected too.
QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

#: Retrieval depth bounds, spelled once: the request field and the response echo use the same pair.
K_MIN = 1
K_MAX = 20

#: Optional caller-supplied retrieval depth. ``None`` means "use ``settings.lumen_top_k``".
KLimit = Annotated[int | None, Field(default=None, ge=K_MIN, le=K_MAX)]

#: Retrieval depth actually used by a request, echoed back so a response is self-describing.
AppliedK = Annotated[int, Field(ge=K_MIN, le=K_MAX)]

# Snippets are a preview of the chunk, not a copy of it: bounded so /search stays a small payload.
SNIPPET_MAX_CHARS = 200


class SearchRequest(BaseModel):
    """``POST /search`` body: a query and an optional, bounded retrieval depth."""

    query: QueryText
    k: KLimit


class AskRequest(BaseModel):
    """``POST /ask`` body: the same bounded input as ``/search`` — retrieval is the same call."""

    query: QueryText
    k: KLimit


class SearchHit(BaseModel):
    """One ranked chunk. ``snippet`` is the first ``SNIPPET_MAX_CHARS`` characters of chunk text."""

    chunk_id: str
    doc_id: str
    title: str
    snippet: str
    score: float


class SearchResponse(BaseModel):
    """Ranked hits plus the top score; ``top_score`` is ``None`` when nothing matched at all."""

    query: str
    k: AppliedK
    hits: list[SearchHit]
    top_score: float | None


class AskResponse(BaseModel):
    """The generated answer and the full evidence trail behind it.

    ``context_chunk_ids`` is everything retrieval offered, ``citations`` is what the answer claims
    to have used. ``abstained`` is true whenever ``answer`` is not a claim of fact, in either mode.
    """

    query: str
    k: AppliedK
    answer: str
    citations: list[str]
    context_chunk_ids: list[str]
    abstained: bool
    mode: GenerationMode
    prompt_version: str


class HealthResponse(BaseModel):
    """Service state without probing it: ``mode`` reports the answerer's honest state."""

    status: str
    version: str
    chunk_count: int
    mode: GenerationMode
