"""Tests for the heading-aware chunker: id format, ordering, coverage, determinism."""

import dataclasses
import re
from pathlib import Path

import pytest

from lumen import chunking
from lumen.chunking import Chunk, chunk_markdown, load_corpus

CORPUS_DIR = Path(chunking.__file__).parent / "corpus"
H2_LINE = re.compile(r"##\s")

RATE_LIMITS_PREAMBLE = "Every API key is rate limited per environment."
RATE_LIMITS_FACT = "Default rate limit is 120 requests per minute per API key."

GUIDE_DOC = """# Widget guide

Intro sentence for widgets.

## Installing widgets

Install with the packagist tool.

## Removing widgets

Remove with the purge tool.
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _h2_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if H2_LINE.match(line))


def _by_id(chunks: list[Chunk]) -> dict[str, Chunk]:
    return {chunk.id: chunk for chunk in chunks}


def test_ids_are_document_scoped_and_one_based() -> None:
    chunks = chunk_markdown("x.md", GUIDE_DOC)
    assert chunks[0].id == "x#1"
    assert [chunk.id for chunk in chunks] == ["x#1", "x#2"]


def test_numbering_restarts_per_document_in_filename_order(tmp_path: Path) -> None:
    _write(tmp_path / "zeta.md", "# Zeta\n\nZeta preamble.\n\n## Zeta one\n\nZeta body one.\n")
    _write(
        tmp_path / "alpha.md",
        "# Alpha\n\n## Alpha one\n\nAlpha body one.\n\n## Alpha two\n\nAlpha body two.\n"
        "\n## Alpha three\n\nAlpha body three.\n",
    )
    chunks = load_corpus(tmp_path)
    assert [chunk.id for chunk in chunks] == [
        "alpha#1",
        "alpha#2",
        "alpha#3",
        "zeta#1",
    ]


def test_doc_id_keeps_the_markdown_extension() -> None:
    assert {chunk.doc_id for chunk in chunk_markdown("x.md", GUIDE_DOC)} == {"x.md"}
    assert all(chunk.doc_id.endswith(".md") for chunk in load_corpus())


def test_every_h2_heading_of_every_corpus_doc_yields_exactly_one_chunk() -> None:
    files = sorted(CORPUS_DIR.glob("*.md"), key=lambda path: path.name)
    assert files, "the package corpus must ship inside src/lumen/corpus"
    expected_total = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        expected = _h2_count(text)
        assert expected > 0, path.name
        assert len(chunk_markdown(path.name, text)) == expected, path.name
        expected_total += expected
    assert len(load_corpus()) == expected_total


def test_corpus_chunk_order_is_filename_then_section() -> None:
    expected: list[str] = []
    for path in sorted(CORPUS_DIR.glob("*.md"), key=lambda item: item.name):
        count = _h2_count(path.read_text(encoding="utf-8"))
        expected.extend(f"{path.stem}#{number}" for number in range(1, count + 1))
    assert [chunk.id for chunk in load_corpus()] == expected


def test_no_corpus_chunk_is_empty_and_no_id_is_duplicated() -> None:
    chunks = load_corpus()
    assert [chunk.id for chunk in chunks if not chunk.text.strip()] == []
    assert len({chunk.id for chunk in chunks}) == len(chunks)
    assert len({chunk.doc_id for chunk in chunks}) == len(list(CORPUS_DIR.glob("*.md")))


def test_rate_limits_first_chunk_holds_the_mandated_fact() -> None:
    assert RATE_LIMITS_FACT in _by_id(load_corpus())["rate_limits#1"].text


def test_h1_preamble_is_prepended_to_the_first_chunk_only() -> None:
    chunks = load_corpus()
    first = _by_id(chunks)["rate_limits#1"]
    assert first.text.startswith(RATE_LIMITS_PREAMBLE)
    assert first.text.count(RATE_LIMITS_PREAMBLE) == 1
    assert [chunk.id for chunk in chunks if RATE_LIMITS_PREAMBLE in chunk.text] == ["rate_limits#1"]


def test_first_chunk_layout_is_preamble_blank_line_then_section_body() -> None:
    head, separator, body = _by_id(load_corpus())["rate_limits#1"].text.partition("\n\n")
    assert separator == "\n\n"
    assert head == RATE_LIMITS_PREAMBLE
    assert body.startswith("Default rate limit is 120 requests per minute per API key.")


def test_title_is_document_title_em_dash_section_heading() -> None:
    chunks = load_corpus()
    assert _by_id(chunks)["rate_limits#1"].title == "Rate limits — Default rate limits"
    assert [chunk.id for chunk in chunks if " — " not in chunk.title] == []


def test_heading_words_survive_in_every_corpus_title() -> None:
    for path in sorted(CORPUS_DIR.glob("*.md"), key=lambda item: item.name):
        text = path.read_text(encoding="utf-8")
        headings = [line[2:].strip() for line in text.splitlines() if H2_LINE.match(line)]
        chunks = chunk_markdown(path.name, text)
        assert [chunk.title.split(" — ", 1)[1] for chunk in chunks] == headings


def test_document_without_h2_heading_yields_no_chunks() -> None:
    assert chunk_markdown("nope.md", "# Nope\n\nJust a paragraph, no sections.\n") == []
    assert chunk_markdown("empty.md", "") == []


def test_load_corpus_returns_no_chunks_for_a_corpus_without_h2_headings(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "flat.md", "# Flat\n\nNothing here but a preamble.\n")
    assert load_corpus(tmp_path) == []


def test_load_corpus_reads_only_markdown_files(tmp_path: Path) -> None:
    _write(tmp_path / "beta.md", "# Beta\n\n## Beta section\n\nBeta body.\n")
    _write(tmp_path / "alpha.md", "# Alpha\n\n## Alpha section\n\nAlpha body.\n")
    _write(tmp_path / "notes.txt", "# Notes\n\n## Ignored section\n\nIgnored body.\n")
    assert [chunk.id for chunk in load_corpus(tmp_path)] == ["alpha#1", "beta#1"]


def test_heading_levels_deeper_than_two_stay_inside_their_section() -> None:
    document = (
        "# Levels\n\n## First\n\nTop line.\n\n### Deeper\n\nDeeper body.\n\n#### Deepest"
        "\n\nDeepest body.\n\n## Second\n\nSecond body.\n"
    )
    chunks = chunk_markdown("levels.md", document)
    assert [chunk.id for chunk in chunks] == ["levels#1", "levels#2"]
    assert "### Deeper" in chunks[0].text
    assert "Deeper body." in chunks[0].text
    assert "#### Deepest" in chunks[0].text
    assert chunks[1].text == "Second body."
    assert [chunk.title for chunk in chunks] == ["Levels — First", "Levels — Second"]


def test_heading_formatting_is_stripped_from_title_but_kept_in_text() -> None:
    document = (
        "# Alpha\n\n## The `Retry-After` **header** and _why_ it matters\n\n"
        "Body keeps `Retry-After` verbatim.\n\n## Handling `429 too_many_requests`\n\n"
        "Body two.\n"
    )
    chunks = chunk_markdown("fmt.md", document)
    assert [chunk.title for chunk in chunks] == [
        "Alpha — The Retry-After header and why it matters",
        "Alpha — Handling 429 too_many_requests",
    ]
    assert "`Retry-After`" in chunks[0].text
    assert "`Retry-After`" not in chunks[0].title


def test_text_is_whitespace_normalised() -> None:
    document = "# T\n\n## A\n\n\nfirst line   \n\n\n\nsecond line\n\n\n## B\n\nb\n\n"
    chunks = chunk_markdown("ws.md", document)
    assert [chunk.id for chunk in chunks] == ["ws#1", "ws#2"]
    assert chunks[0].text == "first line\n\nsecond line"
    assert chunks[1].text == "b"


def test_empty_section_still_occupies_its_chunk_number() -> None:
    chunks = chunk_markdown("sparse.md", "# T\n\n## A\n\n## B\n\nBody B.\n")
    assert [chunk.id for chunk in chunks] == ["sparse#1", "sparse#2"]
    assert chunks[0].text == ""
    assert chunks[1].text == "Body B."


def test_document_without_h1_keeps_its_preamble_and_its_chunk_count() -> None:
    chunks = chunk_markdown("bare.md", "Preamble with no H1 above it.\n\n## Only\n\nBody.\n")
    assert [chunk.id for chunk in chunks] == ["bare#1"]
    assert chunks[0].title == "Only"
    assert chunks[0].text == "Preamble with no H1 above it.\n\nBody."


def test_load_corpus_is_deterministic() -> None:
    assert load_corpus() == load_corpus()
    assert load_corpus(CORPUS_DIR) == load_corpus()
    ids = [chunk.id for chunk in load_corpus()]
    for _ in range(3):
        assert [chunk.id for chunk in load_corpus()] == ids
    texts = [(chunk.id, chunk.title, chunk.text) for chunk in load_corpus()]
    for _ in range(3):
        assert [(chunk.id, chunk.title, chunk.text) for chunk in load_corpus()] == texts


def test_chunk_is_a_frozen_slots_dataclass() -> None:
    chunk = Chunk(id="x#1", doc_id="x.md", title="X — One", text="Body.")
    assert chunk == Chunk(id="x#1", doc_id="x.md", title="X — One", text="Body.")
    assert chunk.__slots__ == ("id", "doc_id", "title", "text")
    assert not hasattr(chunk, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.text = "changed"  # type: ignore[misc]
