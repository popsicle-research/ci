# Architecture Overview

## Components

- **Webhook Service** (`src/popsicle/webhook/`): Flask-based HTTP server that will receive GitHub events and expose additional APIs.
- **Orchestrator** (`src/popsicle/orchestrator/`): Coordinates pipeline execution, ensuring jobs run in the correct order and tracking their progress.
- **Runner** (`src/popsicle/runner/`): Executes job steps inside ephemeral Docker containers to provide isolation from the host.
- **Pipelines** (`src/popsicle/pipelines/`): Logic for parsing CI configuration files and mapping them to executable pipelines.
- **Common Utilities** (`src/popsicle/common/`): Shared helpers such as configuration loading, logging, path management, and
  presentation-friendly formatting helpers in `formatting.py`.
- **CLI** (`src/popsicle/cli/`): Command-line interface for interacting with the platform (listing pipelines, inspecting logs, etc.).
- **API Layer** (`src/popsicle/api/`): Flask routes registered on the webhook app that expose
  read-only REST endpoints for pipelines, jobs, logs, and runner metadata.
- **Web UI** (`src/popsicle/webui/`): Flask blueprint that renders Tailwind-styled Jinja templates for browsing projects,
  pipelines, and job logs without going through the JSON API.

## High-Level Flow

1. **Webhook Trigger**: GitHub sends a webhook to the Flask service when changes occur in a repository.
2. **Pipeline Discovery**: The service clones the repository and reads a CircleCI-style configuration file using the pipelines module.
3. **Orchestration**: Parsed pipelines are passed to the orchestrator, which schedules jobs sequentially (initial implementation) and records their state.
4. **Execution**: The runner executes job steps, capturing logs and exit codes. Docker containers provide clean execution environments.
5. **Persistence**: Pipeline and job metadata are stored in SQLite for durability and for serving API/CLI requests.
6. **Observation**: Users query the API, Web UI, or CLI to inspect pipeline progress and logs. GitHub commit statuses are
   updated to reflect pipeline outcomes.

## End-to-End Sequence Flow

```text
┌─────────────┐   push webhook    ┌────────────────────────────┐
│   GitHub    │ ────────────────▶ │  Flask Webhook Controller  │
└─────────────┘                  └──────────────┬──────────────┘
                                               │
                                      clones repository
                                               │
                                               ▼
                                   ┌──────────────────────┐
                                   │ Pipeline Parser      │
                                   │ (.popsicle/ci.yml│
                                   └─────────┬────────────┘
                                             │
                                  writes pipeline/jobs rows
                                             │
                                             ▼
                                   ┌──────────────────────┐
                                   │ Pipeline Orchestrator│
                                   └─────────┬────────────┘
                                             │
                                     docker run per job
                                             │
                                             ▼
                                   ┌──────────────────────┐
                                   │ Docker Runner        │
                                   └─────────┬────────────┘
                                             │
                                 updates status/log + GitHub
                                             │
                                             ▼
                                  ┌────────────────────────┐
                                  │ SQLite Persistence     │
                                  └─────────┬──────────────┘
                                            │
                                            ▼
          ┌────────────────────────────┐          ┌────────────────────────────┐
          │ REST API (pipelines/logs) │ ◀──────── │ CLI / External Clients     │
          └───────────────┬────────────┘          └────────────────────────────┘
                          │
                          ▼
                 ┌────────────────────┐
                 │ Server-rendered UI │
                 │ (/ui/* routes)     │
                 └────────────────────┘
```

This flow highlights the asynchronous nature of the system: the webhook thread
prepares the workspace and persists initial state, then dispatches orchestration
to a background thread while HTTP responses return immediately.

## REST API

- **`GET /pipelines`**: Returns the most recent pipeline runs ordered by start time
  (20 by default, optionally overridable via the `limit` query parameter). Each
  entry includes repository metadata, commit SHA, branch, status, and
  timestamps.
