VENV := .venv
PY := $(VENV)/bin/python

.PHONY: install fmt lint typecheck test check serve eval-retrieval eval-generation

install:
	@if command -v uv >/dev/null 2>&1; then \
		uv venv $(VENV) --python 3.12; \
		VIRTUAL_ENV=$(CURDIR)/$(VENV) uv pip install -e ".[dev]"; \
	else \
		python3 -m venv $(VENV); \
		$(VENV)/bin/pip install --upgrade pip; \
		$(VENV)/bin/pip install -e ".[dev]"; \
	fi

fmt:
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

lint:
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/ruff check .

typecheck:
	$(VENV)/bin/mypy

test:
	$(VENV)/bin/pytest -q

check: lint typecheck test

serve:
	$(VENV)/bin/uvicorn lumen.main:app --reload

eval-retrieval:
	$(PY) -m eval.retrieval_eval --golden eval/golden.jsonl --k 5 --min-recall 0.80 --min-mrr 0.70 --report eval/report.md

eval-generation:
	$(PY) -m eval.generation_eval
