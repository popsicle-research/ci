# FMG CI/CD Platform

## Introduction

FMG is a minimal CI/CD platform that demonstrates the full lifecycle of running
continuous integration pipelines in response to GitHub events. The system:

- Receives **GitHub push webhooks** and clones the referenced repository.
- Parses **CircleCI-style pipeline definitions** stored at `.circleci/config.yml`.
- Executes jobs inside **ephemeral Docker containers** for isolation.
- Stores pipeline, job, and log metadata in a **SQLite** database.
- Updates **GitHub commit statuses** so authors can track progress inside the
  pull request UI.
- Exposes a **REST API** and **Click-based CLI** for inspecting runs and logs.

This repository packages the code, tests, helper scripts, and documentation
necessary to run the platform locally for learning, demos, or further
development.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Project Setup](#project-setup)
- [Running the Services](#running-the-services)
  - [Webhook & API Server](#webhook--api-server)
  - [Helper Scripts](#helper-scripts)
- [Command Line Interface](#command-line-interface)
  - [Global Options](#global-options)
  - [Pipeline Commands](#pipeline-commands)
  - [Runner Configuration Commands](#runner-configuration-commands)
- [Pipeline Configuration](#pipeline-configuration)
- [Integrating with GitHub](#integrating-with-github)
  - [Expose the Server Publicly](#expose-the-server-publicly)
  - [Create the Webhook](#create-the-webhook)
  - [Commit Status Reporting](#commit-status-reporting)
- [End-to-End Manual Test](#end-to-end-manual-test)
- [Testing & Quality Checks](#testing--quality-checks)
- [Troubleshooting](#troubleshooting)
- [Project Scope & Future Work](#project-scope--future-work)

Refer to [`architecture.md`](architecture.md) for detailed design
considerations and component descriptions.

## Prerequisites

The platform targets macOS (Apple Silicon) but runs anywhere Docker is
available. Install the following on the host that will run the webhook server
and orchestrator:

- **Python 3.11+** – required runtime for the platform.
- **Docker** – Docker Desktop on macOS (including M1/M2) or Docker Engine on
  Linux/Windows. Ensure the daemon is running and the current user can run
  `docker` commands without `sudo`.
- **Git** – used by the webhook handler to clone repositories.
- **Poetry** – recommended for dependency management during development. A
  plain `pip` workflow is also supported (see [Project Setup](#project-setup)).

Optional but recommended tooling:

- [`ngrok`](https://ngrok.com/) or a similar tunnelling service to expose the
  webhook endpoint to GitHub when running locally.
- A GitHub Personal Access Token (PAT) with the `repo:status` scope for commit
  status reporting.

## Project Setup

Clone the repository and install dependencies. The platform ships with a
`pyproject.toml`, so you can choose Poetry **or** a standard `pip` workflow.

**Using Poetry (recommended for contributors):**

```bash
git clone https://github.com/schneiderl/fmg.git
cd fmg
poetry install
```

**Using pip in a virtual environment (for operators running locally):**

```bash
git clone https://github.com/schneiderl/fmg.git
cd fmg
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install .
```

The SQLite database is created on demand under `data/fmg.db`. No additional
setup is required.

## Environment Variables

Configure these variables before launching the server or CLI when applicable:

| Variable | Description |
| --- | --- |
| `GITHUB_TOKEN` | Optional. Personal access token with `repo:status` scope used to update commit statuses. When unset, status reporting is skipped with a warning log. |
| `FMG_WORKSPACE_ROOT` | Optional. Directory where repositories are cloned and executed. Defaults to `workspaces/` inside the project directory. |
| `FMG_SERVER_URL` | Optional. Base URL for the CLI to contact the API. Defaults to `http://localhost:5000`. |
| `PORT` | Optional. Overrides the port used by the Flask server. |
| `WEBHOOK_SECRET` | Not currently enforced. If you extend the webhook to validate signatures, set this to match your GitHub webhook secret. |

## Running the Services

### Webhook & API Server

Launch the webhook receiver (which also registers the REST API routes) using
Flask:

```bash
poetry run flask --app src/fmg/webhook/app.py run --reload
```

By default the server listens on `http://127.0.0.1:5000`. Set the `PORT`
environment variable to override the port and `FMG_WORKSPACE_ROOT` to choose a
custom directory for cloned repositories.

### Helper Scripts

Common workflows are wrapped by scripts in the `scripts/` directory:

- `./scripts/dev_up.sh` – start the development server with sensible defaults.
- `./scripts/test.sh` – run the Pytest suite (used by CI and recommended before
  commits).
- `./scripts/format.sh` – apply Ruff formatting and linting.

Each script assumes a Poetry-managed virtual environment.

## Command Line Interface

The `fmg` CLI interacts with the REST API. It is installed as part of the
project (entry point configured in `pyproject.toml`). When installed via Poetry
use `poetry run fmg`, or if the package is installed in an active virtual
environment the `fmg` command will be available directly. All commands accept
the `--server-url` option or the `FMG_SERVER_URL` environment variable to
target a remote deployment.

### Global Options

```bash
fmg --server-url http://ci.example.com:5000 <command>
```

If omitted, the CLI defaults to `http://localhost:5000`.

### Pipeline Commands

- `fmg list` – display the most recent pipeline runs:

  ```text
  #5 [octo/repo @ a1b2c3d on main] status: SUCCESS (started 2024-06-01T12:00:00Z finished 2024-06-01T12:03:40Z)
  #4 [octo/repo @ d4e5f6g on feature/login] status: FAILURE (started 2024-05-31T18:21:10Z)
  ```

- `fmg logs <pipeline_id> [job_name]` – fetch logs for a pipeline job.
  - If the pipeline has exactly one job the CLI prints its log automatically.
  - When multiple jobs exist the CLI prompts for a specific job name.

  Example:

  ```bash
  fmg logs 5 build
  --- Log for pipeline 5, job "build" ---
  Installing dependencies...
  Running pytest...
  ```

### Runner Configuration Commands

Manage the `runners` table via the API:

- `fmg configure add-runner <host>` – register a runner host. The API currently
  stores metadata without provisioning remote agents.
- `fmg configure list-runners` – list configured runners and their active state.

Example output:

```text
1: localhost [active]
2: 10.0.0.5 [inactive]
```

## Pipeline Configuration

Pipelines follow a CircleCI-style YAML schema placed at `.circleci/config.yml`
in the target repository. A minimal example:

```yaml
version: 2.1
jobs:
  build:
    docker:
      - image: python:3.9
    steps:
      - checkout
      - run: echo "Hello from CI"
workflows:
  version: 2
  build_and_test:
    jobs:
      - build
```

The orchestrator runs jobs sequentially, executing each step inside a Docker
container derived from the first image listed in the job definition.

## Architecture Summary

At a glance, the platform connects GitHub, Docker, and the local database as
follows:

```text
[GitHub] --push webhook--> [FMG Webhook/API Server]
    |                              |
    |                              |-- parses config --> [Pipeline Parser]
    |                              |-- enqueues jobs --> [Orchestrator]
    |                                               |
    |                                               v
    |                                    [Docker Runner executes steps]
    |                                               |
    |                               updates SQLite store & GitHub status
    v                                               |
Developer uses CLI --calls--> [REST API] ---------->|
```

See [`architecture.md`](architecture.md) for an in-depth breakdown of each
component and the storage model.

## Integrating with GitHub

### Expose the Server Publicly

GitHub webhooks require a publicly accessible URL. When running locally use
`ngrok`:

```bash
ngrok http 5000
```

Take note of the generated HTTPS URL (e.g. `https://abcd1234.ngrok.io`). Keep
the tunnel running while GitHub delivers events.

### Create the Webhook

1. Open the target GitHub repository.
2. Navigate to **Settings → Webhooks → Add webhook**.
3. Set the **Payload URL** to `<public-url>/webhook` (e.g.
   `https://abcd1234.ngrok.io/webhook`).
4. Choose **Content type** `application/json`.
5. Select **Just the push event**.
6. (Optional) Configure a secret. Secret validation is not yet implemented, so
   leave empty unless you extend the server accordingly.
7. Save the webhook.

### Commit Status Reporting

Export a GitHub token before launching the server to enable commit status
updates:

```bash
export GITHUB_TOKEN=<personal-access-token-with-repo-status>
```

The webhook handler posts `pending`, `success`, or `failure` statuses to the
originating commit. If the token is missing the platform logs a warning and
continues without updating GitHub.

## End-to-End Manual Test

1. **Prepare a sample repository** with the pipeline configuration shown above.
2. **Start the FMG server** locally (`poetry run flask --app src/fmg/webhook/app.py run --reload`).
3. **Expose the server** through `ngrok http 5000` and configure the webhook as
   described earlier.
4. **Push a commit** to the repository’s default branch.
5. Observe the server logs for webhook processing, cloning, and job execution.
6. Use the CLI to inspect results:

   ```bash
   fmg list
   fmg logs <pipeline-id>
   ```

   Replace `<pipeline-id>` with the numeric identifier printed by `fmg list`.

7. Confirm GitHub displays the commit status transitioning from `pending` to
   `success` (or `failure` on errors).

## Testing & Quality Checks

Run the automated test suite and lint/format checks before committing changes:

```bash
poetry run pytest
poetry run ruff check
poetry run ruff format
```

Helper scripts (`./scripts/test.sh`, `./scripts/format.sh`) wrap the above
commands for convenience.

## Troubleshooting

- **Webhook not received:** ensure the Flask server is reachable from the
  public URL, the `ngrok` tunnel is active, and firewall rules allow incoming
  traffic.
- **Docker build failures:** verify the image exists and supports the host
  architecture (use `arm64`-compatible images on Apple Silicon).
- **Repository clone failures:** the webhook payload must reference a public
  repository or one accessible using credentials available in the environment
  (`GITHUB_TOKEN` or SSH keys).
- **Commit status missing:** confirm `GITHUB_TOKEN` is set and has the
  `repo:status` scope.
- **CLI connection errors:** override the server URL with
  `fmg --server-url http://host:port ...` or set `FMG_SERVER_URL` when running
  the CLI on another machine.

## Project Scope & Future Work

This codebase is a proof-of-concept designed for learning and experimentation.
Potential extensions include remote runner agents, job parallelism, caching,
secrets management, artifact storage, and a web-based dashboard. Contributions
can build upon the documented architecture to evolve the platform.
