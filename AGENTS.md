# Repository Guidelines

## Project Structure & Module Organization
Use `PROJECT_OVERVIEW.md` as the source of truth for architecture boundaries. Runtime code lives in `src/popsicle/` with subpackages for webhook intake, orchestrator, runner, API, and CLI. Configuration parsing belongs in `src/popsicle/pipelines/`, and shared helpers in `src/popsicle/common/`. Mirror the tree inside `tests/unit/` and `tests/integration/`, keeping fixtures in `tests/fixtures/`. Place helper scripts in `scripts/` and infrastructure manifests under `infra/`.

## Issue Delivery Workflow
Work through the GitHub issues sequentially (#1 → #10). Each issue defines scope and constraints—complete the body exactly before advancing. Reference earlier issues for context, log blockers in the tracker, and close only when code, tests, docs, and checklists are done.

## Build, Test, and Development Commands
`poetry install` provisions dependencies. `poetry run flask --app src/popsicle/webhook/app.py run --reload` launches the webhook/API service. `poetry run python -m popsicle.cli pipelines list` exercises the CLI. `poetry run pytest` executes the suite; add `--cov=popsicle --cov-report=term-missing` before publishing. Provide wrappers like `scripts/dev_up.sh`, `scripts/test.sh`, and `scripts/format.sh` for reproducible workflows.

## Coding Style & Naming Conventions
Target Python 3.11. Format with `poetry run ruff format` and lint via `poetry run ruff check`. Use `snake_case` for modules/functions, PascalCase for classes, UPPER_CASE for constants, and kebab-case for CLI commands. Type public APIs, keep functions focused (~50 lines), and document integration boundaries. YAML configs follow CircleCI 2.1 semantics.

## Testing Guidelines
Each change ships with unit coverage; add integration cases for webhook → orchestrator → runner flows. Name test modules `test_<module>.py`, functions `test_<behavior>`, and mark slow suites with `@pytest.mark.integration` so the default Pytest config skips them unless `PYTEST_ADDOPTS='-m "integration"'` is set. Hold ≥85% coverage across `src/popsicle/` and reuse fixtures in `tests/fixtures/` for deterministic payloads and logs.

## Documentation & Commit Requirements
Reflect implementations in `architecture.md` (or the doc referenced in `PROJECT_OVERVIEW.md`) before closing an issue. Document helper scripts, environment variables, and new endpoints. Commit directly to `main` using Conventional Commits (`feat: orchestrator handles fan-out`), reference the issue number, and note commands run plus any post-merge steps.
