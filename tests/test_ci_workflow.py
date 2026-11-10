"""CI workflow files are read by GitHub and by nothing else in this repo.

Incident I20 in ``docs/agent-dossier.md``: one step was named
``- name: make eval-generation: no key, no network, network-blocked``. A plain YAML scalar cannot
hold a second ``": "``, so the parser saw a nested mapping where a string belonged and rejected the
whole file: the run reported failure in 0s with **zero jobs** while ``make check`` was green on the
same commit.

That is the gap this file closes, locally and with no new dependency. PyYAML is deliberately absent
from the repo, so the scanner below is textual by necessity: stdlib only (``pathlib``, ``re``,
``pytest``).

Scope is the defect that happened plus its zero-jobs symptom. I20's other half -- a commit touching
``.github/`` producing a CI run *with* jobs -- needs the GitHub API and stays a manual step.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

#: A job key: two or more leading spaces, then an identifier, then a colon.
JOB_KEY_LINE = re.compile(r"^[ ]{2,}[A-Za-z0-9_-]+:")

# The two wordings I20 is recorded with: docs/agent-dossier.md quotes the parenthesised name, the
# Stage 13 brief quotes the colon-separated one. They are the same defect, so both are asserted.
I20_BROKEN_LINES = (
    "      - name: make eval-generation: no key, no network, network-blocked",
    "      - name: make eval-generation (no key available: must skip cleanly with exit 0)",
)


def _workflow_files() -> list[Path]:
    """The workflow files in a stable order; the guard test below makes an empty list loud."""
    if not WORKFLOWS_DIR.is_dir():
        return []
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


WORKFLOW_FILES = _workflow_files()


def unquoted_colon_names(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, line_text)`` for every ``- name:`` line whose value is an unquoted
    plain scalar containing ``": "``.

    Line numbers are 1-based. A value wrapped in ``'...'`` or ``"..."`` is quoted YAML and is legal:
    the ``": "`` inside it is data, not structure, so it is never reported. A line that merely
    contains ``name:`` but is not a sequence item is out of scope -- this is a scanner for one
    defect, not a YAML parser.
    """
    findings: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("- name:"):
            continue
        value = stripped[len("- name:") :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            continue
        if ": " in value:
            findings.append((number, line))
    return findings


# ------------------------------------------------------------------ 1. the detector, and the
# inverted tripwire that proves it is not decorative.


@pytest.mark.parametrize("line", I20_BROKEN_LINES, ids=("colon-form", "parenthesised-form"))
def test_reports_the_exact_historical_line_that_broke_ci(line: str) -> None:
    findings = unquoted_colon_names(f"jobs:\n{line}\n")
    assert findings == [(2, line)]


def test_quoting_is_the_only_difference_between_a_live_run_and_a_dead_one() -> None:
    good = 'jobs:\n  - name: "make eval-generation: no key"\n'
    bad = "jobs:\n  - name: make eval-generation: no key\n"
    # Inverted tripwire, per the Stage 8 convention: this test passes when the scanner *notices*
    # the defect, so a green run here is evidence the defect would be caught, not that it is absent.
    assert unquoted_colon_names(good) == []
    assert unquoted_colon_names(bad) == [(2, "  - name: make eval-generation: no key")]


def test_a_single_quoted_value_is_as_legal_as_a_double_quoted_one() -> None:
    text = (
        "steps:\n  - name: 'make eval-generation: no key'\n  - name: \"make eval-generation: x\"\n"
    )
    assert unquoted_colon_names(text) == []


def test_a_step_name_without_a_colon_space_is_never_reported() -> None:
    text = "steps:\n  - name: make eval-retrieval (golden-set gates)\n  - name: Checkout\n"
    assert unquoted_colon_names(text) == []


def test_a_mapping_key_called_name_is_out_of_scope_for_this_detector() -> None:
    """``name: CI`` at column 0 is the workflow's own name, not a step, so it is not examined."""
    assert unquoted_colon_names("name: CI: push and pull request\njobs:\n") == []


def test_reports_every_offending_line_rather_than_stopping_at_the_first() -> None:
    broken = I20_BROKEN_LINES[0]
    text = f"steps:\n{broken}\n  - name: Checkout\n{broken}\n"
    assert [number for number, _ in unquoted_colon_names(text)] == [2, 4]


# ------------------------------------------------------------------ 2. the real workflows,
# scanned. I14 is the failure mode this section exists to avoid: a check that inspected nothing
# and reported success anyway, so the file count is asserted before it is trusted.


def test_the_workflows_directory_is_present_and_actually_contains_workflows() -> None:
    assert WORKFLOWS_DIR.is_dir(), f"expected a workflows directory at {WORKFLOWS_DIR}"
    assert WORKFLOW_FILES, (
        f"no .yml/.yaml workflow files under {WORKFLOWS_DIR}: the two tests below would scan "
        "nothing and pass, which is exactly the I14 failure mode"
    )


@pytest.mark.parametrize("workflow", WORKFLOW_FILES, ids=lambda path: path.name)
def test_no_step_name_can_get_the_workflow_rejected_by_github(workflow: Path) -> None:
    findings = unquoted_colon_names(workflow.read_text(encoding="utf-8"))
    assert findings == [], (
        f"{workflow.name}: quote the value or remove the second colon -- {findings}"
    )


# ------------------------------------------------------------------ 3. the static half of the
# "zero jobs" symptom: a file GitHub refuses has no jobs, and neither has one whose only job was
# deleted by a refactor.


@pytest.mark.parametrize("workflow", WORKFLOW_FILES, ids=lambda path: path.name)
def test_every_workflow_declares_a_non_empty_jobs_mapping(workflow: Path) -> None:
    lines = workflow.read_text(encoding="utf-8").splitlines()
    assert "jobs:" in lines, f"{workflow.name}: no `jobs:` key at column 0"
    below = lines[lines.index("jobs:") + 1 :]
    job_keys = [line for line in below if JOB_KEY_LINE.match(line)]
    assert job_keys, (
        f"{workflow.name}: `jobs:` is empty, so a run of this file can only be zero jobs"
    )
