"""Environment-backed settings: the only module in ``lumen`` that reads the environment.

Every value maps to a name in ``.env.example``. ``.env`` is optional: with no file and no exported
variables this module still constructs a complete ``Settings`` whose defaults are the honest,
non-paying defaults (no key, stub generation, no score threshold).

Test override point: routes call ``config.get_settings()`` per request rather than importing the
function object, so tests override configuration by monkeypatching ``lumen.config.get_settings``.
"""

import functools

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for retrieval depth, the generation provider and the abstain gate."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Empty key is the default everywhere, including `make check` and CI: it selects stub mode in
    # lumen.generation, so no test, gate or demo can spend money or depend on an external provider.
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = "https://api.example.com/v1"

    # Only eval/generation_eval.py reads this. Empty means the judge is the answering model itself,
    # which is the configuration that has self-preference bias and says so in its report. Setting it
    # is the one way to break that tie, and the only honest reading of a judge-vs-human agreement
    # number is the one where the judge is not the author.
    llm_judge_model: str = ""

    # Request default for `k` when the caller does not send one; the HTTP schema still bounds `k`.
    lumen_top_k: int = 5

    # Score-based abstention is DISABLED by default and must stay that way until a measurement
    # says otherwise. This repo measured raw retrieval scores on eval/report.md and found that no
    # raw score threshold separates abstention cases from answerable ones: abstention top_score
    # spans 4.3303-9.2034 while answerable top_score spans 4.5297-20.0727, and the cheapest
    # cutoff that covers all six abstentions (t > 9.2034) costs 10 of 18 answerable cases. Stage 8.5
    # re-ran this measurement after changing the tokenizer and the overlap got wider, not narrower.
    # So `None` means "never abstain on score", and setting this field requires re-reading the sweep
    # in the current eval/report.md — a number, not a guess.
    lumen_abstain_threshold: float | None = None


@functools.cache
def get_settings() -> Settings:
    """Process-wide cached settings; monkeypatch this function in tests to inject configuration."""
    return Settings()