- **`GET /pipelines/<id>`**: Provides the details for a specific pipeline
  including its metadata and the list of associated jobs with their statuses and
  timing information. The job log content is intentionally omitted to keep the
  payload lightweight.
- **`GET /pipelines/<pipeline_id>/jobs/<job_id>/log`**: Streams the captured log
  output for a job as `text/plain`. The handler verifies that the job belongs to
  the specified pipeline and returns `404` for mismatches.
- **`GET /runners`**: Lists registered runners from the SQLite store so that
  operators can confirm which executors are available to the orchestrator.
- **`POST /runners`**: Adds a runner record with a provided host/IP. The
  endpoint validates input and returns the created runner representation so
  clients (notably the CLI) can confirm identifiers.

All endpoints are unauthenticated in this proof-of-concept deployment and are
designed for consumption by internal tools such as the CLI.

## CLI Tool

- **Framework**: Implemented with Click for structured commands and help text.
- **Server Targeting**: Reads the API base URL from the `--server-url` option or
  the `popsicle_SERVER_URL` environment variable (defaults to
  `http://localhost:5000`).
- **Pipeline Monitoring**: `popsicle list` renders recent pipelines using the
  `/pipelines` endpoint, while `popsicle logs <pipeline_id> [job_name]` fetches job
  metadata then downloads log output from `/pipelines/<pipeline_id>/jobs/<id>/log`.
- **Runner Configuration**: Subcommands under `popsicle configure` call `/runners`
  endpoints to register hosts (`add-runner`) and inspect current entries
  (`list-runners`).
- **Error Handling**: API responses are validated and surfaced to the user via
  `click.ClickException` messages to keep the CLI output concise and
  script-friendly.

## Web UI

- **Navigation**:
  - **Projects** (`/ui/projects`): Lists repositories that have triggered pipelines, sortable by recent activity or name.
  - **Project Pipelines** (`/ui/projects/<path:repo>`): Filterable table of a repository's pipelines with branch/status filters
    and pagination controls.
  - **Pipeline Details** (`/ui/pipelines/<id>`): Presents pipeline metadata with per-job log previews, copy-to-clipboard buttons,
    and download links for entire logs.
- **Data Access**: The blueprint reads directly from `SQLiteStore`, sharing the persistence helpers with the REST API instead of
  issuing HTTP calls to itself.
- **Safety**: HTML auto-escaping is preserved. When logs exceed roughly 2MB, only the first 2000 lines render in the page along
  with a message directing operators to the download endpoint.
- **Styling**: Tailwind CSS is loaded from the CDN inside `base.html`, providing a consistent layout without a dedicated build
  pipeline. Shared partials cover flash messages and empty states.
- **Access Control**: The UI is delivered without authentication in this proof of concept and should be exposed only to trusted
  internal users, matching the assumptions of the REST API.

## Orchestrator & Runner Execution Model

- **Sequential Scheduling**: `PipelineOrchestrator` (`src/popsicle/orchestrator/`) receives the parsed `PipelineConfig`, selects the dependency-respecting job order emitted by the parser, and updates the SQLite store as each job transitions through `pending → running → success/failure`. Jobs execute on dedicated background threads spawned by the webhook handler, allowing multiple pipelines to progress concurrently without blocking HTTP requests.
- **Fail-Fast Semantics**: If a job fails or the runner raises an exception, the orchestrator records the failure, marks remaining queued jobs as `skipped`, and finalises the pipeline with a `failure` status. Successful pipelines auto-populate completion timestamps when status becomes `success`.
- **Docker Runner**: The orchestrator delegates to `DockerRunner` (`src/popsicle/runner/`) which converts each job into a single `docker run` invocation. The runner mounts the cloned repository into `/workspace` inside the container, concatenates each `run` step into a `sh -c` command guarded with `set -eo pipefail`, and captures combined stdout/stderr for persistence. `checkout` steps remain a no-op because the workspace is already present on disk. Non-zero exit codes propagate failure upstream and the entire container is removed after execution so no stopped containers accumulate.
- **Workspace Cleanup**: After a pipeline finishes—successfully or due to failure—the orchestrator removes the temporary workspace directory to prevent disk growth. Cleanup failures are logged as warnings without interrupting pipeline status updates.
- **Thread-Safe Persistence**: Each runner update performs its own SQLite transaction, so concurrent pipelines simply serialize writes through the database file locks. This keeps the design simple without explicit locks in Python.

