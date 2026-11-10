"""Versioned prompt templates: the only place answer wording lives.

``PROMPT_VERSION`` is stamped onto every answer (``/ask`` and the generation eval) so a change in
answer quality can be attributed to a change in wording rather than to luck. Bump it whenever
``ANSWER_TEMPLATE`` changes in a way that could change an answer.

The template is deliberately short: it constrains the answerer (context only, cite ids, refuse
explicitly) instead of coaching it. ``REFUSAL_LINE`` is interpolated into the template from here, so
what the model is told to say and what ``lumen.generation`` looks for cannot drift apart.
"""

from collections.abc import Sequence

from lumen.chunking import Chunk

PROMPT_VERSION: str = "v1"

#: The exact refusal the model is told to emit verbatim; detection lives in ``lumen.generation``.
REFUSAL_LINE = "I cannot answer this from the provided context."

#: Placeholder-only template; ``{context}`` and ``{question}`` are filled in below.
ANSWER_TEMPLATE = """\
Answer the question using ONLY the context below. Do not use prior knowledge or invent facts.

Context:
{context}

Question: {question}

Rules:
- If the context does not answer the question, reply with exactly this line and nothing else: \
{refusal_line}
- Otherwise answer in two or three sentences.
- On the final line of a non-refusal answer, list the chunk ids you used, formatted exactly as: \
Cited: <comma-separated chunk ids>
"""

# Rendered ahead of the context block when retrieval returned nothing, so the prompt stays valid.
_NO_CONTEXT = "(nothing retrieved)"


def build_answer_prompt(question: str, chunks: Sequence[Chunk]) -> str:
    """Render the answer prompt; chunk ids are shown so the answer can cite them."""
    if chunks:
        context = "\n\n".join(f"[{chunk.id}]\n{chunk.title}\n{chunk.text}" for chunk in chunks)
    else:
        context = _NO_CONTEXT
    return ANSWER_TEMPLATE.format(context=context, question=question, refusal_line=REFUSAL_LINE)
