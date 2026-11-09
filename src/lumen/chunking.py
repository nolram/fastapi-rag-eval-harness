"""Heading-aware markdown chunker: one chunk per ``##`` section of a corpus document.

Deterministic: documents load in filename sort order and sections in document order, so the same
corpus always produces the same chunk ids and the same chunk text.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_H1_HEADING = re.compile(r"^#\s+(.*)$")
_H2_HEADING = re.compile(r"^##\s+(.*)$")
_TITLE_FORMATTING: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"(?<!\w)\*([^*]+)\*(?!\w)"), r"\1"),
    (re.compile(r"(?<!\w)_([^_]+)_(?!\w)"), r"\1"),
)
_TITLE_STRAY_MARKERS = ("`", "*")
_SECTION_SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class Chunk:
    """One ``##`` section of a corpus document, addressed as ``<doc_stem>#<n>`` from 1."""

    id: str
    doc_id: str
    title: str
    text: str


def chunk_markdown(doc_id: str, text: str) -> list[Chunk]:
    """Split one markdown document into exactly one chunk per ``##`` heading, numbered from 1."""
    lines = text.splitlines()
    sections: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _H2_HEADING.match(line)
        if match is not None:
            sections.append((index, _plain(match.group(1))))
    if not sections:
        return []

    document_title = _document_title(lines[: sections[0][0]])
    preamble = _normalise(_body_lines(lines[: sections[0][0]]))
    stem = Path(doc_id).stem

    chunks: list[Chunk] = []
    for number, (start, heading) in enumerate(sections, start=1):
        end = sections[number][0] if number < len(sections) else len(lines)
        body = _normalise(lines[start + 1 : end])
        if number == 1 and preamble:
            body = f"{preamble}{_SECTION_SEPARATOR}{body}" if body else preamble
        title = f"{document_title} — {heading}" if document_title else heading
        chunks.append(Chunk(id=f"{stem}#{number}", doc_id=doc_id, title=title, text=body))
    return chunks


def load_corpus(corpus_dir: Path | None = None) -> list[Chunk]:
    """Load every ``*.md`` document in ``corpus_dir``, defaulting to the corpus in this package."""
    directory = Path(__file__).parent / "corpus" if corpus_dir is None else corpus_dir
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md"), key=lambda item: item.name):
        chunks.extend(chunk_markdown(path.name, path.read_text(encoding="utf-8")))
    return chunks


def _document_title(lines: Sequence[str]) -> str:
    for line in lines:
        match = _H1_HEADING.match(line)
        if match is not None:
            return _plain(match.group(1))
    return ""


def _body_lines(lines: Sequence[str]) -> list[str]:
    """Lines before the first ``##``, excluding the H1 heading line itself."""
    return [line for line in lines if _H1_HEADING.match(line) is None]


def _plain(heading: str) -> str:
    """Strip emphasis and inline-code markers from a heading while keeping its words."""
    plain = heading
    for pattern, replacement in _TITLE_FORMATTING:
        plain = pattern.sub(replacement, plain)
    for marker in _TITLE_STRAY_MARKERS:
        plain = plain.replace(marker, "")
    return " ".join(plain.split())


def _normalise(lines: Sequence[str]) -> str:
    """Strip the block, trim trailing spaces per line, collapse runs of blank lines to one."""
    kept: list[str] = []
    for line in lines:
        trimmed = line.rstrip()
        if not trimmed:
            if kept and kept[-1]:
                kept.append("")
        else:
            kept.append(trimmed)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept)
