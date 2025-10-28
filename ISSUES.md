# CI/CD Platform Development Plan

Below is a breakdown of the development tasks for a minimal CI/CD platform. Each task is defined as a sequential issue with a clear goal, implementation guidance, and acceptance criteria. The issues are written for an AI agent implementer, with architecture considerations and definitions of done (including updating the `architecture.md` as changes are introduced).

---
# Issue 11: Web UI Foundation & Project List Page

### Goal

Introduce a minimalist web UI (Flask + Jinja2 + Tailwind) and implement the **Project List** page. This page lists all repositories that have triggered pipelines (derived from the `pipelines` table), acting as the dashboard entry point.

### Context

The platform already exposes REST endpoints and a CLI. A small server-rendered UI will make it easier to browse projects and drill into pipelines. We will add a UI blueprint, base templates, Tailwind via CDN, and a first page that lists projects with basic metadata (last run time, total runs).

### Implementation Details

**1) Package layout & blueprint**

* Create a new package: `src/fmg/webui/`.

  * `__init__.py`
  * `routes.py` (Flask Blueprint)
  * `templates/` (Jinja2 templates)
  * `static/` (optional: favicon, small logo if desired)
* Register a Flask **Blueprint** in `src/fmg/webui/routes.py`:

  * `ui_bp = Blueprint("ui", __name__, url_prefix="/ui")`
  * Import and register in the main Flask app (webhook service factory) after API routes:

    ```python
    # src/fmg/webhook/app.py (or wherever the app is created)
    from fmg.webui.routes import ui_bp
    app.register_blueprint(ui_bp)
    ```
* Keep UI routes separate from the REST API (`src/fmg/api/`) to avoid mixing JSON and HTML handler concerns.

**2) Base template & Tailwind**

* Create `templates/base.html`:

  * Include Tailwind CDN in `<head>` (no build step):

    ```html
    <script src="https://cdn.tailwindcss.com"></script>
    ```
  * Basic, minimalist layout:

    * Top nav bar with project title (“FMG CI Dashboard”).
    * `<main class="max-w-6xl mx-auto px-4 py-6">` container for page contents.
  * Define Jinja blocks: `{% block head %}{% endblock %}`, `{% block content %}{% endblock %}`.
* Create small shared partials (optional, but useful later):

  * `templates/_flash.html` for flash messages.
  * `templates/_empty_state.html` to reuse an empty-list message block.

**3) Project list route**

* Add route: `GET /ui/projects` (and **optionally** redirect `/ui` → `/ui/projects`).
* Query **SQLite** through existing storage helpers (avoid calling REST API from the server):

  * Get **distinct repos** from `pipelines.repo`.
  * For each repo, compute:

    * `total_pipelines` (COUNT)
    * `last_run_at` (MAX(start_time) or `end_time` if preferred)
    * `last_status` (status of the most recent pipeline)
* Sorting:

  * Default order: most recently active project first (by `last_run_at DESC`).
  * Provide a toggle (query param `?sort=name` vs `?sort=recent`).

**4) Project list template**

* Create `templates/projects.html` that extends `base.html`.
* Render a simple grid of **cards** (minimal Tailwind):

  * Each card shows:

    * Repository slug (e.g., `owner/repo`)
    * Last run timestamp (formatted)
    * Last status (colored badge)
    * Total pipelines count
  * Click navigates to **Project Pipelines page**: `/ui/projects/{{ urlsafe_repo }}`

    * Note: Encode “owner/repo” safely for the URL (replace `/` with `__` or use `<path:repo>` converter).
* Accessibility:

  * Ensure links have clear text; use semantic headings; sufficient color contrast on status badges.

**5) Utilities**

* Add a small helper in `src/fmg/common/formatting.py` (or similar) for:

  * ISO timestamp → human-readable (`YYYY-MM-DD HH:MM:SS`).
  * Status → Tailwind color class mapping (e.g., success=green, failure=red, running=amber).

**6) Tests**

* Add a new test module `tests/webui/test_projects.py`:

  * Setup: Seed SQLite with a few pipelines across multiple repos.
  * `GET /ui/projects` returns 200 and includes project names, counts, and last status.
  * Sorting query parameters change order.
  * Empty state: when no pipelines exist, displays an informative message.

**7) Docs**

* Update **`architecture.md`**:

  * Add a **Web UI** component under “Components”:

    * **Web UI** (`src/fmg/webui/`): Jinja2 templates rendered by Flask, styled with Tailwind via CDN. Read-only views over pipelines data.
  * Extend the high-level flow to mention UI read paths (no changes to execution path).