### Error Handling & Status Propagation

- **Preparation Failures**: Clone or configuration parsing errors are trapped in
  the webhook controller. Pipelines are marked `failure`, jobs are updated with
  context, and the GitHub status reporter posts a failure message when a token
  is available.
- **Docker Execution Errors**: Non-zero exit codes or unexpected exceptions in
  `DockerRunner` mark the corresponding job and pipeline as `failure`. Logs are
  still persisted so operators can inspect the output via CLI or API.
- **Cleanup Issues**: Workspace removal or log persistence issues emit warning
  logs but do not block subsequent webhook processing.

## Pipeline Configuration Parsing

- **Configuration Location**: Repositories declare pipelines in `.popsicle/ci.yml`. When a webhook event is processed, the pipelines module reads this file from the cloned repository workspace.
- **Supported Structure**: The YAML file must provide a `jobs` mapping where each job defines at least one Docker image (using the first image as the execution environment) and a `steps` list. Supported steps include `checkout` (a no-op placeholder inside the container) and `run` commands specified either as strings or with a `command` field.
- **Workflows**: A `workflows` section may be included to describe job ordering using a CircleCI-style `requires` list. The parser performs a topological sort to derive the sequential execution order and validate dependencies.
- **Validation & Errors**: Malformed configurations (missing files, unsupported step types, or unresolved dependencies) raise `PipelineConfigError`, allowing the webhook handler/orchestrator to surface configuration issues as pipeline failures.
- **Data Model**: Parsed configurations are returned as `PipelineConfig` objects containing `JobSpec` and `StepSpec` records. These structures capture Docker images, step definitions, and dependency graphs for the orchestrator.

## Operational Considerations

- **Environment Variables**: Runtime behaviour can be adjusted via
  `popsicle_WORKSPACE_ROOT` (workspace location), `popsicle_SERVER_URL` (CLI default),
  `PORT` (Flask server port), and `GITHUB_TOKEN` (commit status updates). A
  `WEBHOOK_SECRET` can be introduced when GitHub signature verification is
  implemented.
- **Helper Scripts**: `scripts/dev_up.sh`, `scripts/test.sh`, and
  `scripts/format.sh` provide reproducible entrypoints for running the server,
  executing tests, and applying formatting/linting respectively.
- **Testing Strategy**: Unit tests cover the orchestrator, parser, storage
  helpers, and CLI. Integration tests simulate webhook-to-runner flows. The test
  suite runs via `poetry run pytest` and targets ≥85% coverage across
  `src/popsicle/`.

## Persistence Layer

- **SQLite Store**: A lightweight SQLite database (`data/popsicle.db` by default) holds runtime state. The schema includes:
  - `pipelines`: Tracks pipeline executions with repository, commit SHA, branch, status, and timestamps. Status transitions are recorded as pipelines advance.
  - `jobs`: Stores job executions linked to their parent pipeline (foreign key with cascading delete). Each job records its status timeline and aggregated log output.
  - `runners`: Configures known runner hosts along with an `active` flag, preparing for remote execution capabilities.
