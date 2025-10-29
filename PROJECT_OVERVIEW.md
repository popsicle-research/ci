# CI/CD Platform — Contextual Overview

## 🎯 Project Goals

The goal of this project is to **build a minimal, self-contained CI/CD platform** as a **proof of concept**, demonstrating how modern CI/CD systems operate end-to-end.
The platform should:

* Integrate with **GitHub** as its version control system.
* React to **GitHub Webhook events** (pushes, PRs, etc.).
* **Orchestrate pipelines** defined in a YAML configuration file (CircleCI-style syntax).
* Execute **build jobs inside Docker containers** (on a local macOS M1 machine).
* Report pipeline statuses back to **GitHub commit statuses**.
* Provide both a **REST API** and a **CLI tool** to observe and interact with pipelines.
* Store all pipeline and job data in a **SQLite database** for simplicity and persistence.
* Include **step-by-step documentation** and **tests** for all core components.

This system is **not** intended to replace production-grade CI systems, but rather to serve as:

* a teaching / experimentation platform for CI/CD orchestration concepts, or
* a base for future extensions (e.g., caching, remote runners, parallelism, etc.).

---

## 🏗️ High-Level Architecture

The CI/CD platform consists of five main components:

1. **Webhook Server (Event Entry Point)**
   Receives GitHub webhook events (primarily “push” events).
   It creates a new pipeline in response to each event and triggers execution.

2. **Orchestrator**
   Responsible for coordinating job execution.
   Reads the parsed pipeline configuration, runs jobs in order (respecting dependencies), updates the database, and reports results.

3. **Runner**
   Executes each job’s steps inside Docker containers.
   Mounts the cloned repository into the container and runs all steps sequentially within the same environment.

4. **Database (SQLite)**
   Stores persistent records of pipelines, jobs, runners, and logs.
   Used by the API and CLI to query past and active pipeline data.

5. **Interfaces**

   * **REST API:** Exposes pipeline/job information and logs to clients.
   * **CLI Tool:** Command-line interface to view pipelines, fetch logs, and configure runners.
   * **GitHub Commit Status Integration:** Updates commit statuses to pending/success/failure.

---

## ⚙️ How It Works (System Flow)

```text
      ┌────────────────────────────┐
      │        GitHub Repo         │
      └────────────┬───────────────┘
                   │ Push Event (Webhook)
                   ▼
      ┌────────────────────────────┐
      │      Webhook Receiver      │
      │ (Flask HTTP POST /webhook) │
      └────────────┬───────────────┘
                   │
                   │ Parses payload → Clones repo
                   ▼
      ┌────────────────────────────┐
      │   YAML Config Parser       │
      │ (.popsicle/*.yml)          │
      └────────────┬───────────────┘
                   │
                   │ Creates pipeline in SQLite
                   ▼
      ┌────────────────────────────┐
      │       Orchestrator         │
      │  (spawns thread per run)   │
      └────────────┬───────────────┘
                   │
                   │ Executes jobs sequentially
                   ▼
      ┌────────────────────────────┐
      │          Runner            │
      │ (Docker container executor)│
      └────────────┬───────────────┘
                   │
                   │ Captures logs → Updates DB
                   ▼
      ┌────────────────────────────┐
      │       SQLite Database      │
      │ pipelines / jobs / logs    │
      └────────────┬───────────────┘
                   │
                   ├──> REST API (status, logs)
                   ├──> CLI (list pipelines, logs)
                   └──> GitHub Status API (commit status)
```

---

## 🧱 Core Technologies

| Component     | Technology                | Purpose                                   |
| ------------- | ------------------------- | ----------------------------------------- |
| Language      | **Python 3**              | Core platform implementation              |
| Web Framework | **Flask**                 | Handles webhooks and REST API             |
| Database      | **SQLite**                | Lightweight persistence                   |
| Containers    | **Docker**                | Job isolation and environment consistency |
| Config Syntax | **YAML (CircleCI-style)** | Defines jobs, steps, and workflows        |
| CLI           | **Click / Requests**      | Developer interaction                     |
| Tests         | **Pytest**                | Unit and integration tests                |