### Definition of Done

* New `webui` blueprint registered under `/ui`.
* Tailwind-styled base template and a **Project List** page at `/ui/projects`, with sorting and an empty state.
* Uses storage helpers to read from SQLite directly (no internal HTTP).
* Unit tests validate rendering and data presence.
* `architecture.md` updated with the new **Web UI** component and request flow note.

---

# Issue 12: Project Pipelines Page (List View with Filters & Pagination)

### Goal

Implement the **Project Pipelines** page that appears after selecting a project. It lists pipelines for the chosen repo with filters (status, branch) and pagination, linking to **Pipeline Details**.

### Context

After landing on the Project List, users need to drill down to see individual pipeline runs for a specific repository. This page should be fast and simple, with server-side filtering/pagination to keep HTML light.

### Implementation Details

**1) Route & URL shape**

* Route: `GET /ui/projects/<path:repo>` where `repo` is `owner/repo` (use `<path:repo>` so slashes are accepted).

  * Alternatively, if you encoded slashes in Issue 11 (e.g., `owner__repo`), decode here.
* Query params:

  * `status=` one of `running, success, failure, pending` (optional).
  * `branch=` show only that branch (optional).
  * `page=` 1-based page number (default 1).
  * `per_page=` default 20, clamp to a reasonable max (e.g., 100).

**2) Data access**

* Add read helpers in storage layer if not present:

  * `list_pipelines_by_repo(repo, status=None, branch=None, limit=20, offset=0)`
    Returns rows with: `id, repo, branch, commit_sha, status, start_time, end_time`.
  * `count_pipelines_by_repo(repo, status=None, branch=None)`
    For pagination.
  * `list_distinct_branches(repo)` to populate branch filter options.
* Indexing: consider adding DB indexes on `(repo)`, `(repo, branch)`, and `(repo, start_time DESC)` to keep queries snappy.

**3) Template: pipelines list**

* Create `templates/pipelines_list.html`:

  * Header showing the repo name and a “Back to Projects” link.
  * Filters row:

    * Status dropdown with a blank “All” option.
    * Branch dropdown populated from `list_distinct_branches(repo)`, with “All”.
    * “Apply” button (submits GET with query params).
  * Table or cards of pipelines:

    * Columns: **ID**, **Status** (colored badge), **Branch**, **Commit (short SHA)**, **Started**, **Ended** (or duration), **Actions** (link to details).
    * Each row links to **Pipeline Details**: `/ui/pipelines/<id>`
  * Pagination controls:

    * Show `« Prev` / `Next »` and current page indicator.
    * Disable buttons when at bounds; preserve filters in links.

**4) UX polish**

* Status badges (Tailwind):

  * success → `bg-green-100 text-green-800`
  * failure → `bg-red-100 text-red-800`
  * running → `bg-amber-100 text-amber-800`
  * pending → `bg-gray-100 text-gray-800`
* Monospace for commit SHA and IDs.
* Graceful empty state when no pipelines match filters.

**5) Tests**

* `tests/webui/test_pipelines_list.py`:

  * Seed DB with pipelines across branches and statuses.
  * Verify filtering by status works; branch filter works; both combined work.
  * Pagination changes items; out-of-range page returns empty state (or redirects to last page).
  * Links to details include correct pipeline IDs.

**6) Docs**

* Update **`architecture.md`** under “Observation” and REST API notes to mention the **server-rendered UI views**:

  * Clarify UI reads from the same persistence layer and does not introduce new API contracts.
  * Add a short subsection **“Web UI Navigation”** with the three pages and routes.

### Definition of Done

* Route `/ui/projects/<path:repo>` implemented with filters and pagination.
* Page renders a Tailwind table/cards with per-pipeline rows and a link to `/ui/pipelines/<id>`.
* Distinct branches populate a branch filter.
* Unit tests for filtering, pagination, and navigation links.
* `architecture.md` updated to include the **Project Pipelines** view and route.

---

# Issue 13: Pipeline Details Page (Metadata + All Job Logs)

### Goal

Implement the **Pipeline Details** page. Show pipeline metadata and **all logs for that pipeline** (grouped by job) with collapsible sections, copy-to-clipboard, and safe rendering. Keep it minimalist and fast for reasonably sized logs.

### Context

Operators need a single page to inspect what happened during a pipeline run. Since our POC stores full job logs in SQLite, we can render them server-side. We must be careful with output escaping and usability for long logs.

### Implementation Details

**1) Route & data loading**

