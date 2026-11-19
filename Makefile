.PHONY: setup db-up db-down db-reset db-check migrate seed introspect-check test lint types eval eval-gate report record ci serve demo clean

UV ?= uv
CONTAINER_PY = $(UV) run python -m

setup:
	$(UV) sync --extra dev

db-up:
	docker compose up -d --wait

db-down:
	docker compose down

db-reset:
	docker compose down -v
	docker compose up -d --wait

db-check:
	$(CONTAINER_PY) opsquery.db.check

migrate:
	$(CONTAINER_PY) opsquery.db.migrate

seed: migrate
	$(CONTAINER_PY) opsquery.seed.generator

introspect-check:
	$(CONTAINER_PY) opsquery.db.introspect --check

test:
	$(UV) run pytest --cov=opsquery --cov-report=term-missing

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

types:
	$(UV) run mypy

eval:
	$(CONTAINER_PY) opsquery.eval.runner --write-report

eval-gate:
	$(CONTAINER_PY) opsquery.eval.runner --gate

report:
	$(CONTAINER_PY) opsquery.eval.report

record:
	$(CONTAINER_PY) opsquery.llm.record

ci: lint types test

demo:
	$(CONTAINER_PY) opsquery.demo

serve:
	$(CONTAINER_PY) opsquery.demo --serve

clean:
	$(UV) run python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in ('.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov')]; [p.unlink(missing_ok=True) for p in ('.coverage', 'coverage.xml')]"