---

## 📜 YAML Configuration Example

Example `.popsicle/ci.yml` workflow file (additional files like `.popsicle/lint.yml` are parsed the same way):

```yaml
version: 2.1
jobs:
  build:
    docker:
      - image: python:3.9
    steps:
      - checkout
      - run: pip install -r requirements.txt
      - run: pytest

workflows:
  version: 2
  build_and_test:
    jobs:
      - build
```

The orchestrator parses this file, detects the single `build` job, and runs it inside a Docker container.
The job’s `steps` translate directly into shell commands executed sequentially within that container.

---

## 🔄 Lifecycle of a Pipeline

1. **Webhook Triggered:**
   GitHub sends a POST request to `/webhook` when code is pushed.

2. **Pipeline Created:**
   The service clones the repository and parses every `.popsicle/*.yml` workflow file.

3. **Orchestrator Starts:**
   Creates a pipeline entry in the database and begins executing jobs.

4. **Job Execution:**
   Each job runs inside a Docker container using its specified image.
   Logs and exit codes are captured.

5. **Status Reporting:**
   The platform updates GitHub’s commit status:

   * `pending` when the pipeline starts
   * `success` or `failure` after completion

6. **Observation:**

   * The REST API exposes endpoints for pipelines and logs.
   * The CLI can query these endpoints for user-friendly monitoring.

---

## 🧩 Data Model Overview

| Table       | Description                             | Key Fields                                                   |
| ----------- | --------------------------------------- | ------------------------------------------------------------ |
| `pipelines` | Each webhook-triggered pipeline run     | id, repo, commit_sha, branch, status, start_time, end_time   |
| `jobs`      | Each job executed in a pipeline         | id, pipeline_id, job_name, status, start_time, end_time, log |
| `runners`   | Configured runner machines (future use) | id, host                                                     |

Relationships:

* One pipeline → many jobs
* Jobs reference the pipeline via `pipeline_id`

---

## 🧠 Design Principles

* **Lightweight:** No external dependencies beyond Docker and Python stdlib + minimal libraries.
* **Transparent:** Every major action (clone, parse, run, update) is logged and visible.
* **Extensible:** The architecture allows adding remote runners, caching, parallel jobs, or advanced workflow features later.
* **Isolated:** Jobs run in containers to prevent side effects on the host system.
* **Educational:** The design prioritizes clarity over performance or scaling.

---

## 🧩 Future Extensions

This POC intentionally excludes advanced CI/CD features but can evolve to include:

* Parallel job execution / DAG orchestration
* Remote runner registration and dispatch
* Build caching (e.g., via shared volumes or Filestore)
* Secrets management for credentials
* Artifact storage and retrieval
* Web UI for visualization

---

## 🧾 Expected Deliverables

By the end of implementation, the system will include:

* A **running Flask service** with `/webhook` and REST API endpoints
* A **CLI tool** (`popsicle`) for interacting with pipelines
* A **SQLite database** storing pipeline data
* A **fully documented architecture (`architecture.md`)**
* **Unit and integration tests** for all modules
* **Example repositories** demonstrating end-to-end usage

---

## 🧭 End Goal

The end goal is a **fully operational mini CI/CD platform** capable of:

✅ Receiving webhooks from GitHub
✅ Cloning repositories and parsing CircleCI-style configs
✅ Running pipelines locally in Docker containers
✅ Reporting statuses back to GitHub commits
✅ Exposing results through an API and CLI
✅ Being simple enough for an LLM to extend autonomously

---

Would you like me to also generate an **architecture diagram in Mermaid syntax** for inclusion in this file (for `architecture.md` or Confluence visualization)?