- **Access API**: The `SQLiteStore` helper (`src/popsicle/storage/sqlite.py`) lazily initializes the schema and exposes CRUD helpers (`create_pipeline`, `create_job`, `update_*`, `set_job_log`, `list_runners`, etc.). Timestamps default to UTC ISO-8601 strings to simplify ordering and external presentation. Additional aggregate helpers surface project summaries, distinct branches, and paginated pipeline listings for the Web UI.
- **Usage**: Downstream components obtain pipeline/job IDs immediately after webhook processing, then update statuses and logs as orchestration progresses. Query helpers (e.g., `get_recent_pipelines`, `get_jobs_for_pipeline`, `get_project_summaries`) power the REST API and UI. Supporting indexes on repository and branch columns keep dashboard navigation responsive even with larger histories.

## Technology Selections

- **Language**: Python 3.11+
- **Web Framework**: Flask
- **Configuration Parsing**: PyYAML
- **Container Control**: Docker Engine via the Docker CLI (`docker run` invocations)
- **Database**: SQLite using the standard `sqlite3` module
- **CLI Framework**: Click
- **Testing**: Pytest with coverage goals ≥ 85% across runtime packages

## Development Tooling

- Dependency management via Poetry (`poetry install`)
- Formatting and linting with Ruff (`poetry run ruff format`, `poetry run ruff check`)
- Test execution using Pytest (`poetry run pytest`)
- Convenience scripts in `scripts/` wrapping common tasks

## Webhook Handling & GitHub Integration

- **Endpoint**: The Flask app exposes `POST /webhook` which expects GitHub push event payloads. Unsupported events respond with an "ignored" message (HTTP 202) so GitHub does not retry unnecessarily.
- **Payload Processing**:
  - Repository metadata, commit SHA (`after`), and ref are validated from the payload. Missing fields return a 400 error.
  - A pipeline record is created immediately in SQLite, capturing repository, commit, branch, and timestamps.
  - The repository is cloned into a deterministic workspace directory (`workspaces/pipeline-<id>` by default; configurable through `popsicle_WORKSPACE_ROOT`). Private repositories can set `popsicle_GITHUB_TOKEN` to allow authenticated clones.
  - After checkout, `.popsicle/ci.yml` is parsed via the pipelines module. Validation errors mark the pipeline as failed while still returning HTTP 200 to prevent webhook retries. Failures include cloning errors, missing configs, or unsupported syntax.
  - Job rows are pre-created for every job declared in the configuration so downstream consumers can display queued work.
- **Asynchronous Execution**: Once preparation is successful, the orchestrator is invoked on a background thread. The webhook response immediately returns `{ "status": "queued", "pipeline_id": <id> }`, allowing GitHub to finish the request quickly while execution continues.
- **Commit Status Reporting**:
  - `GitHubStatusReporter` (`src/popsicle/github/status.py`) posts commit statuses to `https://api.github.com/repos/<owner>/<repo>/statuses/<sha>` using the REST v3 API.
  - When a pipeline is enqueued the webhook posts a `pending` status so the originating commit immediately reflects that work is in progress. Clone/configuration failures short-circuit with a `failure` status describing the issue.
  - The orchestrator emits `pending`, `success`, or `failure` updates as the pipeline lifecycle progresses. Success messages include the job count; failure messages identify the job that failed when available.
  - Status updates require a Personal Access Token exported as `GITHUB_TOKEN` with `repo:status` scope. If the token is missing or an API call fails the pipeline still runs, but the reporter logs a warning and skips the update.
  - A configurable `context` (`ci/popsicle` by default) keeps our statuses grouped on GitHub. Future deployments can provide a `target_url` builder to deep-link to pipeline details.
- **Workspace Lifecycle**: Each pipeline owns its own directory under the workspace root. Once orchestration completes, the runner signals finish and the orchestrator deletes the workspace directory to control disk usage.
- **Security & Logging**: HMAC validation is not yet enforced; document that a GitHub webhook secret should be configured before production use. Logs include repository and pipeline identifiers to aid troubleshooting without leaking credentials.

## Future Considerations

- Support for job dependencies and parallel execution
- Remote runner registration and dispatch
- Additional configuration options (environment variables, caching, artifacts)
- Web-based dashboard for visualization