* Route: `GET /ui/pipelines/<int:pipeline_id>`
* Data queries (storage helpers already exist or add if needed):

  * `get_pipeline(pipeline_id)` → `id, repo, branch, commit_sha, status, start_time, end_time`
  * `get_jobs_for_pipeline(pipeline_id)` → list of `id, job_name, status, start_time, end_time, log`
* If pipeline not found: return 404 with a simple error page (Tailwind-styled) that links back to Projects.

**2) Template: pipeline details**

* Create `templates/pipeline_details.html`, extends `base.html`.
* **Header section (metadata)**:

  * Repo (link back to `/ui/projects/<repo>`), branch, commit SHA (short), status badge.
  * Timestamps and derived duration (if both times exist).
  * Optional: a “Copy commit SHA” button.
* **Jobs & logs section**:

  * For each job:

    * Card with job name, status badge, timing, and an expandable **details** (use `<details><summary>…</summary>…</details>` for zero-JS simplicity).
    * Inside the expanded area, render the `log` in a `<pre>`:

      * Use `class="whitespace-pre-wrap font-mono text-sm bg-gray-50 p-4 rounded"` to keep formatting and wrap long lines.
      * **Important:** ensure HTML is **escaped**. In Jinja, print logs using `{{ job.log }}` (autoescape on) or `{{ job.log | e }}` explicitly.
    * Add a small **“Copy log”** button per job that writes text to clipboard:

      * Include a tiny, unobtrusive inline JS snippet or use a simple `onclick` with `navigator.clipboard.writeText(...)`. Keep JS minimal and local to this page.
* **Very large logs**:

  * If a single log exceeds e.g., 2MB, offer a “View first 2000 lines” + “Download full log” link.
  * Implement a lightweight download endpoint:

    * `GET /ui/pipelines/<int:pipeline_id>/jobs/<int:job_id>/download` returning `text/plain` with a `Content-Disposition: attachment`.
    * Reuse storage helper for job log fetch; verify job belongs to pipeline.
  * This avoids pushing massive HTML to the browser while keeping UX usable.

**3) Minimal inline script (optional but tiny)**

* In `pipeline_details.html`, at the bottom:

  ```html
  <script>
    function copyText(id) {
      const el = document.getElementById(id);
      if (!el) return;
      navigator.clipboard.writeText(el.innerText || el.textContent);
    }
  </script>
  ```
* Buttons:

  ```html
  <button class="text-xs px-2 py-1 rounded bg-gray-200 hover:bg-gray-300"
          onclick="copyText('log-{{ job.id }}')">
    Copy log
  </button>
  ```

**4) Performance & safety**

* Ensure logs are HTML-escaped. Never mark logs `|safe`.
* Consider truncating logs in HTML to first N characters with a “Show full” control (using `<details>`).
* Server-side: if logs are extremely large, slice before rendering into template and offer download for full text.

**5) Tests**

* `tests/webui/test_pipeline_details.py`:

  * Render a pipeline with 2+ jobs (one success, one failure), assert metadata and badges.
  * Ensure log content is present and escaped (e.g., a string with `<script>` renders literally).
  * Download endpoint returns `text/plain` and correct content.
  * 404 for unknown pipeline or job mismatch.

**6) Docs**

* Update **`architecture.md`**:

  * Add route **`/ui/pipelines/<id>`** under a Web UI section.
  * Mention **download endpoint** for large logs and the decision to escape logs by default.
  * Update sequence diagram (optional) to show read-only UI path.

### Definition of Done

* Route `/ui/pipelines/<int:pipeline_id>` renders metadata and collapsible per-job logs with copy buttons.
* Large logs are truncated in HTML with an optional download endpoint.
* Logs are safely escaped; no untrusted HTML is injected.
* Unit tests cover metadata, log rendering, escaping, and downloads.
* `architecture.md` updated with the **Pipeline Details** view and safety/performance notes.

---

## Shared Acceptance Notes (Issues 11–13)

* All pages extend a common `base.html` and share Tailwind-first, minimalist styling.
* Navigation:

  * **Projects** → `/ui/projects`
  * **Project Pipelines** → `/ui/projects/<path:repo>`
  * **Pipeline Details** → `/ui/pipelines/<int:pipeline_id>`
* Error pages are simple, consistent, and link back to the previous level (projects or the specific project page).
* No authentication is added in this POC; document that these pages are intended for local/internal use.
* Each issue must update **`architecture.md`** accordingly:

  * Add the **Web UI** component and routes.
  * Clarify that UI pages read directly from the persistence layer via storage helpers (no extra REST calls).
  * Note HTML escaping and log handling strategy.
