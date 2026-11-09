"""Smoke test: `import lumen` must work, so `pytest` can never pass on zero collected tests."""

import lumen


def test_lumen_is_importable_and_exposes_version() -> None:
    assert isinstance(lumen.__version__, str)
    assert lumen.__version__
